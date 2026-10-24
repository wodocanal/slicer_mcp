import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "ste_plugin" / "AIAssistant"
sys.path.insert(0, str(PLUGIN))

from core.errors import ToolError
from core.risk_map import analyze_geometry_risk_map


def mesh():
    return [
        {
            "object_id": "part-1",
            "name": "Bracket",
            "vertices": [
                [0, 0, 0],
                [0, 0, 10],
                [10, 0, 0],
                [0, 10, 0],
                [10, 10, 0],
                [0, 10, 10],
            ],
            "triangles": [[0, 1, 2], [3, 4, 5]],
        }
    ]


def test_risk_map_returns_contact_area_and_spatial_overhang_hotspot():
    result = analyze_geometry_risk_map(
        mesh(), {"overhang_limit_deg": 45, "first_layer_height_mm": 0.3}
    )
    assert result["totals"]["first_layer_contact_area_mm2"] == 50
    assert result["totals"]["risky_overhang_area_mm2"] == 50
    assert result["totals"]["hotspot_count"] == 1
    hotspot = result["hotspots"][0]
    assert hotspot["object_id"] == "part-1"
    assert hotspot["kind"] == "bridge_candidate"
    assert hotspot["centroid_mm"] == pytest.approx([10 / 3, 10, 10 / 3], abs=0.001)
    assert hotspot["overhang_deg"] == 90


def test_risk_map_caps_returned_hotspots_but_reports_full_count():
    value = mesh()
    value[0]["triangles"].append([3, 4, 5])
    result = analyze_geometry_risk_map(value, {"max_hotspots": 1})
    assert result["totals"]["hotspot_count"] == 2
    assert len(result["hotspots"]) == 1


def test_risk_map_rejects_invalid_thresholds():
    with pytest.raises(ToolError):
        analyze_geometry_risk_map(mesh(), {"overhang_limit_deg": 120})
    with pytest.raises(ToolError):
        analyze_geometry_risk_map(mesh(), {"max_hotspots": 0})
