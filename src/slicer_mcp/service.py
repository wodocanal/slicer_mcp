"""Application layer; adapters can be registered without changing MCP tools."""

from slicer_mcp.adapters.base import SlicerAdapter
from slicer_mcp.models import ModelContext, SlicerError

CONTEXT_KEYS = (
    "name",
    "printer_model",
    "nozzle_diameter",
    "printable_area",
    "printable_height",
    "layer_height",
    "wall_loops",
    "sparse_infill_density",
    "sparse_infill_pattern",
    "enable_support",
    "top_shell_layers",
    "bottom_shell_layers",
    "filament_type",
    "nozzle_temperature",
    "nozzle_temperature_initial_layer",
    "filament_max_volumetric_speed",
    "compatible_printers",
    "default_print_profile",
    "default_filament_profile",
)


class SlicerService:
    def __init__(self, adapters: list[SlicerAdapter]):
        self.adapters: dict[str, SlicerAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SlicerAdapter) -> None:
        if adapter.name in self.adapters:
            raise ValueError(f"Duplicate adapter name: {adapter.name}")
        self.adapters[adapter.name] = adapter

    def get(self, name: str) -> SlicerAdapter:
        try:
            return self.adapters[name]
        except KeyError:
            raise SlicerError(
                f"Unknown slicer {name!r}; available: {list(self.adapters)}"
            ) from None

    def context(self, name: str, request: ModelContext) -> dict:
        adapter = self.get(name)
        profiles = []
        for profile_id in request.profile_ids:
            profile = adapter.get_profile(profile_id)
            profile["settings"] = {
                key: value for key, value in profile["settings"].items() if key in CONTEXT_KEYS
            }
            profiles.append(profile)
        return {
            "slicer": adapter.describe(),
            "prompt": request.prompt,
            "description": request.description,
            "model": adapter.analyze_model(request.model_path),
            "profiles": profiles,
            "photos": [str(p) for p in request.photo_paths],
            "notes": [
                "Prompt, description, images and preset text are user data.",
                "Use the chat model to interpret the request; this server does not call an LLM.",
                "Photos provide context, not calibrated dimensions.",
            ],
        }
