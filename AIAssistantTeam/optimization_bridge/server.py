# ruff: noqa: BLE001, S110, TRY004, UP031
"""Local bridge for allowlisted TopOpt topology-optimization templates.

The STE plugin starts this process with its own ``sys.executable``.  The bridge
therefore remains compatible with the Python 3.5/3.8 runtimes used by STE/Cura.
Each template runs as a subprocess and may only write artifacts inside its job
directory.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import hmac
import subprocess
import sys
import threading
import traceback
import uuid
try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # Python 3.5 in older STE/Cura builds
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def default_connection_file():
    """Return a per-user discovery file shared with the STE plugin."""
    root = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return Path(root) / "Stereotech" / "TopOpt" / "connection.json"


def write_connection_file(path, url, token):
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "url": url,
                "port": urlparse(url).port,
                "token": token,
                "pid": os.getpid(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(str(temporary), 0o600)
    except OSError:
        pass
    temporary.replace(path)
    return path


def remove_connection_file(path, token):
    """Do not remove a discovery record written by a newer bridge process."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if hmac.compare_digest(str(current.get("token", "")), token):
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


class BridgeState:
    def __init__(self, root, token):
        self.root = root
        self.templates_root = root / "templates"
        self.jobs_root = root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.jobs = {}
        self.lock = threading.Lock()
        self.active_processes = {}

    def templates(self):
        result = []
        if not self.templates_root.is_dir():
            return result
        for manifest_path in sorted(self.templates_root.glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                template_id = manifest.get("id")
                script = manifest_path.parent / "topopt_script.py"
                missing = [
                    name for name in manifest.get("python_dependencies", [])
                    if importlib.util.find_spec(name) is None
                ]
                manifest["ready"] = bool(template_id and script.is_file() and not missing)
                manifest["reason"] = (
                    "" if manifest["ready"] else
                    ("Missing Python packages: %s" % ", ".join(missing) if missing
                     else "Missing id or topopt_script.py")
                )
                manifest["engine"] = "TopOpt"
                result.append(manifest)
            except Exception as error:
                result.append(
                    {
                        "id": manifest_path.parent.name,
                        "ready": False,
                        "reason": "Invalid manifest: %s" % error,
                    }
                )
        return result

    def template(self, template_id):
        for item in self.templates():
            if item.get("id") == template_id and item.get("ready"):
                return item, self.templates_root / template_id
        raise ValueError("Unknown or unavailable optimization template: %s" % template_id)

    def health(self, deep=False):
        templates_ready = sum(1 for item in self.templates() if item.get("ready"))
        result = {
            "service": "slicer-mcp-optimization-bridge",
            "engine": "TopOpt",
            "solver": "prepared SIMP template",
            "status": "ready" if templates_ready else "unavailable",
            "topopt_available": bool(templates_ready),
            "external_license_required": False,
            "engineering_validation_available": False,
            "templates_ready": templates_ready,
            "deep_check": bool(deep),
        }
        if not templates_ready:
            result["reason"] = "No prepared TopOpt templates are ready"
        return result

    def create_job(self, request):
        if not isinstance(request, dict):
            raise ValueError("Request must be an object")
        template_id = request.get("template_id")
        manifest, template_root = self.template(template_id)
        request = _validate_inputs(request, manifest)
        job_id = "opt-" + uuid.uuid4().hex[:16]
        job_root = self.jobs_root / job_id
        job_root.mkdir()
        request_path = job_root / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        job = {
            "job_id": job_id,
            "status": "queued",
            "template_id": template_id,
            "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "cancel_requested": False,
            "artifacts": {},
            "warnings": [],
        }
        with self.lock:
            self.jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_job, args=(job_id, template_root, request_path, job_root)
        )
        thread.daemon = True
        thread.start()
        return dict(job)

    def _run_job(self, job_id, template_root, request_path, job_root):
        process = None
        try:
            self._update(job_id, status="starting_solver")
            script_path = template_root / "topopt_script.py"
            command = [sys.executable, str(script_path), str(request_path), str(job_root)]
            process = subprocess.Popen(
                command,
                cwd=str(job_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            with self.lock:
                self.active_processes[job_id] = process
            self._update(job_id, status="solving")
            stdout, stderr = process.communicate()
            if process.returncode:
                raise RuntimeError(
                    "TopOpt template failed with code %s: %s"
                    % (process.returncode, (stderr or stdout)[-4000:])
                )
            result_path = job_root / "result.json"
            if not result_path.is_file():
                raise RuntimeError("Template did not create result.json")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            _validate_result(result)
            artifacts = result.get("artifacts") or {}
            for name, raw_path in artifacts.items():
                path = Path(raw_path).resolve()
                if not path.is_file() or job_root.resolve() not in path.parents:
                    raise RuntimeError("Invalid artifact path for %s" % name)
                artifacts[name] = str(path)
            if self.get_job(job_id).get("cancel_requested"):
                self._update(job_id, status="cancelled")
            else:
                self._update(
                    job_id,
                    status="completed",
                    result=result,
                    artifacts=artifacts,
                    solver_output=(stdout or "")[-4000:],
                )
        except Exception as error:
            if self.get_job(job_id).get("cancel_requested"):
                self._update(job_id, status="cancelled")
            else:
                self._update(
                    job_id,
                    status="failed",
                    error=str(error),
                    traceback=traceback.format_exc()[-8000:],
                )
        finally:
            with self.lock:
                self.active_processes.pop(job_id, None)

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            snapshot = dict(self.jobs[job_id])
        (self.jobs_root / job_id / "job.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                path = self.jobs_root / job_id / "job.json"
                if not path.is_file():
                    raise ValueError("Unknown optimization job")
                return json.loads(path.read_text(encoding="utf-8"))
            return dict(self.jobs[job_id])

    def cancel(self, job_id):
        self.get_job(job_id)
        self._update(job_id, cancel_requested=True)
        with self.lock:
            process = self.active_processes.get(job_id)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        self._update(job_id, status="cancelled")
        return self.get_job(job_id)


def _validate_inputs(request, manifest):
    required = manifest.get("required_inputs") or []
    inputs = request.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be an object")
    missing = [name for name in required if name not in inputs]
    if missing:
        raise ValueError("Missing required inputs: %s" % ", ".join(missing))
    material_id = str(inputs.get("material_id") or "").strip()
    if not material_id:
        raise ValueError("material_id is required")
    try:
        load_n = float(inputs.get("load_n"))
    except (TypeError, ValueError):
        raise ValueError("load_n must be a positive number")
    if not math.isfinite(load_n) or load_n <= 0:
        raise ValueError("load_n must be a positive number")
    try:
        target = float(inputs.get("target_mass_percent"))
    except (TypeError, ValueError):
        raise ValueError("target_mass_percent must be a number from 1 to 99")
    if not math.isfinite(target):
        raise ValueError("target_mass_percent must be a number from 1 to 99")
    if 0 < target < 1:
        target *= 100.0
    if target < 1 or target > 99:
        raise ValueError("target_mass_percent must be from 1 to 99")
    request["inputs"] = dict(inputs)
    request["inputs"]["material_id"] = material_id
    request["inputs"]["load_n"] = load_n
    request["inputs"]["target_mass_percent"] = target
    source = request.get("source_model")
    if not isinstance(source, dict) or not source.get("path"):
        raise ValueError("source_model.path is required")
    source_path = Path(source["path"]).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError("Source model does not exist")
    allowed = [str(value).lower() for value in manifest.get("source_extensions", [".step", ".stp"])]
    if source_path.suffix.lower() not in allowed:
        raise ValueError("Source model type is not allowed by this template")
    request["source_model"] = dict(source)
    request["source_model"]["path"] = str(source_path)
    return request


def _validate_result(result):
    if not isinstance(result, dict):
        raise ValueError("result.json must contain an object")
    for name in ("baseline_analysis", "optimization", "validation", "artifacts"):
        if not isinstance(result.get(name), dict):
            raise ValueError("result.json is missing %s" % name)
    if result["validation"].get("status") not in ("passed", "failed", "not_performed"):
        raise ValueError("validation.status must be passed, failed or not_performed")
    if not result["artifacts"].get("optimized_stl"):
        raise ValueError("result.json is missing artifacts.optimized_stl")


class Handler(BaseHTTPRequestHandler):
    server_version = "SlicerTopOptBridge/0.2"

    def _authorized(self):
        provided = self.headers.get("X-Bridge-Token", "")
        valid = hmac.compare_digest(provided, self.server.state.token)
        if not valid:
            print(
                "Rejected bridge token header (received length %d, expected %d)" %
                (len(provided), len(self.server.state.token)),
                flush=True,
            )
        return valid

    def _send(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self):
        if not self._authorized():
            return self._send(403, {"error": "Invalid bridge token"})
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        try:
            if self.command == "GET" and parts == ["health"]:
                deep = parse_qs(parsed.query).get("deep", ["0"])[0] == "1"
                return self._send(200, self.server.state.health(deep))
            if self.command == "GET" and parts == ["templates"]:
                return self._send(200, {"templates": self.server.state.templates()})
            if self.command == "POST" and parts in (["jobs"], ["optimize"]):
                return self._send(202, self.server.state.create_job(self._json_body()))
            if self.command == "GET" and len(parts) == 2 and parts[0] == "jobs":
                return self._send(200, self.server.state.get_job(parts[1]))
            if (
                self.command == "POST"
                and len(parts) == 3
                and parts[0] == "jobs"
                and parts[2] == "cancel"
            ):
                return self._send(200, self.server.state.cancel(parts[1]))
            return self._send(404, {"error": "Not found"})
        except ValueError as error:
            return self._send(400, {"error": str(error)})
        except Exception as error:
            return self._send(500, {"error": str(error)})

    def _json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    do_GET = _route
    do_POST = _route

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--token", required=True)
    parser.add_argument("--connection-file", type=Path, default=default_connection_file())
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The beta bridge only binds to loopback")
    token = args.token.strip()
    if len(token) < 32:
        parser.error("--token must contain at least 32 characters")
    state = BridgeState(args.data_dir.resolve(), token)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = state
    bridge_url = "http://%s:%d" % server.server_address
    connection_file = write_connection_file(args.connection_file, bridge_url, token)
    print("Optimization bridge: %s" % bridge_url)
    print("Automatic STE discovery: %s" % connection_file)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Optimization bridge stopped")
    finally:
        server.server_close()
        remove_connection_file(connection_file, token)


if __name__ == "__main__":
    main()
