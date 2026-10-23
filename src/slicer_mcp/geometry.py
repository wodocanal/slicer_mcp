from pathlib import Path

import numpy as np
import trimesh

from slicer_mcp.models import SlicerError


def stl_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".stl":
        raise SlicerError("Provide an existing STL file; 3MF import is not yet supported")
    if path.stat().st_size > 100 * 1024 * 1024:
        raise SlicerError("MVP STL limit is 100 MiB")
    return path


def analyze_stl(path: Path) -> dict:
    path = stl_path(path)
    try:
        mesh = trimesh.load_mesh(path, file_type="stl", process=True)
        if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
            raise ValueError("empty mesh")
        if not np.isfinite(mesh.vertices).all():
            raise ValueError("non-finite coordinates")
        if len(mesh.faces) > 2_000_000:
            raise ValueError("MVP limit is 2 million triangles")
        bounds = mesh.bounds.tolist()
        extents = mesh.extents.tolist()
        area = float(mesh.area)
        volume = float(abs(mesh.volume)) if mesh.is_volume else None
        if not np.isfinite([*mesh.extents, area]).all() or area <= 0:
            raise ValueError("degenerate geometry")
    except Exception as exc:
        raise SlicerError(f"Cannot analyze STL: {exc}") from exc
    warnings = ["STL has no units; coordinates are interpreted as millimetres."]
    if not mesh.is_watertight:
        warnings.append("Mesh is not watertight; repair may be required before slicing.")
    if not mesh.is_winding_consistent:
        warnings.append("Triangle winding is inconsistent.")
    return {
        "path": str(path),
        "units": "mm (assumed)",
        "triangles": len(mesh.faces),
        "vertices": len(mesh.vertices),
        "bounds_mm": bounds,
        "dimensions_mm": extents,
        "surface_area_mm2": area,
        "volume_mm3": volume,
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "warnings": warnings,
    }
