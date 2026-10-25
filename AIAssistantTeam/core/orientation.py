"""Choose a stable build-plate face from the *transformed* triangle mesh.

Coordinates follow STE Slicer's convention: Y is vertical. This is a geometric
screening heuristic, not a slicer or a guarantee that a part needs no supports.
"""

import math

import numpy

from .errors import ToolError


DOWN = numpy.array([0.0, -1.0, 0.0])
MAX_FACES = 80000
MAX_POINTS = 120000


def _unit(vector):
    length = float(numpy.linalg.norm(vector))
    return vector / length if length > 1e-10 else None


def _basis(normal):
    reference = numpy.array([1.0, 0.0, 0.0])
    if abs(normal[0]) > 0.9:
        reference = numpy.array([0.0, 0.0, 1.0])
    first = _unit(numpy.cross(normal, reference))
    return first, numpy.cross(normal, first)


def _triangles(vertices, indices):
    points = numpy.asarray(vertices, dtype=numpy.float64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
        raise ToolError("The object has no usable triangle mesh")
    if not numpy.isfinite(points).all():
        raise ToolError("The mesh contains non-finite coordinates")
    if indices is None or len(indices) == 0:
        if len(points) % 3:
            raise ToolError("An unindexed mesh must contain complete triangles")
        faces = points.reshape((-1, 3, 3))
    else:
        face_indices = numpy.asarray(indices, dtype=numpy.int64).reshape((-1, 3))
        if face_indices.size and (face_indices.min() < 0 or face_indices.max() >= len(points)):
            raise ToolError("Mesh face indices are outside the vertex array")
        if len(face_indices) > MAX_FACES:
            face_indices = face_indices[::int(math.ceil(float(len(face_indices)) / MAX_FACES))]
        faces = points[face_indices]
    if len(faces) > MAX_FACES:
        faces = faces[::int(math.ceil(float(len(faces)) / MAX_FACES))]
    if len(points) > MAX_POINTS:
        points = points[::int(math.ceil(float(len(points)) / MAX_POINTS))]
    return points, faces


def _candidate_normals(normals, areas):
    # Quantisation groups coplanar triangles while tolerating STL normal noise.
    groups = {}
    for normal, area in zip(normals, areas):
        key = tuple(numpy.rint(normal * 12).astype(int))
        if key not in groups:
            groups[key] = [numpy.zeros(3), 0.0]
        groups[key][0] += normal * area
        groups[key][1] += area
    ranked = sorted(groups.values(), key=lambda item: item[1], reverse=True)
    candidates = [DOWN]
    for vector, unused_area in ranked[:32]:
        normal = _unit(vector)
        if normal is None:
            continue
        for direction in (normal, -normal):
            if all(float(numpy.dot(direction, old)) < 0.985 for old in candidates):
                candidates.append(direction)
        if len(candidates) >= 33:
            break
    return candidates


def _measure(normal, points, faces, face_normals, areas):
    projection = numpy.dot(points, normal)
    top = float(projection.max())
    bottom = float(projection.min())
    height = top - bottom
    tolerance = max(0.06, height * 0.002)
    face_projection = numpy.tensordot(faces, normal, axes=([2], [0]))
    on_plate = numpy.min(face_projection, axis=1) >= top - tolerance
    downward = numpy.dot(face_normals, normal)
    contact_area = float(areas[on_plate & (numpy.abs(downward) > 0.9)].sum())
    unsupported_area = float(areas[(downward > 0.707) & ~on_plate].sum())
    axis_a, axis_b = _basis(normal)
    x = numpy.dot(points, axis_a)
    z = numpy.dot(points, axis_b)
    footprint = float((x.max() - x.min()) * (z.max() - z.min()))
    return {
        "contact_area_mm2": contact_area,
        "bounding_footprint_mm2": footprint,
        "height_mm": height,
        "downward_face_area_mm2": unsupported_area,
    }


def choose_orientation(vertices, indices=None):
    """Return the best support face normal in world coordinates, or no change.

    Use measured coplanar contact, height and downward-facing surface area.
    Existing orientation remains a candidate. Small or ambiguous improvements
    are left alone to avoid needless movement of correctly placed parts.
    """
    points, faces = _triangles(vertices, indices)
    raw_normals = numpy.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    doubled_areas = numpy.linalg.norm(raw_normals, axis=1)
    valid = doubled_areas > 1e-9
    if not valid.any():
        raise ToolError("The mesh has no non-degenerate faces")
    faces = faces[valid]
    areas = doubled_areas[valid] * 0.5
    normals = raw_normals[valid] / doubled_areas[valid][:, None]
    total_area = float(areas.sum())
    candidates = _candidate_normals(normals, areas)
    options = []
    for normal in candidates:
        result = _measure(normal, points, faces, normals, areas)
        result["normal"] = normal
        options.append(result)
    max_contact = max(item["contact_area_mm2"] for item in options)
    max_height = max(item["height_mm"] for item in options)
    for item in options:
        relative_contact = item["contact_area_mm2"] / max(max_contact, 1e-9)
        compactness = item["contact_area_mm2"] / max(item["bounding_footprint_mm2"], 1e-9)
        relative_height = item["height_mm"] / max(max_height, 1e-9)
        unsupported = item["downward_face_area_mm2"] / max(total_area, 1e-9)
        item["score"] = (65 * relative_contact + 18 * min(compactness, 1) +
                         17 * (1 - relative_height) - 25 * unsupported)
    current = options[0]
    best = max(options, key=lambda item: item["score"])
    contact_fraction = best["contact_area_mm2"] / max(best["bounding_footprint_mm2"], 1e-9)
    changed = (best is not current and best["score"] > current["score"] + 5 and
               best["contact_area_mm2"] >= 5 and contact_fraction >= 0.03)
    def public(item):
        return {key: round(item[key], 2) for key in (
            "contact_area_mm2", "bounding_footprint_mm2", "height_mm",
            "downward_face_area_mm2", "score")}
    return {
        "changed": bool(changed),
        "normal": [float(value) for value in best["normal"]] if changed else None,
        "angle_deg": round(math.degrees(math.acos(max(-1.0, min(1.0,
                            float(numpy.dot(best["normal"], DOWN)))))), 2) if changed else 0,
        "before": public(current),
        "after": public(best if changed else current),
        "reason": ("A larger stable contact face and lower unsupported area were found."
                   if changed else "Current placement is competitive, or the mesh has no reliable flat support face."),
        "confidence": ("high" if contact_fraction >= 0.35 else
                       "moderate" if contact_fraction >= 0.12 else "low"),
        "note": "Estimated triangle contact is a heuristic; verify the preview and support needs.",
    }
