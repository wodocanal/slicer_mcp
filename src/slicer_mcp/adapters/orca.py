"""OrcaSlicer CLI adapter. All writes live in an isolated job directory."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from slicer_mcp.adapters.dimensional import Slicer3D
from slicer_mcp.geometry import stl_path
from slicer_mcp.models import SliceRequest, SlicerError, SliceResult
from slicer_mcp.profiles import ProfileCatalog


def find_orca() -> Path | None:
    configured = os.environ.get("ORCASLICER_BIN")
    candidates = (
        [configured]
        if configured
        else [
            "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
            str(Path.home() / "Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"),
            shutil.which("orca-slicer"),
            shutil.which("OrcaSlicer"),
        ]
    )
    return next(
        (
            Path(p).resolve()
            for p in candidates
            if p and Path(p).is_file() and os.access(p, os.X_OK)
        ),
        None,
    )


def gcode_statistics(path: Path) -> dict[str, str]:
    result = {}
    with path.open(errors="replace") as stream:
        for line in stream:
            if line.startswith("; model printing time:"):
                for item in line[2:].split(";"):
                    key, value = item.split(":", 1)
                    result[key.strip()] = value.strip()
            elif line.startswith("; total layer number:"):
                result["total layer number"] = line.split(":", 1)[1].strip()
            if line.startswith("; ") and "=" in line:
                key, value = line[2:].split("=", 1)
                key = key.strip()
                if key.startswith(("estimated printing time", "total filament", "filament used")):
                    result[key] = value.strip()
    return result


class OrcaSlicer(Slicer3D):
    name = "orca"
    capabilities = (
        "analyze_stl",
        "list_profiles",
        "read_profiles",
        "plan_parameters",
        "slice_stl",
        "export_gcode",
        "export_3mf",
    )

    def __init__(
        self,
        workspace: Path,
        executable: Path | None = None,
        catalog: ProfileCatalog | None = None,
        timeout: float = 180,
    ):
        self.workspace = workspace.resolve()
        self.executable = executable or find_orca()
        self.timeout = timeout
        if catalog is None:
            builtin = os.environ.get("ORCASLICER_PROFILES_DIR")
            resources = (
                self.executable.parent.parent / "Resources/profiles"
                if self.executable
                else Path("/nonexistent-orca-profiles")
            )
            user = Path(
                os.environ.get(
                    "ORCASLICER_USER_DIR",
                    str(Path.home() / "Library/Application Support/OrcaSlicer/user"),
                )
            )
            catalog = ProfileCatalog(
                {"builtin": Path(builtin) if builtin else resources, "user": user}
            )
        self.catalog = catalog

    def describe(self) -> dict:
        result = super().describe()
        result.update(
            {
                "available": self.executable is not None,
                "executable": str(self.executable) if self.executable else None,
                "printing_supported": False,
                "input_formats": ["stl"],
            }
        )
        return result

    def list_profiles(self, kind: str, query: str, limit: int) -> dict:
        return self.catalog.list(kind, query, limit)

    def get_profile(self, profile_id: str) -> dict:
        return {
            "id": profile_id,
            "kind": self.catalog.get(profile_id).kind,
            "settings": self.catalog.resolve(profile_id),
        }

    def _settings(self, request: SliceRequest) -> tuple[dict, dict, dict]:
        settings = []
        for kind in ("machine", "process", "filament"):
            id_ = getattr(request, f"{kind}_profile")
            if self.catalog.get(id_).kind != kind:
                raise SlicerError(f"Expected a {kind} profile, got {id_}")
            settings.append(self.catalog.resolve(id_))
        machine, process, filament = settings
        for profile in (process, filament):
            compatible = profile.get("compatible_printers", [])
            if compatible and machine["name"] not in compatible:
                raise SlicerError(
                    f"{profile['name']} does not list {machine['name']} as compatible"
                )
        nozzles = machine.get("nozzle_diameter", [])
        if len(nozzles) != 1:
            raise SlicerError("The MVP supports single-nozzle printer profiles only")
        for key, value in request.parameters.model_dump(exclude_none=True).items():
            if key == "sparse_infill_density":
                process[key] = f"{value:g}%"
            elif isinstance(value, bool):
                process[key] = "1" if value else "0"
            else:
                process[key] = str(value)
        layer = float(process.get("layer_height", 0))
        if not 0 < layer <= float(nozzles[0]) * 0.8:
            raise SlicerError("Layer height must be positive and at most 80% of nozzle diameter")
        return machine, process, filament

    def plan_parameters(self, request: SliceRequest) -> dict:
        machine, process, filament = self._settings(request)
        before = self.catalog.resolve(request.process_profile)
        changes = {
            key: {"before": before.get(key), "after": process[key]}
            for key in request.parameters.model_dump(exclude_none=True)
        }
        return {
            "machine": machine["name"],
            "process": process["name"],
            "filament": filament["name"],
            "changes": changes,
            "effective_parameters": {
                key: process.get(key) for key in type(request.parameters).model_fields
            },
            "warnings": [
                "Preset compatibility lists are checked; Orca validates the final configuration.",
                "Strength is not simulated or guaranteed.",
            ],
        }

    def slice(self, request: SliceRequest) -> SliceResult:
        if self.executable is None:
            raise SlicerError("OrcaSlicer CLI not found; set ORCASLICER_BIN to its executable")
        model_path = stl_path(request.model_path)
        settings = self._settings(request)
        analysis = self.analyze_model(model_path)
        if not analysis["watertight"] or not analysis["winding_consistent"]:
            raise SlicerError("Repair the non-watertight/inconsistent STL before slicing")
        job_id = uuid4().hex
        job = self.workspace / "jobs" / job_id
        job.mkdir(parents=True)
        input_path = job / "model.stl"
        shutil.copyfile(model_path, input_path)
        for kind, data in zip(("machine", "process", "filament"), settings, strict=True):
            (job / f"{kind}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        command = [
            str(self.executable),
            "--datadir",
            str(job / "orca-data"),
            "--load-settings",
            f"{job}/machine.json;{job}/process.json",
            "--load-filaments",
            str(job / "filament.json"),
            "--slice",
            "0",
            "--arrange",
            "1",
            "--orient",
            "0",
            "--ensure-on-bed",
            "--outputdir",
            str(job),
            "--export-3mf",
            "result.3mf",
            str(input_path),
        ]
        manifest_path = job / "manifest.json"
        manifest = {
            "job_id": job_id,
            "status": "running",
            "slicer": self.name,
            "request": request.model_dump(mode="json"),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "command": command,
            "plan": self.plan_parameters(request),
        }

        def save_manifest() -> None:
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

        save_manifest()
        log_path = job / "orca.log"
        try:
            with log_path.open("w") as log:
                completed = subprocess.run(
                    command,
                    cwd=job,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                    check=False,
                )
            if completed.returncode != 0:
                raise SlicerError(f"OrcaSlicer exited with code {completed.returncode}")
            gcodes = sorted(job.glob("*.gcode"))
            if not gcodes or any(p.stat().st_size == 0 for p in gcodes):
                raise SlicerError("OrcaSlicer returned success without a non-empty G-code file")
            for path in gcodes:
                with path.open(errors="replace") as stream:
                    if not any(re.match(r"^G[01]\s.*\bE-?[0-9]", line) for line in stream):
                        raise SlicerError("Generated file contains no extrusion moves")
            warnings = list(analysis["warnings"])
            log_text = log_path.read_text(errors="replace")
            if "Unable to load shaders" in log_text:
                warnings.append(
                    "Orca CLI reported OpenGL shader errors; 3MF thumbnails may be missing. G-code was produced."
                )
            result = SliceResult(
                job_id=job_id,
                slicer=self.name,
                gcode_paths=[str(p) for p in gcodes],
                project_path=str(job / "result.3mf") if (job / "result.3mf").exists() else None,
                manifest_path=str(manifest_path),
                log_path=str(log_path),
                statistics={p.name: gcode_statistics(p) for p in gcodes},
                warnings=warnings,
            )
            manifest.update({"status": "sliced", "result": result.model_dump(mode="json")})
            save_manifest()
            return result
        except (OSError, subprocess.TimeoutExpired, SlicerError) as exc:
            manifest.update({"status": "failed", "error": str(exc)})
            save_manifest()
            raise SlicerError(f"{exc}. Job: {job_id}. Log: {log_path}") from exc
