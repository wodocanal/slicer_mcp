"""Geometry-only helpers for placing an imported comparison variant."""


def centered_scene_build_area(width, depth, shape="rectangular", margin=5.0):
    """Return STE scene X/Z limits; the visual plate is centered at the origin."""
    width = float(width)
    depth = float(depth)
    margin = min(float(margin), width / 20.0, depth / 20.0)
    return {
        "min_x": -width / 2.0 + margin,
        "max_x": width / 2.0 - margin,
        "min_z": -depth / 2.0 + margin,
        "max_z": depth / 2.0 - margin,
        "shape": shape or "rectangular",
    }


def _translated(rectangle, dx, dz):
    return (
        rectangle[0] + dx,
        rectangle[1] + dx,
        rectangle[2] + dz,
        rectangle[3] + dz,
    )


def _inside_build_area(rectangle, build_area):
    if not build_area:
        return True
    if not (
        rectangle[0] >= build_area["min_x"]
        and rectangle[1] <= build_area["max_x"]
        and rectangle[2] >= build_area["min_z"]
        and rectangle[3] <= build_area["max_z"]
    ):
        return False
    shape = str(build_area.get("shape", "")).lower()
    if "ellipt" not in shape and "circ" not in shape and "round" not in shape:
        return True
    center_x = (build_area["min_x"] + build_area["max_x"]) / 2.0
    center_z = (build_area["min_z"] + build_area["max_z"]) / 2.0
    radius_x = (build_area["max_x"] - build_area["min_x"]) / 2.0
    radius_z = (build_area["max_z"] - build_area["min_z"]) / 2.0
    if radius_x <= 0 or radius_z <= 0:
        return False
    # Check bounding box corners with minor tolerance (0.999) for float precision
    for x in (rectangle[0], rectangle[1]):
        for z in (rectangle[2], rectangle[3]):
            if ((x - center_x) / radius_x) ** 2 + ((z - center_z) / radius_z) ** 2 > 1.001:
                return False
    return True


def _overlaps(rectangle, occupied, clearance):
    for other in occupied:
        separated = (
            rectangle[1] + clearance <= other[0]
            or rectangle[0] - clearance >= other[1]
            or rectangle[3] + clearance <= other[2]
            or rectangle[2] - clearance >= other[3]
        )
        if not separated:
            return True
    return False


def choose_adjacent_offset(new_rectangle, occupied, build_area=None, gap_mm=10.0):
    """Return a collision-free X/Z offset without moving existing objects.

    Tries cardinal and diagonal adjacent positions, progressively scales down
    gap if space is tight, and falls back to a free-space grid search on the build
    surface.
    """
    if not occupied:
        return {"dx": 0.0, "dz": 0.0, "side": "current", "gap_mm": 0.0}
    min_x = min(item[0] for item in occupied)
    max_x = max(item[1] for item in occupied)
    min_z = min(item[2] for item in occupied)
    max_z = max(item[3] for item in occupied)
    new_width = new_rectangle[1] - new_rectangle[0]
    new_depth = new_rectangle[3] - new_rectangle[2]
    center_x = (min_x + max_x) / 2.0
    center_z = (min_z + max_z) / 2.0

    # Multi-gap progression: requested gap, followed by safer smaller gaps
    gap_candidates = [gap_mm]
    for g in (5.0, 3.0, 1.5):
        if g < gap_mm and g not in gap_candidates:
            gap_candidates.append(g)

    for current_gap in gap_candidates:
        candidates = [
            # Cardinal directions
            (max_x + current_gap - new_rectangle[0], center_z - new_depth / 2.0 - new_rectangle[2], "right"),
            (min_x - current_gap - new_rectangle[1], center_z - new_depth / 2.0 - new_rectangle[2], "left"),
            (center_x - new_width / 2.0 - new_rectangle[0], max_z + current_gap - new_rectangle[2], "back"),
            (center_x - new_width / 2.0 - new_rectangle[0], min_z - current_gap - new_rectangle[3], "front"),
            # Diagonal corners
            (max_x + current_gap - new_rectangle[0], max_z + current_gap - new_rectangle[2], "back-right"),
            (max_x + current_gap - new_rectangle[0], min_z - current_gap - new_rectangle[3], "front-right"),
            (min_x - current_gap - new_rectangle[1], max_z + current_gap - new_rectangle[2], "back-left"),
            (min_x - current_gap - new_rectangle[1], min_z - current_gap - new_rectangle[3], "front-left"),
        ]
        for dx, dz, side in candidates:
            candidate = _translated(new_rectangle, dx, dz)
            if _inside_build_area(candidate, build_area) and not _overlaps(candidate, occupied, 0.0):
                return {"dx": dx, "dz": dz, "side": side, "gap_mm": current_gap}

    # Grid search fallback if direct adjacent placement is constrained
    if build_area:
        step = 5.0
        x_start = build_area["min_x"]
        x_end = build_area["max_x"] - new_width
        z_start = build_area["min_z"]
        z_end = build_area["max_z"] - new_depth
        cx = (build_area["min_x"] + build_area["max_x"]) / 2.0
        cz = (build_area["min_z"] + build_area["max_z"]) / 2.0

        grid_points = []
        x = x_start
        while x <= x_end + 1e-6:
            z = z_start
            while z <= z_end + 1e-6:
                dist = (x + new_width / 2.0 - cx) ** 2 + (z + new_depth / 2.0 - cz) ** 2
                grid_points.append((dist, x, z))
                z += step
            x += step
        grid_points.sort(key=lambda item: item[0])

        for dist, gx, gz in grid_points:
            dx = gx - new_rectangle[0]
            dz = gz - new_rectangle[2]
            candidate = _translated(new_rectangle, dx, dz)
            if _inside_build_area(candidate, build_area) and not _overlaps(candidate, occupied, 1.5):
                return {"dx": dx, "dz": dz, "side": "free_space_grid", "gap_mm": 1.5}

    return None

