import json

import pytest

from pipeline_runner import PipelineError, check_rotary_axis, load_models, validate_artifact


def test_rotary_axis_reports_selected_geometry_axis():
    result = check_rotary_axis([59, 25, 59], "Z")
    assert result["axis"] == "Y"
    assert result["requested_axis"] == "Z"


def test_load_models_resolves_relative_paths(tmp_path):
    model = tmp_path / "part.stl"
    model.write_text("solid x\nendsolid x\n")
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"models": [{"name": "part", "path": "part.stl",
                                                "mode": "classic"}]}))
    assert load_models(str(config))[0]["path"] == str(model.resolve())


def test_pipeline_5d_validator_rejects_classic_payload(tmp_path):
    path = tmp_path / "result.gcode"
    path.write_text("M109 S200\nG1 X1 E1\n" + "; pad\n" * 400)
    with pytest.raises(PipelineError):
        validate_artifact(path, "gcode", "cylindrical_full")
