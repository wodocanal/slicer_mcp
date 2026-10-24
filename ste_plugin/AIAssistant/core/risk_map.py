"""Spatial geometry-risk analysis using triangle locations in the current orientation."""

import math

from .errors import ToolError


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ToolError("%s must contain three coordinates" % label)
    result = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            raise ToolError("%s coordinates must be finite numbers" % label)
        if not math.isfinite(number):
            raise ToolError("%s coordinates must be finite numbers" % label)
        result.append(number)
    return result


def _triangle_geometry(vertices):
    first, second, third = vertices
    ab = [second[index] - first[index] for index in range(3)]
    ac = [third[index] - first[index] for index in range(3)]
    cross = [
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    ]
    length = math.sqrt(sum(value * value for value in cross))
    if length <= 1e-12:
        return 0, [0, 0, 0], [sum(point[index] for point in vertices) / 3.0
                               for index in range(3)]
    return length / 2.0, [value / length for value in cross], [
        sum(point[index] for point in vertices) / 3.0 for index in range(3)
    ]


def analyze_geometry_risk_map(objects, arguments=None):
    """Return spatial hotspots; Y is the STE build direction."""
    arguments = arguments or {}
    overhang_limit = float(arguments.get("overhang_limit_deg", 45))
    first_layer_height = float(arguments.get("first_layer_height_mm", 0.3))
    max_hotspots = int(arguments.get("max_hotspots", 100))
    if not 0 <= overhang_limit <= 90:
        raise ToolError("overhang_limit_deg must be between 0 and 90")
    if not 0 < first_layer_height <= 5:
        raise ToolError("first_layer_height_mm must be between 0 and 5")
    if not 1 <= max_hotspots <= 1000:
        raise ToolError("max_hotspots must be between 1 and 1000")

    hotspots = []
    summaries = []
    total_contact = 0.0
    total_overhang = 0.0
    for item in objects:
        vertices = [_vector(value, "vertex") for value in item.get("vertices", [])]
        triangles = item.get("triangles") or []
        if not vertices or not triangles:
            summaries.append({"object_id": item.get("object_id"), "name": item.get("name"),
                              "analyzed": False, "reason": "No triangle mesh data"})
            continue
        minimum_y = min(point[1] for point in vertices)
        object_contact = 0.0
        object_overhang = 0.0
        analyzed = 0
        for triangle_index, raw_indices in enumerate(triangles):
            if not isinstance(raw_indices, (list, tuple)) or len(raw_indices) != 3:
                continue
            try:
                points = [vertices[int(index)] for index in raw_indices]
            except (IndexError, TypeError, ValueError):
                continue
            area, normal, centroid = _triangle_geometry(points)
            if not area:
                continue
            analyzed += 1
            maximum_height = max(point[1] for point in points) - minimum_y
            minimum_height = min(point[1] for point in points) - minimum_y
            if maximum_height <= first_layer_height and abs(normal[1]) > 0.5:
                object_contact += area * abs(normal[1])
                continue
            downward = max(0.0, -normal[1])
            overhang_deg = math.degrees(math.asin(min(1.0, downward)))
            if overhang_deg > overhang_limit:
                severity = min(100, round(40 + (overhang_deg - overhang_limit) * 2.0))
                kind = "bridge_candidate" if downward > 0.92 and minimum_height > 1 else "overhang"
                object_overhang += area
                hotspots.append({
                    "object_id": item.get("object_id"),
                    "object_name": item.get("name"),
                    "triangle_index": triangle_index,
                    "kind": kind,
                    "severity": int(severity),
                    "centroid_mm": [round(value, 3) for value in centroid],
                    "area_mm2": round(area, 3),
                    "overhang_deg": round(overhang_deg, 2),
                    "height_above_bed_mm": round(centroid[1] - minimum_y, 3),
                    "reason": (
                        "Down-facing triangle exceeds the configured overhang threshold; "
                        "bridge classification is geometric screening, not support-ray validation."
                    ),
                })
        total_contact += object_contact
        total_overhang += object_overhang
        summaries.append({
            "object_id": item.get("object_id"),
            "name": item.get("name"),
            "analyzed": True,
            "triangle_count": analyzed,
            "first_layer_contact_area_mm2": round(object_contact, 3),
            "risky_overhang_area_mm2": round(object_overhang, 3),
        })

    hotspots.sort(key=lambda value: (value["severity"], value["area_mm2"]), reverse=True)
    returned = hotspots[:max_hotspots]
    return {
        "coordinate_system": "STE world coordinates; +Y is build direction",
        "overhang_limit_deg": overhang_limit,
        "first_layer_height_mm": first_layer_height,
        "objects": summaries,
        "totals": {
            "first_layer_contact_area_mm2": round(total_contact, 3),
            "risky_overhang_area_mm2": round(total_overhang, 3),
            "hotspot_count": len(hotspots),
            "returned_hotspot_count": len(returned),
        },
        "hotspots": returned,
        "limitations": [
            "Bridge candidates are down-facing triangles, not a full ray-cast bridge analysis.",
            "Thin walls, trapped supports, nozzle reachability and 5D collisions are not measured.",
            "The result is a geometric screening map in the current orientation.",
        ],
    }
