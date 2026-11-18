"""Internal drawing data used by the DrawScore workflow."""

from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import LineString

VIEW_NAMES = ("front", "top", "right")


class Visibility(StrEnum):
    """Line visibility in an orthographic drawing."""

    VISIBLE = "visible"
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class Curve:
    """A projected polyline in model coordinates."""

    points: tuple[tuple[float, float], ...]
    visibility: Visibility

    @property
    def geometry(self) -> LineString:
        """Return the polyline used for curve union and distance queries."""
        return LineString(self.points)


@dataclass(frozen=True, slots=True)
class DrawingSet:
    """Front, top, and right views at one angular offset."""

    views: dict[str, tuple[Curve, ...]]


@dataclass(frozen=True, slots=True)
class MetricConfig:
    """Validated distance scale and hard deadline for a STEP comparison."""

    sigma_fraction: float
    timeout_seconds: float
    alignment_samples: int = 10_000
    random_seed: int = 0
