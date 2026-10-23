"""Slicer-independent contracts. The MCP layer speaks only these types."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ParameterPatch(Contract):
    """Small, explicit MVP parameter surface; omitted fields keep preset values."""

    layer_height: float | None = Field(default=None, gt=0, le=1)
    wall_loops: int | None = Field(default=None, ge=1, le=20)
    sparse_infill_density: float | None = Field(default=None, ge=0, le=100)
    enable_support: bool | None = None
    top_shell_layers: int | None = Field(default=None, ge=0, le=30)
    bottom_shell_layers: int | None = Field(default=None, ge=0, le=30)


class SliceRequest(Contract):
    model_path: Path
    machine_profile: str
    process_profile: str
    filament_profile: str
    parameters: ParameterPatch = Field(default_factory=ParameterPatch)


class ModelContext(Contract):
    model_path: Path
    prompt: str = ""
    description: str = ""
    photo_paths: list[Path] = Field(default_factory=list, max_length=4)
    profile_ids: list[str] = Field(default_factory=list, max_length=3)


class SliceResult(Contract):
    job_id: str
    status: Literal["sliced"] = "sliced"
    slicer: str
    gcode_paths: list[str]
    project_path: str | None = None
    manifest_path: str
    log_path: str
    statistics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SlicerError(ValueError):
    """An actionable, expected adapter error exposed to the MCP client."""


class UnsupportedOperation(SlicerError):
    pass
