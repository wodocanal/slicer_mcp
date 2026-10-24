import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "ste_plugin" / "AIAssistant"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLUGIN))

from core.errors import ToolError
from core.optimization import OptimizationBridgeClient, compare_optimization_variants

from optimization_bridge.server import BridgeState, _validate_inputs, _validate_result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("X-Bridge-Token") != "secret":
            self.send_response(403)
            self.end_headers()
            return
        body = json.dumps({"status": "ready", "templates_ready": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        pass


def test_bridge_client_uses_configured_loopback_and_token():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        client = OptimizationBridgeClient(
            lambda: (f"http://127.0.0.1:{server.server_port}", "secret")
        )
        assert client.capabilities(False)["status"] == "ready"
    finally:
        server.shutdown()
        server.server_close()


def test_client_rejects_insecure_remote_bridge():
    client = OptimizationBridgeClient(lambda: ("http://example.com:8765", "secret"))
    with pytest.raises(ToolError):
        client.capabilities(False)


def test_bridge_reports_missing_pymechanical_or_templates_honestly(tmp_path):
    state = BridgeState(tmp_path, "secret")
    health = state.health(False)
    assert health["templates_ready"] == 0
    assert health["status"] == "unavailable"
    assert health.get("reason")


def test_template_contract_requires_real_model_and_inputs(tmp_path):
    source = tmp_path / "part.step"
    source.write_text("test")
    manifest = {"required_inputs": ["load_n", "material_id"], "source_extensions": [".step"]}
    request = {
        "source_model": {"path": str(source)},
        "inputs": {"load_n": 500, "material_id": "validated"},
    }
    _validate_inputs(request, manifest)
    request["inputs"].pop("load_n")
    with pytest.raises(ValueError):
        _validate_inputs(request, manifest)


def test_result_contract_requires_validation_and_real_stl_reference():
    valid = {
        "baseline_analysis": {"status": "passed"},
        "optimization": {"status": "completed"},
        "validation": {"status": "passed"},
        "artifacts": {"optimized_stl": "C:/job/optimized.stl"},
    }
    _validate_result(valid)
    invalid = dict(valid)
    invalid["validation"] = {"status": "not_run"}
    with pytest.raises(ValueError):
        _validate_result(invalid)


def test_optimization_comparison_keeps_engineering_and_slicer_results_separate():
    def variant(slot, seconds, weight, cost, risk):
        return {
            "slot": slot,
            "quote": {
                "print_time_seconds": seconds,
                "material_weights_g": [weight],
                "print_cost_rub": cost,
            },
            "risk": {"overall_score": risk},
            "tariffs": {"machine": 100},
            "settings": {"layer_height": 0.2},
        }

    result = compare_optimization_variants(
        variant("baseline", 3600, 100, 200, 25),
        variant("optimized", 4200, 70, 180, 40),
    )
    assert result["changes"] == {
        "print_time_seconds": 600,
        "material_weight_g": -30.0,
        "print_cost_rub": -20,
        "risk_score": 15,
    }
    assert "strength" in result["engineering_warning"].lower()
