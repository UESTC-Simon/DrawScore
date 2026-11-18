"""Visibility-aware Gaussian curve coverage with angular integration."""

import math
from collections.abc import Mapping, Sequence

import numpy as np
import shapely
from shapely.geometry import GeometryCollection
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from drawscore.model import VIEW_NAMES, Curve, DrawingSet, Visibility


def _curve_union(curves: tuple[Curve, ...], visibility: Visibility) -> BaseGeometry:
    lines = [curve.geometry for curve in curves if curve.visibility is visibility]
    return unary_union(lines) if lines else GeometryCollection()


def _sample_lines(geometry: BaseGeometry, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    samples: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for line in shapely.get_parts(geometry):
        points = np.asarray(line.coords, dtype=float)
        for start, end in zip(points[:-1], points[1:], strict=True):
            length = float(np.linalg.norm(end - start))
            if length == 0:
                continue
            count = max(1, math.ceil(length / spacing))
            positions = (np.arange(count, dtype=float) + 0.5) / count
            samples.append(start + positions[:, None] * (end - start))
            weights.append(np.full(count, length / count, dtype=float))
    if not samples:
        return np.empty((0, 2), dtype=float), np.empty(0, dtype=float)
    return np.concatenate(samples), np.concatenate(weights)


def _coverage(
    source: Mapping[Visibility, BaseGeometry],
    target: Mapping[Visibility, BaseGeometry],
    sigma: float,
) -> float:
    source_length = sum(float(geometry.length) for geometry in source.values())
    if source_length == 0:
        return 1.0
    weighted_similarity = 0.0
    for visibility in Visibility:
        source_geometry = source[visibility]
        if source_geometry.is_empty:
            continue
        points, weights = _sample_lines(source_geometry, sigma / 4.0)
        target_geometry = target[visibility]
        if not target_geometry.is_empty:
            distances = np.asarray(
                shapely.distance(shapely.points(points[:, 0], points[:, 1]), target_geometry),
                dtype=float,
            )
            weighted_similarity += float(
                np.sum(weights * np.exp(-0.5 * np.square(distances / sigma)))
            )
    # Weighted summation can round slightly above the exact curve length.
    return min(1.0, weighted_similarity / source_length)


def score_angular_drawing_pair(
    reference: Sequence[DrawingSet], candidate: Sequence[DrawingSet], sigma: float
) -> float:
    """Return minimum-view F-score after averaging angular precision and recall."""
    similarities = []
    for view in VIEW_NAMES:
        precisions, recalls = [], []
        for reference_sample, candidate_sample in zip(reference, candidate, strict=True):
            reference_geometry = {
                visibility: _curve_union(reference_sample.views[view], visibility)
                for visibility in Visibility
            }
            candidate_geometry = {
                visibility: _curve_union(candidate_sample.views[view], visibility)
                for visibility in Visibility
            }
            precisions.append(_coverage(candidate_geometry, reference_geometry, sigma))
            recalls.append(_coverage(reference_geometry, candidate_geometry, sigma))
        precision, recall = float(np.mean(precisions)), float(np.mean(recalls))
        similarity = (
            0.0 if precision + recall == 0.0 else 2.0 * precision * recall / (precision + recall)
        )
        similarities.append(similarity)
    return min(similarities)
