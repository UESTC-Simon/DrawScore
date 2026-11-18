"""Exercise the public CLI using generated STEP geometry."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, Rot, export_step


@pytest.fixture(scope="module")
def models(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Create small models that exercise registration and visible/hidden curves."""
    directory = tmp_path_factory.mktemp("step-models")
    reference = Box(12, 18, 24) - Pos(2, 3, 0) * Cylinder(2, 30)
    shapes = {
        "reference": reference,
        "transformed": Pos(35, -12, 6) * Rot(0, 0, 90) * reference,
        "different": Cylinder(8, 24),
    }
    paths = {name: directory / f"{name}.step" for name in shapes}
    for name, shape in shapes.items():
        export_step(shape, paths[name])
    return paths


def run_cli(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    """Run the module entry point with a bounded subprocess lifetime."""
    environment = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    return subprocess.run(
        [sys.executable, "-m", "drawscore", *(str(argument) for argument in arguments)],
        capture_output=True,
        text=True,
        timeout=90,
        env=environment,
        check=False,
    )


@pytest.mark.parametrize("candidate, minimum", [("reference", 0.999999), ("transformed", 0.95)])
def test_cli_recognizes_identical_geometry(
    models: dict[str, Path], candidate: str, minimum: float
) -> None:
    outcome = run_cli(models["reference"], models[candidate], "--tolerance", "0.005")
    assert outcome.returncode == 0, outcome.stderr
    assert minimum <= float(outcome.stdout) <= 1.0


def test_cli_scores_different_geometry_and_uses_tolerance(models: dict[str, Path]) -> None:
    scores = []
    for tolerance in ("0.005", "0.02"):
        outcome = run_cli(models["reference"], models["different"], "--tolerance", tolerance)
        assert outcome.returncode == 0, outcome.stderr
        scores.append(float(outcome.stdout))
    assert 0.0 <= scores[0] < scores[1] < 1.0


def test_cli_reports_invalid_step(tmp_path: Path, models: dict[str, Path]) -> None:
    broken = tmp_path / "broken.step"
    broken.write_text("not a STEP file", encoding="utf-8")
    outcome = run_cli(models["reference"], broken, "--tolerance", "0.005")
    assert outcome.returncode != 0
    assert outcome.stdout == ""
    assert str(broken) in outcome.stderr


def test_cli_enforces_scoring_deadline(models: dict[str, Path]) -> None:
    outcome = run_cli(models["reference"], models["reference"], "--timeout", "0.001")
    assert outcome.returncode != 0
    assert "timeout" in outcome.stderr.lower() or "exceeded" in outcome.stderr.lower()


@pytest.mark.parametrize("tolerance", ["nan", "inf", "0", "-0.1", "1"])
def test_api_rejects_invalid_tolerance(models: dict[str, Path], tolerance: str) -> None:
    from drawscore import score_drawscore

    with pytest.raises(ValueError):
        score_drawscore(models["reference"], models["reference"], tolerance=float(tolerance))


def test_api_reports_missing_step(tmp_path: Path, models: dict[str, Path]) -> None:
    from drawscore import score_drawscore

    with pytest.raises(FileNotFoundError):
        score_drawscore(models["reference"], tmp_path / "missing.step", tolerance=0.005)
