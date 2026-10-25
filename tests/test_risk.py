import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "AIAssistantTeam"
sys.path.insert(0, str(PLUGIN))

from core.errors import ToolError
from core.risk import assess_print_risk


def snapshot(material="PLA", duration=3600, weight=30, speed=45):
    return {
        "printing_mode": "classic",
        "material_name": material,
        "objects": [
            {
                "name": "part",
                "dimensions_mm": [50, 20, 50],
                "footprint_bbox_mm2": 2500,
            }
        ],
        "settings": {
            "layer_height": 0.2,
            "machine_nozzle_size": 0.4,
            "print_speed": speed,
            "support_enable": False,
            "adhesion_type": "brim",
            "material_print_temperature": 210,
            "material_bed_temperature": 55,
            "machine_buildplate_type": "PEI",
        },
        "slicing": {
            "state": "done",
            "print_time_seconds": duration,
            "material_weights_g": [weight],
            "material_names": [material],
        },
    }


def test_low_risk_pla_has_explainable_categories():
    result = assess_print_risk(
        snapshot(),
        {
            "first_layer_contact_area_mm2": 1200,
            "longest_bridge_mm": 3,
            "max_unsupported_overhang_deg": 35,
            "bed_clean": True,
        },
    )
    assert 0 <= result["overall_score"] < 40
    assert result["overall_level"] in ("low", "moderate")
    assert {item["id"] for item in result["categories"]} == {
        "adhesion",
        "geometry",
        "material",
        "visual_quality",
        "job_failure",
    }
    assert result["facts"]["nozzle_diameter_mm"] == 0.4
    assert "not a failure probability" in result["summary"]


def test_demanding_long_pc_job_with_bad_geometry_is_high_risk():
    value = snapshot(material="PC Carbon", duration=18 * 3600, weight=620, speed=110)
    value["settings"].update(
        {
            "adhesion_type": "none",
            "material_print_temperature": 220,
            "material_bed_temperature": 60,
            "layer_height": 0.32,
        }
    )
    result = assess_print_risk(
        value,
        {
            "first_layer_contact_area_mm2": 70,
            "longest_bridge_mm": 28,
            "max_unsupported_overhang_deg": 70,
            "enclosure": False,
            "material_dry": False,
            "bed_clean": False,
        },
    )
    assert result["overall_score"] >= 75
    assert result["overall_level"] == "critical"
    categories = {item["id"]: item for item in result["categories"]}
    assert categories["adhesion"]["score"] >= 75
    assert categories["material"]["score"] >= 75
    assert categories["geometry"]["score"] >= 60
    assert any("temperature" in reason.lower() for reason in categories["material"]["reasons"])


def test_missing_geometry_is_reported_instead_of_guessed():
    value = snapshot()
    value["slicing"] = {"state": "not_started"}
    result = assess_print_risk(value)
    assert len(result["unknowns"]) >= 4
    assert result["facts"]["print_time_seconds"] == 0
    assert result["confidence"] < 80


def test_invalid_observations_are_rejected():
    with pytest.raises(ToolError):
        assess_print_risk(snapshot(), {"max_unsupported_overhang_deg": 120})
    with pytest.raises(ToolError):
        assess_print_risk(snapshot(), {"enclosure": "maybe"})
