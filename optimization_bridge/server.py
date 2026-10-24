# ruff: noqa: BLE001, S110, TRY004, UP031
"""Local Ansys/PyMechanical bridge for prepared topology-optimization templates.

Run with Python 3.12+. This process is intentionally separate from STE Slicer's
embedded Python 3.5 runtime.
"""

import argparse
import hashlib
import importlib.util
import json
import secrets
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class BridgeState:
    def __init__(self, root, token):
        self.root = root
        self.templates_root = root / "templates"
        self.jobs_root = root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.jobs = {}
        self.lock = threading.Lock()
        self.active_mechanical = {}

    def templates(self):
        result = []
        if not self.templates_root.is_dir():
            return result
        for manifest_path in sorted(self.templates_root.glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                template_id = manifest.get("id")
                script = manifest_path.parent / "mechanical_script.py"
                manifest["ready"] = bool(template_id and script.is_file())
                manifest["reason"] = (
                    "" if manifest["ready"] else "Missing id or mechanical_script.py"
                )
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
        try:
            pymechanical = importlib.util.find_spec("ansys.mechanical.core") is not None
        except (ImportError, ModuleNotFoundError):
            pymechanical = False
        result = {
            "service": "slicer-mcp-optimization-bridge",
            "status": "ready" if pymechanical else "unavailable",
            "pymechanical_available": pymechanical,
            "mechanical_found": False,
            "license_available": None,
            "templates_ready": sum(1 for item in self.templates() if item.get("ready")),
            "deep_check": bool(deep),
        }
        if not pymechanical:
            result["reason"] = "Install ansys-mechanical-core in this Python environment"
            return result
        try:
            from ansys.tools.common.path import find_mechanical

            executable, version = find_mechanical()
            result["mechanical_found"] = True
            result["mechanical_version"] = version
            result["mechanical_executable"] = executable
        except Exception as error:
            result["status"] = "unavailable"
            result["reason"] = "Mechanical installation not found: %s" % error
            return result
        if deep:
            try:
                from ansys.mechanical.core import launch_mechanical

                mechanical = launch_mechanical(batch=True)
                result["license_available"] = True
                result["mechanical_product"] = str(mechanical)
                mechanical.exit()
            except Exception as error:
                result["status"] = "unavailable"
                result["license_available"] = False
                result["reason"] = "Mechanical launch or license check failed: %s" % error
                return result
        if not result["templates_ready"]:
            result["status"] = "unavailable"
            result["reason"] = "No prepared optimization templates are ready"
        return result

    def create_job(self, request):
        if not isinstance(request, dict):
            raise ValueError("Request must be an object")
        template_id = request.get("template_id")
        manifest, template_root = self.template(template_id)
        _validate_inputs(request, manifest)
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
        mechanical = None
        try:
            self._update(job_id, status="starting_mechanical")
            from ansys.mechanical.core import launch_mechanical

            mechanical = launch_mechanical(batch=True)
            with self.lock:
                self.active_mechanical[job_id] = mechanical
            self._update(job_id, status="solving")
            script_path = template_root / "mechanical_script.py"
            prefix = "REQUEST_PATH = %s\nOUTPUT_DIR = %s\n" % (
                repr(str(request_path)),
                repr(str(job_root)),
            )
            output = mechanical.run_python_script(prefix + script_path.read_text(encoding="utf-8"))
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
                    mechanical_output=str(output)[-4000:],
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
                self.active_mechanical.pop(job_id, None)
            if mechanical is not None:
                try:
                    mechanical.exit(force=True)
                except Exception:
                    pass

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
            mechanical = self.active_mechanical.get(job_id)
        if mechanical is not None:
            try:
                mechanical.exit(force=True)
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
    source = request.get("source_model")
    if not isinstance(source, dict) or not source.get("path"):
        raise ValueError("source_model.path is required")
    source_path = Path(source["path"]).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError("Source model does not exist")
    allowed = [str(value).lower() for value in manifest.get("source_extensions", [".step", ".stp"])]
    if source_path.suffix.lower() not in allowed:
        raise ValueError("Source model type is not allowed by this template")


def _validate_result(result):
    if not isinstance(result, dict):
        raise ValueError("result.json must contain an object")
    for name in ("baseline_analysis", "optimization", "validation", "artifacts"):
        if not isinstance(result.get(name), dict):
            raise ValueError("result.json is missing %s" % name)
    if result["validation"].get("status") not in ("passed", "failed"):
        raise ValueError("validation.status must be passed or failed")
    if not result["artifacts"].get("optimized_stl"):
        raise ValueError("result.json is missing artifacts.optimized_stl")


class Handler(BaseHTTPRequestHandler):
    server_version = "SlicerOptimizationBridge/0.1"

    def _authorized(self):
        return secrets.compare_digest(
            self.headers.get("X-Bridge-Token", ""), self.server.state.token
        )

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
            if self.command == "POST" and parts == ["jobs"]:
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
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The beta bridge only binds to loopback")
    token = args.token or secrets.token_urlsafe(32)
    state = BridgeState(args.data_dir.resolve(), token)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = state
    print("Optimization bridge: http://%s:%d" % server.server_address)
    print("X-Bridge-Token: %s" % token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Optimization bridge stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
