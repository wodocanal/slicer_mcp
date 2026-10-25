"""Explicit, safe printer-fleet backends for OctoPrint and Moonraker."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .errors import ToolError


class PrinterBackend(object):
    def __init__(self, config):
        self.config = dict(config)
        self.id = str(config.get("id") or "").strip()
        self.name = str(config.get("name") or self.id)
        self.url = str(config.get("url") or "").rstrip("/")
        self.token = str(config.get("token") or "")
        if not self.id or not self.url:
            raise ToolError("Each printer needs an id and URL")

    def _request(self, method, path, data=None, headers=None, timeout=15):
        request = urllib.request.Request(self.url + path, data=data, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            raise ToolError("Printer %s request failed: %s" % (self.id, error))
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {"raw": raw.decode("utf-8", "replace")}

    def summary(self):
        return {"id": self.id, "name": self.name,
                "backend": self.__class__.__name__.replace("Backend", "").lower(),
                "url": self.url}


class OctoPrintBackend(PrinterBackend):
    def _headers(self):
        if not self.token:
            raise ToolError("OctoPrint printer %s has no configured API token" % self.id)
        return {"X-Api-Key": self.token}

    def status(self):
        return self._request("GET", "/api/job", headers=self._headers())

    def temperatures(self):
        return self._request("GET", "/api/printer", headers=self._headers())

    def upload(self, path):
        boundary = "----slicer-mcp-upload"
        with open(path, "rb") as stream:
            content = stream.read()
        name = os.path.basename(path)
        body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                "filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n" %
                (boundary, name)).encode("utf-8") + content + ("\r\n--%s--\r\n" % boundary).encode("ascii")
        headers = self._headers()
        headers["Content-Type"] = "multipart/form-data; boundary=%s" % boundary
        return self._request("POST", "/api/files/local", body, headers, timeout=60)

    def command(self, command):
        payload = json.dumps({"command": command}).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        return self._request("POST", "/api/job", payload, headers)

    def start(self): return self.command("start")
    def pause(self): return self.command("pause")
    def resume(self): return self.command("pause")
    def cancel(self): return self.command("cancel")


class MoonrakerBackend(PrinterBackend):
    def _headers(self):
        return {"X-Api-Key": self.token} if self.token else {}

    def status(self):
        return self._request("GET", "/printer/objects/query?print_stats&display_status",
                             headers=self._headers())

    def temperatures(self):
        return self._request("GET", "/printer/objects/query?extruder&heater_bed",
                             headers=self._headers())

    def upload(self, path):
        boundary = "----slicer-mcp-upload"
        with open(path, "rb") as stream:
            content = stream.read()
        name = os.path.basename(path)
        body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                "filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n" %
                (boundary, name)).encode("utf-8") + content + ("\r\n--%s--\r\n" % boundary).encode("ascii")
        headers = self._headers()
        headers["Content-Type"] = "multipart/form-data; boundary=%s" % boundary
        return self._request("POST", "/server/files/upload", body, headers, timeout=60)

    def _action(self, path): return self._request("POST", path, headers=self._headers())
    def start(self, filename):
        return self._action("/printer/print/start?filename=%s" % urllib.parse.quote(filename))
    def pause(self): return self._action("/printer/print/pause")
    def resume(self): return self._action("/printer/print/resume")
    def cancel(self): return self._action("/printer/print/cancel")


class PrinterFleet(object):
    BACKENDS = {"octoprint": OctoPrintBackend, "moonraker": MoonrakerBackend}

    def __init__(self, configs=None):
        self.printers = {}
        for config in configs or []:
            kind = str(config.get("backend") or "").lower()
            if kind not in self.BACKENDS:
                raise ToolError("Unsupported printer backend: %s" % kind)
            backend = self.BACKENDS[kind](config)
            if backend.id in self.printers:
                raise ToolError("Duplicate printer id: %s" % backend.id)
            self.printers[backend.id] = backend

    @classmethod
    def from_environment(cls):
        raw = os.environ.get("STE_PRINTER_FLEET_JSON", "").strip()
        if not raw:
            return cls([])
        if raw.startswith("[") or raw.startswith("{"):
            value = json.loads(raw)
        else:
            with open(os.path.abspath(os.path.expanduser(raw)), "r", encoding="utf-8") as stream:
                value = json.load(stream)
        return cls(value.get("printers", []) if isinstance(value, dict) else value)

    def list(self):
        return {"configured": len(self.printers),
                "printers": [item.summary() for item in self.printers.values()]}

    def get(self, printer_id):
        if printer_id not in self.printers:
            raise ToolError("Unknown printer_id: %s" % printer_id)
        return self.printers[printer_id]

    def plan(self, jobs):
        ids = sorted(self.printers)
        if not ids:
            raise ToolError("No printers are configured")
        loads = dict((printer_id, 0.0) for printer_id in ids)
        assignments = []
        for job in sorted(jobs, key=lambda item: float(item.get("duration_seconds", 0)), reverse=True):
            printer_id = min(ids, key=lambda value: loads[value])
            duration = max(0.0, float(job.get("duration_seconds", 0)))
            assignments.append({"job_id": str(job.get("job_id")),
                                "printer_id": printer_id,
                                "starts_at_seconds": loads[printer_id],
                                "duration_seconds": duration})
            loads[printer_id] += duration
        return {"assignments": assignments, "printer_load_seconds": loads,
                "makespan_seconds": max(loads.values())}
