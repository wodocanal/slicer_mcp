"""Restricted client and comparison helpers for the local TopOpt bridge."""

import json
import math
import os
from decimal import Decimal

try:
    from urllib.parse import urlparse
    from urllib.request import ProxyHandler, Request, build_opener, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:  # pragma: no cover
    from urlparse import urlparse
    from urllib2 import ProxyHandler, Request, build_opener, urlopen, HTTPError, URLError

from .errors import ToolError


def map_optimization_status(capabilities, templates):
    """Merge a successful health response with the fetched template list."""
    mapped = dict(capabilities or {})
    template_items = list(templates or [])
    if mapped.get("status") == "ready" and template_items:
        mapped["status"] = "ready"
        mapped["bridge_status"] = "connected"
        mapped["topopt_available"] = True
        mapped["templates_ready"] = len(template_items)
        mapped.pop("reason", None)
    return mapped


def normalize_optimization_specification(specification, default_source_path=None):
    """Validate chat inputs and normalize a fractional mass target to percent."""
    if not isinstance(specification, dict):
        raise ToolError("Optimization specification must be an object")
    normalized = dict(specification)
    template_id = str(normalized.get("template_id") or "").strip()
    if not template_id:
        raise ToolError("template_id is required")
    normalized["template_id"] = template_id

    inputs = normalized.get("inputs")
    if not isinstance(inputs, dict):
        raise ToolError("inputs must be an object")
    inputs = dict(inputs)
    material_id = str(inputs.get("material_id") or "").strip()
    if not material_id:
        raise ToolError("material_id is required")
    inputs["material_id"] = material_id
    try:
        load_n = float(inputs.get("load_n"))
    except (TypeError, ValueError):
        raise ToolError("load_n must be a positive number")
    if not math.isfinite(load_n) or load_n <= 0:
        raise ToolError("load_n must be a positive number")
    inputs["load_n"] = load_n
    try:
        target = float(inputs.get("target_mass_percent"))
    except (TypeError, ValueError):
        raise ToolError("target_mass_percent must be a number from 1 to 99")
    if not math.isfinite(target):
        raise ToolError("target_mass_percent must be a number from 1 to 99")
    if 0 < target < 1:
        target *= 100.0
    if target < 1 or target > 99:
        raise ToolError("target_mass_percent must be from 1 to 99")
    inputs["target_mass_percent"] = target
    normalized["inputs"] = inputs

    source = normalized.get("source_model")
    if source is None and default_source_path:
        source = {"path": default_source_path, "fixture": True}
    if not isinstance(source, dict) or not source.get("path"):
        raise ToolError(
            "source_model.path is required when the default bracket fixture is unavailable"
        )
    path = os.path.abspath(os.path.expanduser(str(source["path"])))
    if not os.path.isfile(path):
        raise ToolError("Source model does not exist: %s" % path)
    if os.path.splitext(path)[1].lower() != ".stl":
        raise ToolError("The bundled TopOpt template requires an STL source model")
    normalized["source_model"] = {"path": path}
    if source.get("fixture"):
        normalized["source_model"]["fixture"] = True
    return normalized


class OptimizationBridgeClient(object):
    def __init__(self, configuration, timeout=15):
        self._configuration = configuration
        self._timeout = timeout
        self._active_discovery = None

    def _config(self):
        base_url, token, _automatic = self._resolved_config()
        return base_url, token

    def _resolved_config(self):
        base_url, token = self._configuration()
        base_url = (base_url or "").strip().rstrip("/")
        token = (token or "").strip()
        parsed_configured = urlparse(base_url)
        configured_host = (parsed_configured.hostname or "").lower()
        configured_port = parsed_configured.port
        use_discovery = (
            not token
            or (
                parsed_configured.scheme == "http"
                and configured_host in ("127.0.0.1", "localhost", "::1")
                and configured_port == 8765
            )
        )
        discovered = self._active_discovery or _read_discovered_connection()
        discovered_url = (discovered.get("url") or "").strip().rstrip("/")
        automatic = bool(discovered_url and use_discovery)
        if automatic:
            base_url = discovered_url
            token = (discovered.get("token") or "").strip()
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("Optimization bridge URL is not configured")
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
            raise ToolError("A remote optimization bridge must use HTTPS")
        if not token:
            raise ToolError(
                "Optimization bridge token is unavailable; start the local bridge "
                "or configure the token manually"
            )
        return base_url, token, automatic

    def request(self, method, path, body=None, timeout=None):
        base_url, token = self._config()
        try:
            return self._request_once(base_url, token, method, path, body, timeout)
        except HTTPError as error:
            if error.code != 403 or not _is_loopback_url(base_url):
                detail = error.read().decode("utf-8", "replace")[:1000]
                raise ToolError("Optimization bridge HTTP %s: %s" % (error.code, detail))
            # A bridge restart may replace the token after this client cached it.
            # Drop the cache, force a fresh read, and repeat exactly once.
            self._active_discovery = None
            discovered = _read_discovered_connection()
            retry_url = (discovered.get("url") or base_url).rstrip("/")
            retry_token = (discovered.get("token") or "").strip()
            try:
                result = self._request_once(retry_url, retry_token, method,
                                            path, body, timeout)
                self._active_discovery = discovered
                return result
            except HTTPError as retry_error:
                detail = retry_error.read().decode("utf-8", "replace")[:1000]
                raise ToolError(
                    "Optimization bridge HTTP %s: %s" % (retry_error.code, detail)
                )
            except (URLError, ValueError) as retry_error:
                raise ToolError("Optimization bridge unavailable: %s" % retry_error)
        except (URLError, ValueError) as error:
            raise ToolError("Optimization bridge unavailable: %s" % error)

    def _request_once(self, base_url, token, method, path, body=None, timeout=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "X-Bridge-Token": token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=data, headers=headers)
        request.get_method = lambda: method
        if _is_loopback_url(base_url):
            response = build_opener(ProxyHandler({})).open(
                request, timeout=timeout or self._timeout
            )
        else:
            response = urlopen(request, timeout=timeout or self._timeout)
        result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict):
            raise ToolError("Optimization bridge returned an invalid response")
        return result

    def capabilities(self, deep=True):
        result = self.request(
            "GET", "/health?deep=%d" % (1 if deep else 0), timeout=60
        )
        result["connection"] = self.connection_info()
        return result

    def connection_info(self):
        base_url, token, automatic = self._resolved_config()
        return {
            "url": base_url,
            "automatic": automatic,
            "token_available": bool(token),
        }

    def templates(self):
        return self.request("GET", "/templates")

    def start(self, specification):
        return self.request("POST", "/optimize", specification, timeout=60)

    def status(self, job_id):
        return self.request("GET", "/jobs/%s" % _safe_id(job_id))

    def cancel(self, job_id):
        return self.request("POST", "/jobs/%s/cancel" % _safe_id(job_id), {})


def _safe_id(value):
    value = str(value or "")
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in value.lower()):
        raise ToolError("Invalid optimization job ID")
    return value


def _connection_file_paths(path=None):
    if path:
        return [str(path)]
    root = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return [os.path.join(root, "Stereotech", "TopOpt", "connection.json")]


def _connection_file_path():
    return _connection_file_paths()[0]


def _read_discovered_connection(path=None):
    records = _read_discovered_connections(path)
    return records[0] if records else {}


def _read_discovered_connections(path=None):
    records = []
    for candidate in _connection_file_paths(path):
        value = _read_discovered_connection_file(candidate)
        if value and not any(
                item.get("url") == value.get("url") and
                item.get("token") == value.get("token") for item in records):
            records.append(value)
    return records


def _read_discovered_connection_file(path):
    try:
        with open(path, "r") as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    url = value.get("url")
    token = value.get("token")
    if not isinstance(url, str) or not isinstance(token, str):
        return {}
    parsed = urlparse(url)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in (
        "127.0.0.1", "localhost", "::1"
    ):
        return {}
    return {
        "url": url,
        "port": value.get("port"),
        "token": token,
        "pid": value.get("pid"),
        "path": path,
    }


def _is_loopback_url(url):
    parsed = urlparse(url or "")
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in (
        "127.0.0.1", "localhost", "::1"
    )


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
            "Use an engineering validation workflow and physical FDM testing."
        ),
    }
