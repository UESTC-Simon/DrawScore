"""Run rigid registration, projection, and scoring under a hard deadline."""

import itertools
import math
import multiprocessing
import os
from multiprocessing.connection import Connection
from pathlib import Path

import numpy as np
from build123d.topology.three_d import Mixin3D

from drawscore.alignment import align_shapes
from drawscore.metric import score_angular_drawing_pair
from drawscore.model import MetricConfig
from drawscore.projection import (
    bounding_box_diagonal,
    load_step_shape,
    project_shape_angular,
    transformed_shape,
)


def _repeated_inertia_axis(shape: Mixin3D) -> np.ndarray | None:
    moments, axes = np.linalg.eigh(np.asarray(shape.matrix_of_inertia, dtype=float))
    lower_equal = np.isclose(moments[0], moments[1], rtol=1.0e-6, atol=0.0)
    upper_equal = np.isclose(moments[1], moments[2], rtol=1.0e-6, atol=0.0)
    if lower_equal == upper_equal:
        return None
    return axes[:, 2] if lower_equal else axes[:, 0]


def _rotation_angle(rotation: np.ndarray) -> float:
    cosine = float(np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0))
    return math.acos(cosine)


def _canonicalize_rotation(
    rotation: np.ndarray,
    translation: np.ndarray,
    candidate_shape: Mixin3D,
    gt_shape: Mixin3D,
    maximum_displacement: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Remove registration noise bounded by the drawing distance scale."""

    center_vector = candidate_shape.center()
    center = np.asarray((center_vector.X, center_vector.Y, center_vector.Z), dtype=float)
    aligned_center = rotation @ center + translation

    candidate_axis = _repeated_inertia_axis(candidate_shape)
    gt_axis = _repeated_inertia_axis(gt_shape)
    if candidate_axis is not None and gt_axis is not None:
        transformed_axis = rotation @ candidate_axis
        if float(transformed_axis @ gt_axis) < 0.0:
            gt_axis = -gt_axis
        cross = np.cross(transformed_axis, gt_axis)
        sine = float(np.linalg.norm(cross))
        if sine > 1.0e-12:
            cosine = float(np.clip(transformed_axis @ gt_axis, -1.0, 1.0))
            skew = np.asarray(
                (
                    (0.0, -cross[2], cross[1]),
                    (cross[2], 0.0, -cross[0]),
                    (-cross[1], cross[0], 0.0),
                )
            )
            rotation = (np.eye(3) + skew + skew @ skew * ((1.0 - cosine) / sine**2)) @ rotation

    canonical_rotations: list[np.ndarray] = []
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1.0, 1.0), repeat=3):
            canonical = np.zeros((3, 3), dtype=float)
            for column, (row, sign) in enumerate(zip(permutation, signs, strict=True)):
                canonical[row, column] = sign
            if np.linalg.det(canonical) > 0:
                canonical_rotations.append(canonical)

    canonical = min(
        canonical_rotations,
        key=lambda candidate: _rotation_angle(candidate @ rotation.T),
    )
    angle = _rotation_angle(canonical @ rotation.T)
    diagonal = bounding_box_diagonal(candidate_shape)
    if 2.0 * diagonal * math.sin(angle / 2.0) <= maximum_displacement:
        rotation = canonical
    return rotation, aligned_center - rotation @ center


def _score_step_worker(
    connection: Connection, reference: Path, candidate: Path, config: MetricConfig
) -> None:
    # Native geometry libraries write directly to stdout's file descriptor.
    os.dup2(2, 1)
    try:
        reference_shape = load_step_shape(reference)
        candidate_shape = load_step_shape(candidate)
        length = bounding_box_diagonal(reference_shape)
        rotation, translation = align_shapes(
            candidate_shape, reference_shape, config.alignment_samples, config.random_seed
        )
        rotation, translation = _canonicalize_rotation(
            rotation,
            translation,
            candidate_shape,
            reference_shape,
            maximum_displacement=config.sigma_fraction * length,
        )
        transform = np.eye(4)
        transform[:3, :3], transform[:3, 3] = rotation, translation
        aligned_shape = transformed_shape(candidate_shape, transform)
        center = reference_shape.bounding_box().center()
        look_at = (float(center.X), float(center.Y), float(center.Z))
        reference_drawings = project_shape_angular(reference_shape, length, config, look_at)
        candidate_drawings = project_shape_angular(aligned_shape, length, config, look_at)
        score = score_angular_drawing_pair(
            reference_drawings, candidate_drawings, config.sigma_fraction * length
        )
        connection.send((score, None))
    except Exception as error:
        # Transfer failures across the process boundary instead of losing worker errors.
        connection.send((None, f"{type(error).__name__}: {error}"))
    finally:
        connection.close()


def score_step_pair(reference: Path, candidate: Path, config: MetricConfig) -> float:
    """Return the score, terminating and joining the worker on timeout or interruption."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_score_step_worker, args=(sender, reference, candidate, config)
    )
    try:
        process.start()
        sender.close()
        process.join(config.timeout_seconds)
        if process.is_alive():
            raise TimeoutError(f"STEP scoring exceeded {config.timeout_seconds:g} seconds")
        if process.exitcode != 0:
            raise RuntimeError(f"STEP scoring worker exited with code {process.exitcode}")
        try:
            score, error = receiver.recv()
        except EOFError as error:
            raise RuntimeError("STEP scoring worker returned no result") from error
        if error is not None:
            raise RuntimeError(f"STEP scoring failed: {error}")
        return score
    finally:
        if process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive():
                process.kill()
                process.join()
        receiver.close()
        sender.close()
        process.close()
