import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "AIAssistantTeam"
sys.path.insert(0, str(PLUGIN))

from core.errors import ToolError
from core.manufacturing import (
    capture_manufacturing_scenario,
    compare_manufacturing_scenarios,
    plan_manufacturing_settings,
)


def snapshot(seconds=3600, weight=100, speed=50):
    return {
        "printing_mode": "classic",
        "material_id": "pla",
        "material_name": "PLA",
        "objects": [{"name": "part", "dimensions_mm": [40, 40, 20], "footprint_bbox_mm2": 1600}],
        "settings": {
            "machine_nozzle_size": 0.4,
            "layer_height": 0.2,
            "wall_line_count": 3,
            "infill_sparse_density": 20,
            "print_speed": speed,
            "speed_print_layer_0": 20,
            "adhesion_type": "brim",
            "support_enable": False,
            "material_print_temperature": 210,
            "material_bed_temperature": 60,
            "machine_buildplate_type": "textured PEI",
        },
        "slicing": {
            "state": "done",
            "print_time_seconds": seconds,
            "material_weights_g": [weight],
            "material_names": ["PLA"],
        },
    }


def capture_args(scenario, quantity=1):
    return {
        "scenario": scenario,
        "quantity": quantity,
        "parts_per_job": 2,
        "machine_count": 2,
        "machine_hour_rate_rub": 100,
        "material_prices_rub_per_kg": [1000],
        "batch_setup_cost_rub": 50,
        "per_part_extra_cost_rub": 10,
        "reserve_percent": 10,
        "observations": {
            "first_layer_contact_area_mm2": 1200,
            "longest_bridge_mm": 2,
            "max_unsupported_overhang_deg": 30,
            "bed_clean": True,
        },
    }


def test_planner_distinguishes_fast_prototype_and_final_production():
    prototype = plan_manufacturing_settings(
        snapshot(),
        {
            "scenario": "prototype",
            "quantity": 1,
            "strength_priority": 20,
            "appearance_priority": 20,
            "speed_priority": 90,
        },
    )
    production = plan_manufacturing_settings(
        snapshot(),
        {
            "scenario": "production",
            "quantity": 100,
            "strength_priority": 80,
            "appearance_priority": 80,
            "speed_priority": 30,
            "cost_priority": 20,
        },
    )
    assert (
        prototype["recommended_settings"]["layer_height"]
        > production["recommended_settings"]["layer_height"]
    )
    assert (
        prototype["recommended_settings"]["wall_line_count"]
        < production["recommended_settings"]["wall_line_count"]
    )
    assert (
        prototype["recommended_settings"]["infill_sparse_density"]
        < production["recommended_settings"]["infill_sparse_density"]
    )


def test_batch_capture_scales_actual_cost_time_reserve_and_parallelism():
    result = capture_manufacturing_scenario(snapshot(), capture_args("production", 10))
    assert result["base_jobs"] == 5
    assert result["reserve_parts"] == 1
    assert result["planned_jobs"] == 6
    assert result["planned_capacity_parts"] == 12
    assert result["unused_capacity_parts"] == 1
    assert result["production"]["total_machine_time_seconds"] == 21600
    assert result["production"]["estimated_elapsed_seconds"] == 10800
    assert result["production"]["total_material_weights_g"] == [600]
    assert result["cost"]["reference_job_cost_rub"] == 200
    assert result["cost"]["batch_cost_rub"] == 1350
    assert result["cost"]["cost_per_requested_part_rub"] == 135
    assert result["risk"]["overall_score"] < 50


def test_comparison_uses_captured_slice_facts_without_claiming_strength():
    prototype = capture_manufacturing_scenario(
        snapshot(seconds=1800, weight=50, speed=70), capture_args("prototype", 1)
    )
    production = capture_manufacturing_scenario(
        snapshot(seconds=3600, weight=100, speed=50), capture_args("production", 10)
    )
    result = compare_manufacturing_scenarios(prototype, production)
    assert result["comparison"]["reference_job_time_change_seconds"] == 1800
    assert result["comparison"]["reference_job_material_change_g"] == 50
    assert "strength" in result["warnings"][1].lower()


def test_manufacturing_rejects_incomplete_slice_and_invalid_batch_values():
    incomplete = snapshot()
    incomplete["slicing"]["state"] = "processing"
    with pytest.raises(ToolError):
        capture_manufacturing_scenario(incomplete, capture_args("production", 10))
    for field, value in (("quantity", 0), ("parts_per_job", 1.5), ("machine_count", 0)):
        arguments = capture_args("production", 10)
        arguments[field] = value
        with pytest.raises(ToolError):
            capture_manufacturing_scenario(snapshot(), arguments)
