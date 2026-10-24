import ast
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "ste_plugin" / "AIAssistant"
sys.path.insert(0, str(PLUGIN))

from core.economics import compare_cost_variants, estimate_print_cost
from core.errors import ToolError


def test_team_plugin_remains_python35_compatible():
    for path in PLUGIN.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 5))


def test_quote_sums_extruders_machine_and_explicit_other_costs():
    slicing = {
        "state": "done",
        "print_time_seconds": 5400,
        "material_weights_g": [40, 10],
        "material_names": ["PC", "PVA"],
    }
    quote = estimate_print_cost(
        slicing,
        {
            "machine_hour_rate_rub": 200,
            "material_prices_rub_per_kg": [2000, 4000],
            "outside_work_items": [{"label": "CAD", "amount_rub": 50}],
            "markup_rub": 25,
            "tax_rub": 10,
            "target_budget_rub": 500,
        },
    )
    assert [item["cost_rub"] for item in quote["materials"]] == [80, 40]
    assert quote["print_cost_rub"] == 420
    assert quote["customer_price_rub"] == 505
    assert quote["budget_met"] is False


def test_quote_rejects_incomplete_slice_missing_or_invalid_tariffs():
    slicing = {"state": "processing", "print_time_seconds": 10, "material_weights_g": [3]}
    prices = {"machine_hour_rate_rub": 100, "material_prices_rub_per_kg": [1000]}
    with pytest.raises(ToolError):
        estimate_print_cost(slicing, prices)
    slicing["state"] = "done"
    for invalid in ([], [None], [-1], [float("nan")]):
        with pytest.raises(ToolError):
            estimate_print_cost(slicing, dict(prices, material_prices_rub_per_kg=invalid))


def test_comparison_exposes_tariff_and_scene_mismatch():
    slicing = {"state": "done", "print_time_seconds": 3600, "material_weights_g": [100]}
    tariffs = {"machine_hour_rate_rub": 100, "material_prices_rub_per_kg": [1000]}
    baseline = {
        "quote": estimate_print_cost(slicing, tariffs),
        "tariffs": tariffs,
        "scene_objects": ["part"],
    }
    slicing["print_time_seconds"] = 1800
    candidate = {
        "quote": estimate_print_cost(slicing, tariffs),
        "tariffs": dict(tariffs),
        "scene_objects": ["part"],
    }
    comparison = compare_cost_variants(baseline, candidate)
    assert comparison["changes"]["print_cost_rub"] == -50
    assert comparison["same_tariffs"] is True
    candidate["scene_objects"] = []
    candidate["tariffs"]["machine_hour_rate_rub"] = 200
    comparison = compare_cost_variants(baseline, candidate)
    assert comparison["same_tariffs"] is False
    assert comparison["same_scene_objects"] is False
