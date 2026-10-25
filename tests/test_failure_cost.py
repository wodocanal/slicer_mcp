import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "AIAssistantTeam"
sys.path.insert(0, str(PLUGIN))

from core.errors import ToolError
from core.failure_cost import analyze_failure_cost


def slicing():
    return {
        "state": "done",
        "print_time_seconds": 7200,
        "material_weights_g": [100],
        "material_names": ["PLA"],
    }


def arguments():
    return {
        "machine_hour_rate_rub": 100,
        "material_prices_rub_per_kg": [1000],
        "quantity": 10,
        "parts_per_job": 2,
        "machine_count": 2,
        "delay_cost_rub_per_hour": 50,
        "failure_stages_percent": [25, 50, 100],
    }


def test_failure_cost_shows_early_middle_and_late_loss():
    result = analyze_failure_cost(slicing(), arguments())
    assert result["reference_job"]["print_cost_rub"] == 300
    assert result["production"]["base_jobs"] == 5
    assert result["production"]["baseline_print_cost_rub"] == 1500
    assert [item["lost_material_weights_g"] for item in result["scenarios"]] == [[25], [50], [100]]
    assert (
        result["scenarios"][0]["total_extra_cost_rub"]
        < result["scenarios"][2]["total_extra_cost_rub"]
    )
    assert result["scenarios"][2]["batch_cost_after_failure_rub"] == 2200


def test_expected_loss_uses_only_explicit_probabilities():
    value = arguments()
    value["failure_probabilities_percent"] = [4, 3, 2]
    result = analyze_failure_cost(slicing(), value)
    assert result["user_supplied_total_failure_probability_percent"] == 9
    expected = sum(item["expected_extra_cost_rub"] for item in result["scenarios"])
    assert result["expected_extra_cost_rub"] == pytest.approx(expected, abs=0.01)
    assert result["expected_batch_cost_rub"] == pytest.approx(1500 + expected, abs=0.01)


def test_failure_cost_does_not_accept_probability_over_100_or_incomplete_slice():
    value = arguments()
    value["failure_probabilities_percent"] = [50, 40, 30]
    with pytest.raises(ToolError):
        analyze_failure_cost(slicing(), value)
    incomplete = slicing()
    incomplete["state"] = "processing"
    with pytest.raises(ToolError):
        analyze_failure_cost(incomplete, arguments())
    fractional = arguments()
    fractional["machine_count"] = 1.5
    with pytest.raises(ToolError):
        analyze_failure_cost(slicing(), fractional)
