"""Empty implementation followed by dimensional base implementations."""

from pathlib import Path
from typing import Any

from slicer_mcp.models import SliceRequest, SliceResult, UnsupportedOperation


class SlicerAdapter:
    """Null adapter: unsupported operations fail explicitly, never fake success."""

    name = "empty"
    dimension: str | None = None
    capabilities: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dimension": self.dimension,
            "capabilities": list(self.capabilities),
        }

    def analyze_model(self, path: Path) -> dict[str, Any]:
        raise UnsupportedOperation(f"{self.name}: model analysis is not implemented")

    def list_profiles(self, kind: str, query: str, limit: int) -> dict[str, Any]:
        raise UnsupportedOperation(f"{self.name}: profile discovery is not implemented")

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        raise UnsupportedOperation(f"{self.name}: profile reading is not implemented")

    def plan_parameters(self, request: SliceRequest) -> dict[str, Any]:
        raise UnsupportedOperation(f"{self.name}: parameter planning is not implemented")

    def slice(self, request: SliceRequest) -> SliceResult:
        raise UnsupportedOperation(f"{self.name}: slicing is not implemented")

    def submit_print(self, result: SliceResult, printer_id: str) -> dict[str, Any]:
        raise UnsupportedOperation(f"{self.name}: printer submission is not implemented")
