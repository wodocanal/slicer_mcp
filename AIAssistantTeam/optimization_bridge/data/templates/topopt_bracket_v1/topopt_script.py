#!/usr/bin/env python3
"""Prepared 2D SIMP compliance-minimization template for a cantilever part.

The solver is real, but the boundary conditions are intentionally fixed by the
template: the left edge is constrained and a downward point load is applied at
the middle of the right edge.  It creates an extruded STL candidate.  It does
not calculate stress, fatigue, buckling, safety factor, or certify the part.
"""

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np


def filter2d(values, kernel):
    """Small dependency-free 2D convolution for the sensitivity filter."""
    radius_x, radius_y = kernel.shape[0] // 2, kernel.shape[1] // 2
    padded = np.pad(values, ((radius_x, radius_x), (radius_y, radius_y)), mode="constant")
    result = np.zeros_like(values, dtype=float)
    for i in range(kernel.shape[0]):
        for j in range(kernel.shape[1]):
            if kernel[i, j]:
                result += kernel[i, j] * padded[i:i + values.shape[0], j:j + values.shape[1]]
    return result


def stl_bounds(path):
    data = Path(path).read_bytes()
    points = []
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if len(data) == 84 + 50 * count:
            offset = 84
            for _ in range(count):
                for start in (12, 24, 36):
                    points.append(struct.unpack("<3f", data[offset + start:offset + start + 12]))
                offset += 50
    if not points:
        raise ValueError("The prepared template currently requires a binary STL")
    values = np.asarray(points, dtype=float)
    return values.min(axis=0), values.max(axis=0)


def element_stiffness(nu=0.3):
    a11 = np.array([[12, 3, -6, -3], [3, 12, 3, 0],
                    [-6, 3, 12, -3], [-3, 0, -3, 12]], dtype=float)
    a12 = np.array([[-6, -3, 0, 3], [-3, -6, -3, -6],
                    [0, -3, -6, 3], [3, -6, 3, -6]], dtype=float)
    b11 = np.array([[-4, 3, -2, 9], [3, -4, -9, 4],
                    [-2, -9, -4, -3], [9, 4, -3, -4]], dtype=float)
    b12 = np.array([[2, -3, 4, -9], [-3, 2, 9, -2],
                    [4, 9, 2, 3], [-9, -2, 3, 2]], dtype=float)
    return (np.block([[a11, a12], [a12.T, a11]]) +
            nu * np.block([[b11, b12], [b12.T, b11]])) / (24.0 * (1.0 - nu * nu))


def solve_simp(nelx, nely, volume_fraction, iterations=45, penal=3.0, radius=2.2):
    ke = element_stiffness()
    nodes = np.arange((nelx + 1) * (nely + 1)).reshape((nelx + 1, nely + 1))
    base = (2 * nodes[:-1, :-1] + 2).reshape((-1, 1))
    offsets = np.array([0, 1, 2 * nely + 2, 2 * nely + 3,
                        2 * nely, 2 * nely + 1, -2, -1])
    edof = base + offsets
    rows = np.repeat(edof, 8, axis=1).ravel()
    cols = np.tile(edof, (1, 8)).ravel()
    ndof = 2 * (nelx + 1) * (nely + 1)
    force = np.zeros(ndof)
    load_node = nelx * (nely + 1) + nely // 2
    force[2 * load_node + 1] = -1.0
    fixed = np.arange(2 * (nely + 1))
    free = np.setdiff1d(np.arange(ndof), fixed)
    density = np.full((nelx, nely), volume_fraction, dtype=float)
    kernel_size = int(math.ceil(radius)) * 2 - 1
    center = kernel_size // 2
    kernel = np.zeros((kernel_size, kernel_size), dtype=float)
    for i in range(kernel_size):
        for j in range(kernel_size):
            kernel[i, j] = max(0.0, radius - math.hypot(i - center, j - center))
    denominator = filter2d(np.ones_like(density), kernel)
    history = []
    for step in range(iterations):
        modulus = 1.0e-9 + density.ravel() ** penal * (1.0 - 1.0e-9)
        values = (modulus[:, None, None] * ke[None, :, :]).ravel()
        stiffness = np.zeros((ndof, ndof), dtype=float)
        np.add.at(stiffness, (rows, cols), values)
        displacement = np.zeros(ndof)
        displacement[free] = np.linalg.solve(stiffness[np.ix_(free, free)], force[free])
        ue = displacement[edof]
        energy = np.einsum("ij,ij->i", np.dot(ue, ke), ue).reshape((nelx, nely))
        compliance = float(np.sum(modulus.reshape((nelx, nely)) * energy))
        sensitivity = -penal * density ** (penal - 1.0) * energy
        sensitivity = filter2d(density * sensitivity, kernel) / (
            np.maximum(1.0e-3, density) * denominator)
        lower, upper, move = 0.0, 1.0e9, 0.2
        while (upper - lower) / (upper + lower + 1.0e-12) > 1.0e-4:
            middle = 0.5 * (lower + upper)
            candidate = np.maximum(
                0.001,
                np.maximum(density - move,
                           np.minimum(1.0, np.minimum(density + move,
                                                     density * np.sqrt(-sensitivity / middle)))),
            )
            if candidate.mean() > volume_fraction:
                lower = middle
            else:
                upper = middle
        change = float(np.max(np.abs(candidate - density)))
        density = candidate
        history.append({"iteration": step + 1, "compliance": compliance, "change": change})
        if change < 0.01 and step >= 14:
            break
    return density, history


def normal(a, b, c):
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / length, ny / length, nz / length


def add_quad(triangles, a, b, c, d):
    triangles.append((normal(a, b, c), a, b, c))
    triangles.append((normal(a, c, d), a, c, d))


def density_to_stl(density, path, width, height, depth, threshold=0.45):
    solid = density >= threshold
    nelx, nely = solid.shape
    dx, dz = width / nelx, height / nely
    triangles = []
    for ix in range(nelx):
        for iz in range(nely):
            if not solid[ix, iz]:
                continue
            x0, x1 = ix * dx, (ix + 1) * dx
            z0, z1 = iz * dz, (iz + 1) * dz
            y0, y1 = 0.0, depth
            add_quad(triangles, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))
            add_quad(triangles, (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))
            if ix == 0 or not solid[ix - 1, iz]:
                add_quad(triangles, (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))
            if ix == nelx - 1 or not solid[ix + 1, iz]:
                add_quad(triangles, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))
            if iz == 0 or not solid[ix, iz - 1]:
                add_quad(triangles, (x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
            if iz == nely - 1 or not solid[ix, iz + 1]:
                add_quad(triangles, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    with open(path, "wb") as stream:
        stream.write(b"Slicer MCP TopOpt SIMP geometry candidate".ljust(80, b"\0"))
        stream.write(struct.pack("<I", len(triangles)))
        for item in triangles:
            for vector in item:
                stream.write(struct.pack("<3f", *vector))
            stream.write(struct.pack("<H", 0))
    return len(triangles)


def main():
    request_path = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    inputs = request["inputs"]
    target = float(inputs["target_mass_percent"])
    if target < 1.0 or target > 99.0:
        raise ValueError("target_mass_percent must be from 1 to 99")
    load_n = float(inputs["load_n"])
    if load_n <= 0:
        raise ValueError("load_n must be positive")
    source_path = Path(request["source_model"]["path"]).resolve()
    lower, upper = stl_bounds(source_path)
    dimensions = np.maximum(upper - lower, 1.0)
    ordered = sorted(dimensions.tolist(), reverse=True)
    width, height, depth = ordered[0], ordered[1], ordered[2]
    aspect = max(1.0, min(5.0, width / height))
    nely = 20
    nelx = max(24, min(60, int(round(nely * aspect))))
    density, history = solve_simp(nelx, nely, target / 100.0, iterations=30)
    stl_path = output_dir / "optimized.stl"
    triangles = density_to_stl(density, stl_path, width, height, depth)
    result = {
        "baseline_analysis": {
            "status": "completed",
            "method": "2D plane-stress SIMP compliance minimization",
            "boundary_conditions": "left edge fixed; downward point load at right-edge midpoint",
            "load_n": load_n,
            "material_id": str(inputs["material_id"]),
            "normalized_compliance": history[0]["compliance"]
        },
        "optimization": {
            "status": "completed",
            "engine": "TopOpt SIMP",
            "iterations": len(history),
            "target_mass_percent": target,
            "achieved_density_percent": round(float(density.mean()) * 100.0, 2),
            "normalized_compliance": history[-1]["compliance"],
            "mesh": {"nelx": nelx, "nely": nely, "extrusion_depth_mm": depth},
            "triangle_count": triangles
        },
        "validation": {
            "status": "not_performed",
            "engineering_grade": False,
            "reason": "No 3D stress, fatigue, buckling or safety-factor validation was run. The STL is a geometry candidate for review and physical testing."
        },
        "warnings": [
            "This prepared template is valid only for a prismatic cantilever with the stated boundary conditions.",
            "The source STL supplies envelope dimensions; its original topology is not preserved."
        ],
        "artifacts": {"optimized_stl": str(stl_path)}
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
