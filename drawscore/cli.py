"""Command-line interface for comparing two STEP models."""

import argparse
from pathlib import Path

from drawscore import score_drawscore


def main(argv: list[str] | None = None) -> int:
    """Print a DrawScore, or exit with an error when comparison fails."""
    parser = argparse.ArgumentParser(
        prog="drawscore", description="Compare two STEP models and print DrawScore in [0, 1]."
    )
    parser.add_argument("reference", type=Path, help="reference STEP file")
    parser.add_argument("candidate", type=Path, help="candidate STEP file")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.005,
        help="Gaussian distance scale / reference bounding-box diagonal (default: 0.005)",
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="scoring deadline in seconds (default: 60)"
    )
    args = parser.parse_args(argv)
    try:
        score = score_drawscore(
            args.reference, args.candidate, tolerance=args.tolerance, timeout=args.timeout
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"drawscore: {error}\n")
    print(f"{score:.10f}")
    return 0
