"""Orthographic hidden-line projection with shared angular cameras."""

import math
from pathlib import Path

import numpy as np
from build123d import GeomType
from build123d.importers import import_step
from build123d.topology import Compound, Edge
from build123d.topology.three_d import Mixin3D
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform  # type: ignore[attr-defined]
from OCP.gp import gp_Trsf  # type: ignore[attr-defined]

from drawscore.model import Curve, DrawingSet, MetricConfig, Visibility

PROJECTION_DIRECTIONS = {
    "front": (0.0, -1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "right": (1.0, 0.0, 0.0),
}
_PROJECTION_UP = {
    "front": (0.0, 0.0, 1.0),
    "top": (0.0, 1.0, 0.0),
    "right": (0.0, 0.0, 1.0),
}
_ANGULAR_SAMPLE_COUNT = 8


def load_step_shape(path: str | Path) -> Compound:
    """Load the solid geometry from a STEP model."""

    try:
        solids = import_step(path).solids()
    except (AssertionError, RuntimeError, ValueError) as error:
        raise ValueError(f"Could not load STEP geometry from {path}") from error
    if not solids:
        raise ValueError(f"STEP file contains no solid geometry: {path}")
    return Compound(solids)


def validate_rigid_transform(matrix: np.ndarray) -> np.ndarray:
    """Validate the proper rigid transform returned by registration."""

    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4):
        raise ValueError("Rigid transform must be a 4x4 matrix")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1.0e-7):
        raise ValueError("Transform contains scale or shear")
    if not np.isclose(np.linalg.det(rotation), 1.0, atol=1.0e-7):
        raise ValueError("Transform contains a reflection and is not in SE(3)")
    if not np.allclose(matrix[3], (0.0, 0.0, 0.0, 1.0), atol=1.0e-9):
        raise ValueError("Transform has an invalid homogeneous-coordinate row")
    return matrix


def transformed_shape(shape: Mixin3D, matrix: np.ndarray) -> Compound:
    """Return a rigidly transformed build123d shape."""

    rigid = validate_rigid_transform(matrix)
    transform = gp_Trsf()
    transform.SetValues(*(float(value) for value in rigid[:3, :].ravel()))
    builder = BRepBuilderAPI_Transform(shape.wrapped, transform, True)
    builder.Build()
    return Compound(builder.Shape())


def bounding_box_diagonal(shape: Mixin3D) -> float:
    """Return the positive finite diagonal used to normalize distances."""
    diagonal = float(shape.bounding_box().diagonal)
    if not math.isfinite(diagonal) or diagonal <= 0:
        raise ValueError("Shape has a degenerate bounding box")
    return diagonal


def _edge_points(
    edge: Edge,
    normalization_length: float,
    config: MetricConfig,
) -> tuple[tuple[float, float], ...]:
    if edge.geom_type is GeomType.LINE:
        vectors = (edge.start_point(), edge.end_point())
    else:
        deflection = max(normalization_length * config.sigma_fraction / 40.0, 1.0e-8)
        vectors = edge.positions(deflection=deflection)
    points: list[tuple[float, float]] = []
    for vector in vectors:
        point = (float(vector.X), float(vector.Y))
        if not points or point != points[-1]:
            points.append(point)
    if len(points) < 2:
        raise ValueError("Projected edge produced fewer than two polyline points")
    return tuple(points)


def _project_shape_view(
    shape: Mixin3D,
    view: str,
    normalization_length: float,
    config: MetricConfig,
    angular_offset: tuple[float, float],
    look_at: tuple[float, float, float],
) -> tuple[Curve, ...]:
    direction = np.asarray(PROJECTION_DIRECTIONS[view], dtype=float)
    up = np.asarray(_PROJECTION_UP[view], dtype=float)
    alpha, beta = angular_offset
    if alpha != 0.0 or beta != 0.0:
        right = np.cross(up, direction)
        right /= np.linalg.norm(right)
        rotation_vector = alpha * right + beta * up
        angle = float(np.linalg.norm(rotation_vector))
        axis = rotation_vector / angle
        direction = (
            direction * math.cos(angle)
            + np.cross(axis, direction) * math.sin(angle)
            + axis * float(axis @ direction) * (1.0 - math.cos(angle))
        )
        up = up - float(up @ direction) * direction
        up /= np.linalg.norm(up)

    viewport_origin = np.asarray(look_at, dtype=float) + normalization_length * direction
    visible_edges, hidden_edges = shape.project_to_viewport(
        tuple(float(value) for value in viewport_origin),
        tuple(float(value) for value in up),
        look_at=look_at,
    )
    raw: list[Curve] = []
    for visibility, edges in (
        (Visibility.VISIBLE, visible_edges),
        (Visibility.HIDDEN, hidden_edges),
    ):
        for edge in edges:
            raw.append(
                Curve(
                    points=_edge_points(edge, normalization_length, config),
                    visibility=visibility,
                )
            )
    return tuple(raw)


def project_shape_angular(
    shape: Mixin3D,
    normalization_length: float,
    config: MetricConfig,
    look_at: tuple[float, float, float],
) -> tuple[DrawingSet, ...]:
    """Project eight symmetric angular samples around the three standard views."""
    # A point within L/2 of the view center moves by at most L sin(theta/2).
    angular_radius = 2.0 * math.asin(config.sigma_fraction)
    # The RMS radius of a uniform disk gives a one-ring area quadrature.
    sample_radius = angular_radius / math.sqrt(2.0)
    samples = []
    for index in range(_ANGULAR_SAMPLE_COUNT):
        phase = 2.0 * math.pi * index / _ANGULAR_SAMPLE_COUNT
        alpha, beta = sample_radius * math.cos(phase), sample_radius * math.sin(phase)
        views = {
            view: _project_shape_view(
                shape, view, normalization_length, config, (alpha, beta), look_at
            )
            for view in PROJECTION_DIRECTIONS
        }
        samples.append(DrawingSet(views))
    return tuple(samples)
