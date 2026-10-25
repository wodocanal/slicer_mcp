#!/usr/bin/env python3
"""Batch real STE Slicer jobs through the installed plugin's MCP endpoint."""

import argparse
import json
import os
import re
import socket
import sys
import time
import traceback
from pathlib import Path

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import ProxyHandler, Request, build_opener
except ImportError:  # pragma: no cover
    from urllib2 import HTTPError, ProxyHandler, Request, URLError, build_opener


ROTATIONAL_WORDS = ("wheel", "roller", "cylinder", "ролик", "колес", "цилиндр")
CLASSIC_WORDS = ("gear", "bracket", "plate", "шестер", "кроншт", "пластин")
TERMINAL_SLICE_STATES = ("done", "disabled", "error", "failed")


class PipelineError(RuntimeError):
    pass


class TransientMcpError(PipelineError):
    """A timeout or temporary loopback transport failure that may be retried."""


def infer_print_mode(name, configured=None):
    if configured:
        if configured not in ("classic", "cylindrical_full"):
            raise PipelineError("Unsupported pipeline print mode: %s" % configured)
        return configured
    lowered = name.lower()
    if any(word in lowered for word in ROTATIONAL_WORDS):
        return "cylindrical_full"
    if any(word in lowered for word in CLASSIC_WORDS):
        return "classic"
    return "classic"


def check_rotary_axis(dimensions, axis, tolerance=0.80):
    """Choose the most plausible rotary axis without blocking the pipeline.

    STE uses Y-up, and imported parts are often already placed on a flat face.
    Therefore a configuration copied from STL coordinates can name the wrong
    scene axis.  Prefer the axis whose two radial dimensions match best.  A
    large mismatch remains visible as a warning, but it is not a slicing gate.
    """
    if not isinstance(dimensions, list) or len(dimensions) != 3:
        raise PipelineError("The loaded object has no usable XYZ dimensions")
    axes = {"X": 0, "Y": 1, "Z": 2}
    axis = str(axis or "").upper()
    if axis not in axes:
        raise PipelineError("5D model requires rotary_axis X, Y or Z")
    values = [float(value) for value in dimensions]
    if min(values) <= 0:
        raise PipelineError("5D model dimensions must be positive")

    candidates = []
    for candidate_axis, axial_index in axes.items():
        radial = [value for index, value in enumerate(values)
                  if index != axial_index]
        axial = values[axial_index]
        mismatch = abs(radial[0] - radial[1]) / max(radial)
        ambiguous = axial >= max(radial) * 0.95
        candidates.append({
            "axis": candidate_axis,
            "axial_dimension_mm": axial,
            "radial_dimensions_mm": radial,
            "radial_mismatch_percent": round(mismatch * 100.0, 3),
            "mismatch": mismatch,
            "ambiguous": ambiguous,
        })
    requested = next(item for item in candidates if item["axis"] == axis)
    # A non-ambiguous axis is more useful than an equal-diameter but axial
    # dimension, then minimize the actual radial mismatch.
    selected = min(candidates, key=lambda item: (
        item["ambiguous"], item["mismatch"], item["axial_dimension_mm"]
    ))
    warnings = []
    if selected["axis"] != axis:
        warnings.append(
            "Configured 5D axis %s has %.1f%% radial mismatch; using %s with %.1f%%"
            % (axis, requested["mismatch"] * 100.0, selected["axis"],
               selected["mismatch"] * 100.0)
        )
    if selected["mismatch"] > tolerance:
        warnings.append(
            "5D radial dimensions differ by %.1f%% (soft limit %.1f%%); slicing continues"
            % (selected["mismatch"] * 100.0, tolerance * 100.0)
        )
    if selected["ambiguous"]:
        warnings.append(
            "5D rotary axis %s remains geometrically ambiguous; slicing continues"
            % selected["axis"]
        )
    return {
        "confirmed": not selected["ambiguous"] and selected["mismatch"] <= tolerance,
        "axis": selected["axis"],
        "requested_axis": axis,
        "axial_dimension_mm": selected["axial_dimension_mm"],
        "radial_dimensions_mm": selected["radial_dimensions_mm"],
        "radial_mismatch_percent": selected["radial_mismatch_percent"],
        "warnings": warnings,
    }


def validate_artifact(path, kind):
    path = Path(path).resolve()
    if not path.is_file():
        raise PipelineError("Expected artifact was not created: %s" % path)
    size = path.stat().st_size
    if kind == "3mf":
        if size <= 0:
            raise PipelineError("3MF artifact is empty: %s" % path)
        return {"path": str(path), "bytes": size}
    if size <= 1024:
        raise PipelineError("G-code artifact is too small: %s (%d bytes)" % (path, size))
    sample = path.read_bytes()[:4 * 1024 * 1024].decode("utf-8", "replace")
    commands = {
        "G28": r"(?mi)^\s*G28(?:\s|;|$)",
        "G1": r"(?mi)^\s*G1(?:\s|;|$)",
        "M104/M109": r"(?mi)^\s*M10[49](?:\s|;|$)",
        "M140/M190": r"(?mi)^\s*M(?:140|190)(?:\s|;|$)",
    }
    found = {label: re.search(pattern, sample) is not None
             for label, pattern in commands.items()}
    # Stereotech start-code profiles may home outside the exported payload, so
    # G28 is diagnostic rather than a reason to reject an otherwise valid file.
    missing = [label for label in ("G1", "M104/M109", "M140/M190")
               if not found[label]]
    if missing:
        raise PipelineError("G-code is missing commands: %s" % ", ".join(missing))
    warnings = [] if found["G28"] else [
        "G28 is absent; accepted for a Stereotech profile"
    ]
    return {"path": str(path), "bytes": size, "commands": found,
            "warnings": warnings}


class McpClient(object):
    def __init__(self, url, token, timeout=120):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.request_id = 0
        self.opener = build_opener(ProxyHandler({}))

    def _rpc_once(self, method, params=None, timeout=None):
        self.request_id += 1
        payload = json.dumps({
            "jsonrpc": "2.0", "id": self.request_id,
            "method": method, "params": params or {},
        }).encode("utf-8")
        request = Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "X-Session-Token": self.token},
        )
        try:
            response = self.opener.open(request, timeout=timeout or self.timeout)
            value = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:2000]
            if error.code in (408, 429, 500, 502, 503, 504):
                raise TransientMcpError("MCP HTTP %s: %s" % (error.code, detail))
            raise PipelineError("MCP HTTP %s: %s" % (error.code, detail))
        except (TimeoutError, socket.timeout) as error:
            raise TransientMcpError("MCP request timed out: %s" % error)
        except URLError as error:
            raise TransientMcpError("MCP socket is temporarily unavailable: %s" % error)
        except ValueError as error:
            raise PipelineError("MCP request failed: %s" % error)
        if value.get("error"):
            error = value["error"]
            raise PipelineError("MCP %s: %s" % (error.get("code"), error.get("message")))
        return value.get("result") or {}

    def rpc(self, method, params=None, timeout=None, retries=0, retry_delay=1.5):
        attempts = int(retries) + 1
        for attempt in range(attempts):
            try:
                return self._rpc_once(method, params, timeout)
            except TransientMcpError:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(float(retry_delay))

    def call(self, name, arguments=None, timeout=None, retries=0,
             retry_delay=1.5):
        result = self.rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout,
            retries=retries, retry_delay=retry_delay,
        )
        if result.get("isError"):
            raise PipelineError("Tool %s returned an error" % name)
        return result.get("structuredContent")


class PipelineRunner(object):
    def __init__(self, client, results_root, slice_timeout=180, poll_seconds=2,
                 overwrite=False):
        self.client = client
        self.results_root = Path(results_root).resolve()
        self.slice_timeout = int(slice_timeout)
        self.poll_seconds = float(poll_seconds)
        self.overwrite = bool(overwrite)
        self.log_path = self.results_root / "pipeline.log"

    def log(self, model, message, **fields):
        self.results_root.mkdir(parents=True, exist_ok=True)
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "model": model, "message": message}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def run(self, models):
        self.client.rpc("initialize", {"protocolVersion": "2024-11-05"}, retries=2)
        available = self.client.rpc("tools/list", retries=2).get("tools") or []
        names = {item.get("name") for item in available}
        required = {
            "load_model", "list_objects", "remove_object", "set_print_mode",
            "list_profiles", "select_profile", "auto_orient_object",
            "start_slicing", "get_slicing_status", "save_project_3mf",
            "export_gcode",
        }
        missing = sorted(required - names)
        if missing:
            raise PipelineError("Installed plugin is missing tools: %s" % ", ".join(missing))
        summary = []
        for specification in models:
            summary.append(self.run_model(specification))
        return summary

    def run_model(self, specification):
        name = str(specification["name"])
        source = Path(specification["path"]).resolve()
        output = self.results_root / name
        report_path = output / "pipeline-result.json"
        started = time.monotonic()
        report = {"model": name, "source": str(source), "status": "running"}
        self.log(name, "started", source=str(source))
        try:
            if not source.is_file() or source.suffix.lower() != ".stl":
                raise PipelineError("STL source does not exist: %s" % source)
            output.mkdir(parents=True, exist_ok=True)
            project_path = output / "project.3mf"
            gcode_path = output / "result.gcode"
            for artifact in (project_path, gcode_path, report_path):
                if artifact.exists():
                    if not self.overwrite:
                        raise PipelineError("Output already exists: %s" % artifact)
                    artifact.unlink()
            self._clear_scene(name)
            self._load_model_safely(name, source)
            objects = self._wait_for_objects(1, 20)
            if len(objects) != 1:
                raise PipelineError("Expected one loaded object, found %d" % len(objects))
            part = objects[0]
            mode = infer_print_mode(name, specification.get("mode"))
            self.client.call("set_print_mode", {"mode": mode}, retries=2)
            material = self._select_profile(
                "material", specification.get("material", "generic_pla_175")
            )
            quality = self._select_profile(
                "quality", specification.get("quality", "fast")
            )
            orientation = None
            if mode == "classic":
                orientation = self.client.call(
                    "auto_orient_object", {"object_id": part["id"]}, retries=2
                )
            else:
                orientation = check_rotary_axis(
                    part.get("dimensions_mm"), specification.get("rotary_axis")
                )
                for warning in orientation.get("warnings") or []:
                    self.log(name, "rotary_axis_warning", warning=warning,
                             selected_axis=orientation.get("axis"))
            for key, value in (specification.get("settings") or {}).items():
                self.client.call("set_setting", {"key": key, "value": value,
                                                  "scope": "global"}, retries=2)
            self._start_slicing_safely(name)
            slicing = self._wait_for_slice(name)
            self._wait_for_qt_ready(name)
            project_result = self._write_artifact_safely(
                name, "save_project_3mf", project_path, "3mf")
            self._wait_for_qt_ready(name)
            gcode_result = self._write_artifact_safely(
                name, "export_gcode", gcode_path, "gcode")
            project = validate_artifact(project_path, "3mf")
            gcode = validate_artifact(gcode_path, "gcode")
            for label, tool_result, artifact in (
                    ("3MF", project_result, project), ("G-code", gcode_result, gcode)):
                if not isinstance(tool_result, dict) or tool_result.get("verified") is not True:
                    raise PipelineError("%s tool did not verify its output" % label)
                if Path(tool_result.get("path", "")).resolve() != Path(artifact["path"]):
                    raise PipelineError("%s tool returned a different output path" % label)
            report.update({
                "status": "completed", "mode": mode, "material": material,
                "quality": quality, "object": part, "orientation": orientation,
                "slicing": slicing, "project": project, "gcode": gcode,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "screenshots": sorted(path.name for path in output.glob("chat_*.png")),
            })
            self.log(name, "completed", elapsed_seconds=report["elapsed_seconds"],
                     project_bytes=project["bytes"], gcode_bytes=gcode["bytes"])
        except Exception as error:
            report.update({"status": "failed", "error": str(error),
                           "traceback": traceback.format_exc(),
                           "elapsed_seconds": round(time.monotonic() - started, 3)})
            self.log(name, "failed", error=str(error))
        output.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        return report

    def _clear_scene(self, model):
        objects = self.client.call("list_objects", {}, retries=2) or []
        for item in objects:
            object_id = item["id"]
            try:
                self.client.call("remove_object", {"object_id": object_id}, retries=2)
            except TransientMcpError:
                remaining = self.client.call("list_objects", {}, retries=2) or []
                if any(value.get("id") == object_id for value in remaining):
                    raise
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if not (self.client.call("list_objects", {}, retries=2) or []):
                    return
            except TransientMcpError as error:
                self.log(model, "waiting_for_scene", reason=str(error))
            time.sleep(0.2)
        raise PipelineError("Scene did not become empty before %s" % model)

    def _load_model_safely(self, model, source):
        """Avoid duplicate imports when load_model completed after its HTTP timeout."""
        last_error = None
        for attempt in range(3):
            try:
                self.client.call("load_model", {"path": str(source)})
                return
            except TransientMcpError as error:
                last_error = error
                self.log(model, "load_model_retry", attempt=attempt + 1,
                         reason=str(error))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        if self.client.call("list_objects", {}, retries=1) or []:
                            return
                    except TransientMcpError:
                        pass
                    time.sleep(0.5)
                if attempt < 2:
                    time.sleep(1.5)
        raise last_error

    def _wait_for_objects(self, minimum, timeout):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                objects = self.client.call("list_objects", {}, retries=2) or []
                if len(objects) >= minimum:
                    return objects
            except TransientMcpError as error:
                last_error = error
            time.sleep(0.2)
        if last_error is not None:
            raise PipelineError("Model check remained unavailable: %s" % last_error)
        raise PipelineError("Model did not appear in the STE scene")

    def _select_profile(self, kind, wanted):
        listing = self.client.call("list_profiles", {"kind": kind}, retries=2)
        profiles = listing.get("profiles") or []
        wanted_lower = str(wanted).lower()
        matches = [item for item in profiles
                   if str(item.get("id", "")).lower() == wanted_lower]
        if not matches:
            matches = [item for item in profiles
                       if wanted_lower in str(item.get("id", "")).lower()
                       or wanted_lower in str(item.get("name", "")).lower()]
        if not matches:
            available = ", ".join(str(item.get("id")) for item in profiles[:20])
            raise PipelineError("%s profile %s is unavailable; available: %s"
                                % (kind, wanted, available))
        selected = self.client.call(
            "select_profile", {"kind": kind, "id": matches[0]["id"], "extruder": 0},
            retries=2,
        )
        return selected.get("selected")

    def _wait_for_slice(self, model):
        deadline = time.monotonic() + self.slice_timeout
        last_state = None
        while time.monotonic() < deadline:
            try:
                status = self.client.call("get_slicing_status", {}, retries=2)
            except TransientMcpError as error:
                self.log(model, "slicer_busy", operation="get_slicing_status",
                         reason=str(error))
                time.sleep(self.poll_seconds)
                continue
            state = status.get("state")
            if state != last_state:
                self.log(model, "slicing", state=state)
                last_state = state
            if state == "done":
                return status
            if state in TERMINAL_SLICE_STATES and state != "done":
                raise PipelineError("Slicing stopped with state: %s" % state)
            time.sleep(self.poll_seconds)
        raise PipelineError("Slicing exceeded %d seconds" % self.slice_timeout)

    def _start_slicing_safely(self, model):
        """Confirm that slicing started before repeating a timed-out write call."""
        last_error = None
        for attempt in range(3):
            try:
                self.client.call("start_slicing", {})
                return
            except TransientMcpError as error:
                last_error = error
                self.log(model, "start_slicing_retry", attempt=attempt + 1,
                         reason=str(error))
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        status = self.client.call(
                            "get_slicing_status", {}, retries=1)
                        if status.get("state") in ("processing", "done"):
                            return
                    except TransientMcpError:
                        pass
                    time.sleep(0.5)
                if attempt < 2:
                    time.sleep(1.5)
        raise last_error

    def _wait_for_qt_ready(self, model, timeout=30):
        """Require two consecutive scene reads after long Qt/engine operations."""
        deadline = time.monotonic() + timeout
        consecutive = 0
        last_error = None
        while time.monotonic() < deadline:
            try:
                self.client.call("list_objects", {}, retries=1)
                consecutive += 1
                if consecutive >= 2:
                    return
            except TransientMcpError as error:
                consecutive = 0
                last_error = error
                self.log(model, "slicer_busy", operation="qt_ready", reason=str(error))
            time.sleep(0.5)
        raise PipelineError("STE Slicer did not become responsive: %s" % last_error)

    def _write_artifact_safely(self, model, tool_name, path, kind):
        """Retry writes without duplicating an operation that finished after timeout."""
        last_error = None
        for attempt in range(3):
            try:
                return self.client.call(tool_name, {"path": str(path)},
                                        timeout=self.client.timeout)
            except (TransientMcpError, PipelineError) as error:
                last_error = error
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        artifact = validate_artifact(path, kind)
                        self.log(model, "artifact_recovered_after_rpc_error",
                                 tool=tool_name, path=artifact["path"],
                                 attempt=attempt + 1, reason=str(error))
                        return {
                            "success": True,
                            "verified": True,
                            "path": artifact["path"],
                            "bytes": artifact["bytes"],
                            "verification_source": "pipeline_disk_check_after_rpc_error",
                        }
                    except PipelineError:
                        time.sleep(0.5)
                if not isinstance(error, TransientMcpError):
                    raise
                if attempt < 2:
                    self._wait_for_qt_ready(model)
                    time.sleep(1.5)
        raise last_error


def load_models(config_path):
    config_path = Path(config_path).resolve()
    value = json.loads(config_path.read_text(encoding="utf-8"))
    models = value.get("models") if isinstance(value, dict) else value
    if not isinstance(models, list) or not models:
        raise PipelineError("Pipeline config must contain a non-empty models list")
    result = []
    for raw in models:
        item = dict(raw)
        path = Path(item["path"])
        if not path.is_absolute():
            path = config_path.parent / path
        item["path"] = str(path.resolve())
        result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser(description="Run real STE Slicer batch jobs over MCP")
    parser.add_argument("--url", required=True, help="http://127.0.0.1:<port>/mcp")
    parser.add_argument("--token", default=os.environ.get("STE_MCP_TOKEN", ""))
    parser.add_argument("--config", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--slice-timeout", type=int, default=180)
    parser.add_argument("--http-timeout", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not args.token:
        raise PipelineError("Set STE_MCP_TOKEN or pass --token")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    runner = PipelineRunner(
        McpClient(args.url, args.token, timeout=args.http_timeout), args.results,
        args.slice_timeout, args.poll_seconds, args.overwrite,
    )
    summary = runner.run(load_models(args.config))
    failed = [item for item in summary if item.get("status") != "completed"]
    print(json.dumps({"models": summary, "failed": len(failed)},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
