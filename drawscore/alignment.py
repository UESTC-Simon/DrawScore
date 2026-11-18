# Copyright 2026 Hugging Face
# SPDX-License-Identifier: Apache-2.0
# Adapted from CADGenBench alignment; see NOTICE for provenance and changes.
"""Rigid PCA and multiscale ICP registration for DrawScore."""

import itertools
from typing import TYPE_CHECKING

import numpy as np
from build123d.topology import Compound
from scipy.spatial import cKDTree  # pyright: ignore[reportAttributeAccessIssue]

from drawscore.sampling import sample_shape

if TYPE_CHECKING:
    import open3d as o3d

type ICPLevel = tuple[o3d.geometry.PointCloud, o3d.geometry.PointCloud, float, int]

_OPEN3D_ICP_VOXEL_DIVISOR = 150.0
_TIE_F1_TOLERANCE = 0.005
_TIE_CHAMFER_FRACTION = 0.005
_OCTAHEDRAL_CANDIDATES: list[np.ndarray] = []


def _build_octahedral_candidates() -> list[np.ndarray]:
    """Return the 24 rotation matrices in the octahedral symmetry group."""
    if _OCTAHEDRAL_CANDIDATES:
        return _OCTAHEDRAL_CANDIDATES

    perms = list(itertools.permutations(range(3)))
    for perm in perms:
        for signs in itertools.product((-1, 1), repeat=3):
            rotation = np.zeros((3, 3))
            for col, (row, sign) in enumerate(zip(perm, signs, strict=True)):
                rotation[row, col] = sign
            # Keep only proper rotations (det = +1)
            if np.linalg.det(rotation) > 0:
                _OCTAHEDRAL_CANDIDATES.append(rotation)

    return _OCTAHEDRAL_CANDIDATES


def _pca_align_candidates(
    src_pts: np.ndarray,
    tgt_pts: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return all 24 octahedral PCA candidates sorted by mean closest-point distance.

    Each entry is ``(rotation, t)`` such that ``rotation @ src + t ≈ tgt`` (approximately).
    The list is ordered best-first so callers can take ``[:k]`` for multi-start ICP.
    """
    src_center = src_pts.mean(axis=0)
    tgt_center = tgt_pts.mean(axis=0)

    src_c = src_pts - src_center
    tgt_c = tgt_pts - tgt_center

    src_axes = _principal_axes(src_c)
    tgt_axes = _principal_axes(tgt_c)

    candidates = _build_octahedral_candidates()
    tgt_tree = cKDTree(tgt_c)

    scored: list[tuple[float, np.ndarray, np.ndarray]] = []
    for orientation in candidates:
        rotation_candidate = tgt_axes @ orientation @ src_axes.T
        transformed = (rotation_candidate @ src_c.T).T
        dists, _ = tgt_tree.query(transformed)
        cost = dists.mean()
        t_cand = tgt_center - rotation_candidate @ src_center
        scored.append((cost, rotation_candidate, t_cand))

    scored.sort(key=lambda x: x[0])
    return [(rotation, t) for _, rotation, t in scored]


def _principal_axes(centered: np.ndarray) -> np.ndarray:
    """Return (3, 3) matrix whose columns are PCA eigenvectors (descending)."""
    cov = (centered.T @ centered) / len(centered)
    eigvals, eigvecs = np.linalg.eigh(cov)
    # eigh returns ascending order; flip to descending
    idx = np.argsort(eigvals)[::-1]
    axes = eigvecs[:, idx]
    # Ensure right-handed frame
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    return axes


def _prepare_levels(source: np.ndarray, target: np.ndarray, max_iter: int) -> list[ICPLevel]:
    import open3d as o3d

    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(source, dtype=np.float64)))
    tgt = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(target, dtype=np.float64)))
    diagonal = float(np.linalg.norm(np.ptp(target, axis=0)))
    voxel = max(diagonal / _OPEN3D_ICP_VOXEL_DIVISOR, 1e-6)
    iterations = np.maximum(1, np.rint(np.asarray([0.50, 0.30, 0.20]) * max_iter).astype(int))
    iterations[-1] += max(0, max_iter - int(iterations.sum()))
    levels = []
    for scale, count in zip((voxel * 4.0, voxel * 2.0, voxel), iterations, strict=True):
        src_level = src.voxel_down_sample(float(scale))
        tgt_level = tgt.voxel_down_sample(float(scale))
        for cloud in (src_level, tgt_level):
            cloud.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=30))
            cloud.normalize_normals()
        levels.append((src_level, tgt_level, float(scale), int(count)))
    return levels


def _refine_pose(
    levels: list[ICPLevel], rotation: np.ndarray, translation: np.ndarray, tolerance: float
) -> tuple[np.ndarray, np.ndarray]:
    import open3d as o3d

    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation
    for source, target, scale, iterations in levels:
        result = o3d.pipelines.registration.registration_icp(
            source,
            target,
            max_correspondence_distance=scale * 2.0,
            init=transform,
            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(
                o3d.pipelines.registration.TukeyLoss(k=scale)
            ),
            criteria=o3d.pipelines.registration.ICPConvergenceCriteria(
                relative_fitness=tolerance, relative_rmse=tolerance, max_iteration=iterations
            ),
        )
        transform = np.asarray(result.transformation, dtype=np.float64)
    left, _, right = np.linalg.svd(transform[:3, :3])
    rotation = left @ right
    if np.linalg.det(rotation) < 0:
        left[:, -1] *= -1
        rotation = left @ right
    return rotation, np.asarray(transform[:3, 3], dtype=np.float64)


class _ShapeAgreement:
    def __init__(self, source: np.ndarray, target: np.ndarray) -> None:
        self.source = source
        self.target = target
        self.target_tree = cKDTree(target)
        self.diagonal = float(np.linalg.norm(np.ptp(target, axis=0)))
        self.threshold = max(1e-6, 0.01 * self.diagonal)
        self.cap = max(1e-6, 0.02 * self.diagonal)

    def __call__(self, rotation: np.ndarray, translation: np.ndarray) -> tuple[float, float]:
        transformed = (rotation @ self.source.T).T + translation
        source_tree = cKDTree(transformed)
        source_distances, _ = self.target_tree.query(transformed)
        target_distances, _ = source_tree.query(self.target)
        precision = float((source_distances <= self.threshold).mean())
        recall = float((target_distances <= self.threshold).mean())
        f1 = 0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
        chamfer = float(
            (
                np.minimum(source_distances, self.cap).mean()
                + np.minimum(target_distances, self.cap).mean()
            )
            / 2.0
        )
        return f1, chamfer


def align_points(
    source: np.ndarray,
    target: np.ndarray,
    icp_max_iter: int = 50,
    icp_tolerance: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate the original candidate set while reusing immutable ICP preparation."""
    candidates = [
        (np.eye(3, dtype=np.float64), np.zeros(3, dtype=np.float64)),
        *_pca_align_candidates(source, target),
    ]
    levels = _prepare_levels(source, target, icp_max_iter)
    agreement = _ShapeAgreement(source, target)
    scored = []
    for rotation, translation in candidates:
        refined_rotation, refined_translation = _refine_pose(
            levels, rotation, translation, icp_tolerance
        )
        for r, t in ((rotation, translation), (refined_rotation, refined_translation)):
            f1, chamfer = agreement(r, t)
            scored.append(
                (f1, chamfer, float(np.arccos(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0))), r, t)
            )
    best_f1 = max(row[0] for row in scored)
    f1_ties = [row for row in scored if row[0] >= best_f1 - _TIE_F1_TOLERANCE]
    minimum_chamfer = min(row[1] for row in f1_ties)
    ties = [
        row
        for row in f1_ties
        if row[1] <= minimum_chamfer + _TIE_CHAMFER_FRACTION * agreement.diagonal
    ]
    ties.sort(key=lambda row: (row[2], row[1]))
    _, _, _, rotation, translation = ties[0]
    return rotation, translation


def align_shapes(
    source: Compound, target: Compound, n_samples: int, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Align sampled solid surfaces without exporting an intermediate STEP file."""
    source_points = sample_shape(source, n_samples, seed)
    target_points = sample_shape(target, n_samples, seed + 1)
    return align_points(source_points, target_points)
