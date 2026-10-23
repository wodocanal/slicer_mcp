import json

import pytest
from pydantic import ValidationError

from slicer_mcp.adapters.base import SlicerAdapter
from slicer_mcp.adapters.dimensional import Slicer3D, Slicer5D
from slicer_mcp.adapters.orca import OrcaSlicer
from slicer_mcp.models import ParameterPatch, SliceRequest, SlicerError, UnsupportedOperation
from slicer_mcp.service import SlicerService


def request(cube, **parameters):
    return SliceRequest(
        model_path=cube,
        machine_profile="test:vendor/machine/printer.json",
        process_profile="test:vendor/process/standard.json",
        filament_profile="test:vendor/filament/pla.json",
        parameters=parameters,
    )


def test_dimensional_extension_and_null_behavior(cube):
    with pytest.raises(UnsupportedOperation):
        SlicerAdapter().slice(request(cube))
    for adapter in (Slicer3D(), Slicer5D()):
        assert adapter.analyze_model(cube)["volume_mm3"] == pytest.approx(8000)
        with pytest.raises(UnsupportedOperation):
            adapter.slice(request(cube))
    with pytest.raises(UnsupportedOperation):
        Slicer5D().validate_kinematics({})
    service = SlicerService([Slicer5D()])
    assert service.get("base-5d").dimension == "5d"


def test_analysis_rejects_invalid_file(tmp_path):
    path = tmp_path / "bad.stl"
    path.write_text("invalid STL")
    with pytest.raises(SlicerError):
        Slicer3D().analyze_model(path)


def test_parameter_plan_preserves_presets(catalog, cube, tmp_path):
    adapter = OrcaSlicer(tmp_path, catalog=catalog)
    plan = adapter.plan_parameters(request(cube, wall_loops=4, sparse_infill_density=35))
    assert plan["changes"]["wall_loops"] == {"before": "2", "after": "4"}
    assert plan["effective_parameters"]["sparse_infill_density"] == "35%"
    assert catalog.resolve("test:vendor/process/standard.json")["wall_loops"] == "2"
    assert not (tmp_path / "jobs").exists()


@pytest.mark.parametrize(
    "patch",
    [
        {"layer_height": 0},
        {"wall_loops": -1},
        {"sparse_infill_density": 101},
        {"layer_height": float("nan")},
        {"arbitrary_cli_option": "--no-check"},
    ],
)
def test_invalid_parameters(patch):
    with pytest.raises(ValidationError):
        ParameterPatch(**patch)


def test_invalid_nozzle_layer_combination(catalog, cube, tmp_path):
    with pytest.raises(SlicerError, match="80%"):
        OrcaSlicer(tmp_path, catalog=catalog).plan_parameters(request(cube, layer_height=0.4))


def test_wrong_profile_kind(catalog, cube, tmp_path):
    value = request(cube)
    value.machine_profile = value.process_profile
    with pytest.raises(SlicerError, match="Expected a machine"):
        OrcaSlicer(tmp_path, catalog=catalog).plan_parameters(value)


def test_incompatible_profile(catalog, cube, tmp_path):
    catalog.get("test:vendor/filament/pla.json").data["compatible_printers"] = ["other"]
    with pytest.raises(SlicerError, match="compatible"):
        OrcaSlicer(tmp_path, catalog=catalog).plan_parameters(request(cube))


@pytest.mark.parametrize("mode", ["failure", "empty", "timeout", "invalid"])
def test_cli_failures_are_not_success(catalog, cube, tmp_path, mode):
    executable = tmp_path / "fake-orca"
    body = {
        "failure": "raise SystemExit(3)",
        "empty": "pass",
        "timeout": "import time; time.sleep(5)",
        "invalid": "from pathlib import Path; Path('plate_1.gcode').write_text('; no moves')",
    }[mode]
    import sys

    executable.write_text(f"#!{sys.executable}\n{body}\n")
    executable.chmod(0o755)
    adapter = OrcaSlicer(tmp_path, executable=executable, catalog=catalog, timeout=0.15)
    with pytest.raises(SlicerError):
        adapter.slice(request(cube))
    manifest = next((tmp_path / "jobs").glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "failed"
