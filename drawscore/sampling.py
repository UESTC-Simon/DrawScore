# Copyright 2026 Hugging Face
# SPDX-License-Identifier: Apache-2.0
# Adapted from CADGenBench sampling; see NOTICE for provenance and changes.
"""Area-weighted sampling of tessellated STEP solid surfaces."""

import numpy as np
from build123d.topology import Compound
from OCP.BRep import BRep_Tool  # type: ignore[attr-defined]
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # type: ignore[attr-defined]
from OCP.TopAbs import TopAbs_FACE  # type: ignore[attr-defined]
from OCP.TopExp import TopExp_Explorer  # type: ignore[attr-defined]
from OCP.TopLoc import TopLoc_Location  # type: ignore[attr-defined]
from OCP.TopoDS import TopoDS  # type: ignore[attr-defined]


def sample_shape(shape: Compound, n_points: int, seed: int) -> np.ndarray:
    """Return uniformly area-weighted points from the original registration mesh."""
    BRepMesh_IncrementalMesh(shape.wrapped, 0.1, False, 0.5, True)
    all_vertices: list[np.ndarray] = []
    all_triangles: list[np.ndarray] = []
    offset = 0
    explorer = TopExp_Explorer(shape.wrapped, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Value())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            raise ValueError("Solid face could not be tessellated for alignment")
        transform = location.Transformation()
        node_count, triangle_count = triangulation.NbNodes(), triangulation.NbTriangles()
        vertices = np.empty((node_count, 3), dtype=np.float64)
        for index in range(1, node_count + 1):
            point = triangulation.Node(index).Transformed(transform)
            vertices[index - 1] = (point.X(), point.Y(), point.Z())
        triangles = np.empty((triangle_count, 3), dtype=np.int32)
        for index in range(1, triangle_count + 1):
            first, second, third = triangulation.Triangle(index).Get()
            triangles[index - 1] = (first - 1 + offset, second - 1 + offset, third - 1 + offset)
        all_vertices.append(vertices)
        all_triangles.append(triangles)
        offset += node_count
        explorer.Next()
    if not all_vertices:
        raise ValueError("STEP solid has no surface mesh for alignment")
    vertices, triangles = np.vstack(all_vertices), np.vstack(all_triangles)
    first, second, third = (vertices[triangles[:, index]] for index in range(3))
    areas = 0.5 * np.linalg.norm(np.cross(second - first, third - first), axis=1)
    total_area = float(areas.sum())
    if not np.isfinite(total_area) or total_area <= 0.0:
        raise ValueError("STEP solid must have positive finite surface area")
    generator = np.random.default_rng(seed)
    chosen = generator.choice(len(triangles), size=n_points, p=areas / total_area)
    r1, r2 = generator.random(n_points), generator.random(n_points)
    sqrt_r1 = np.sqrt(r1)
    u, v, w = 1.0 - sqrt_r1, sqrt_r1 * (1.0 - r2), sqrt_r1 * r2
    return u[:, None] * first[chosen] + v[:, None] * second[chosen] + w[:, None] * third[chosen]
