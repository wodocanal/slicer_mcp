import json
from pathlib import Path

import pytest
import trimesh

from slicer_mcp.profiles import ProfileCatalog


@pytest.fixture
def cube(tmp_path: Path) -> Path:
    path = tmp_path / "куб с пробелами.stl"
    mesh = trimesh.creation.box(extents=[20, 20, 20])
    mesh.apply_translation([10, 10, 10])
    mesh.export(path)
    return path


@pytest.fixture
def catalog(tmp_path: Path) -> ProfileCatalog:
    root = tmp_path / "profiles"
    profiles = {
        "machine/base": {"name": "base", "nozzle_diameter": ["0.4"]},
        "machine/printer": {"name": "printer", "inherits": "base"},
        "process/base": {"name": "base", "layer_height": "0.20", "wall_loops": "2"},
        "process/standard": {
            "name": "standard",
            "inherits": "base",
            "compatible_printers": ["printer"],
            "sparse_infill_density": "15%",
        },
        "filament/pla": {"name": "pla", "compatible_printers": ["printer"]},
    }
    for name, data in profiles.items():
        path = root / f"vendor/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return ProfileCatalog({"test": root})
