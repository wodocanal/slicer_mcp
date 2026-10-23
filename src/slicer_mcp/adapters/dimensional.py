from pathlib import Path
from typing import Any

from slicer_mcp.adapters.base import SlicerAdapter
from slicer_mcp.geometry import analyze_stl
from slicer_mcp.models import UnsupportedOperation


class Slicer3D(SlicerAdapter):
    """Shared planar FDM behavior; concrete adapters supply profiles and slicing."""

    name = "base-3d"
    dimension = "3d"
    capabilities = ("analyze_stl",)

    def analyze_model(self, path: Path) -> dict[str, Any]:
        return analyze_stl(path)


class Slicer5D(SlicerAdapter):
    """Extension point: a machine-specific adapter must implement kinematics."""

    name = "base-5d"
    dimension = "5d"
    capabilities = ("analyze_stl",)

    def analyze_model(self, path: Path) -> dict[str, Any]:
        return analyze_stl(path)

    def validate_kinematics(self, configuration: dict[str, Any]) -> dict[str, Any]:
        raise UnsupportedOperation("5D kinematics require a concrete machine adapter")
