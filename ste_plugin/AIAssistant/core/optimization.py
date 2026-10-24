"""Restricted client and comparison helpers for the optional Ansys bridge."""

import json
from decimal import Decimal

try:
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:  # pragma: no cover
    from urlparse import urlparse
    from urllib2 import Request, urlopen, HTTPError, URLError

from .errors import ToolError


class OptimizationBridgeClient(object):
    def __init__(self, configuration, timeout=15):
        self._configuration = configuration
        self._timeout = timeout

    def _config(self):
        base_url, token = self._configuration()
        base_url = (base_url or "").strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("Optimization bridge URL is not configured")
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
            raise ToolError("A remote optimization bridge must use HTTPS")
        return base_url, (token or "").strip()

    def request(self, method, path, body=None, timeout=None):
        base_url, token = self._config()
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "X-Bridge-Token": token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=data, headers=headers)
        request.get_method = lambda: method
        try:
            response = urlopen(request, timeout=timeout or self._timeout)
            result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:1000]
            raise ToolError("Optimization bridge HTTP %s: %s" % (error.code, detail))
        except (URLError, ValueError) as error:
            raise ToolError("Optimization bridge unavailable: %s" % error)
        if not isinstance(result, dict):
            raise ToolError("Optimization bridge returned an invalid response")
        return result

    def capabilities(self, deep=True):
        return self.request("GET", "/health?deep=%d" % (1 if deep else 0), timeout=60)

    def templates(self):
        return self.request("GET", "/templates")

    def start(self, specification):
        return self.request("POST", "/jobs", specification, timeout=60)

    def status(self, job_id):
        return self.request("GET", "/jobs/%s" % _safe_id(job_id))

    def cancel(self, job_id):
        return self.request("POST", "/jobs/%s/cancel" % _safe_id(job_id), {})


def _safe_id(value):
    value = str(value or "")
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in value.lower()):
        raise ToolError("Invalid optimization job ID")
    return value


def compare_optimization_variants(baseline, optimized):
    if not baseline or not optimized:
        raise ToolError("Capture both baseline and optimized completed slices first")
    before = baseline["quote"]
    after = optimized["quote"]
    before_mass = sum(Decimal(str(value)) for value in before["material_weights_g"])
    after_mass = sum(Decimal(str(value)) for value in after["material_weights_g"])
    return {
        "baseline": baseline,
        "optimized": optimized,
        "changes": {
            "print_time_seconds": after["print_time_seconds"] - before["print_time_seconds"],
            "material_weight_g": float(after_mass - before_mass),
            "print_cost_rub": round(after["print_cost_rub"] - before["print_cost_rub"], 2),
            "risk_score": optimized["risk"]["overall_score"] - baseline["risk"]["overall_score"],
        },
        "same_tariffs": baseline["tariffs"] == optimized["tariffs"],
        "same_print_settings": baseline["settings"] == optimized["settings"],
        "engineering_warning": (
            "Slicer economics and screening risk do not validate structural strength. "
            "Use the Ansys validation result and physical FDM testing."
        ),
    }
