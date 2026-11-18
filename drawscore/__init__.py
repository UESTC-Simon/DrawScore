"""Score the drawing similarity of two STEP models."""

import math
from pathlib import Path

__all__ = ["score_drawscore"]


def score_drawscore(
    reference: str | Path,
    candidate: str | Path,
    *,
    tolerance: float = 0.005,
    timeout: float = 60.0,
) -> float:
    """Return DrawScore in [0, 1] after rigidly aligning the candidate.

    Args:
        reference: Reference STEP file.
        candidate: Candidate STEP file, in the same physical units as the reference.
        tolerance: Gaussian distance scale as a fraction of the reference bounding-box
            diagonal. Must be finite and strictly between zero and one.
        timeout: Positive, finite scoring deadline in seconds.

    Raises:
        ValueError: A scoring parameter is invalid.
        FileNotFoundError: An input file does not exist.
        RuntimeError: STEP geometry is invalid or the geometry worker fails.
        TimeoutError: Scoring exceeds the deadline.
    """
    if not math.isfinite(tolerance) or not 0.0 < tolerance < 1.0:
        raise ValueError("tolerance must be finite and strictly between 0 and 1")
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("timeout must be positive and finite")
    reference, candidate = Path(reference), Path(candidate)
    for path in (reference, candidate):
        if not path.is_file():
            raise FileNotFoundError(f"STEP file not found: {path}")

    from drawscore.model import MetricConfig
    from drawscore.step import score_step_pair

    return score_step_pair(reference, candidate, MetricConfig(tolerance, timeout))
