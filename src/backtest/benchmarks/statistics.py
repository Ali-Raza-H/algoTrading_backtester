from __future__ import annotations

from statistics import mean, median
from typing import Any


def empirical_comparison(observed: float, samples: list[float]) -> dict[str, Any]:
    if not samples:
        raise ValueError("at least one benchmark sample is required")
    ordered = sorted(samples)

    def quantile(fraction: float) -> float:
        index = round((len(ordered) - 1) * fraction)
        return ordered[index]

    below = sum(value < observed for value in samples)
    ties = sum(value == observed for value in samples)
    exceed = sum(value >= observed for value in samples)
    return {
        "observed": observed,
        "sample_size": len(samples),
        "mean": mean(samples),
        "median": median(samples),
        "quantiles": {
            "5": quantile(0.05),
            "25": quantile(0.25),
            "75": quantile(0.75),
            "95": quantile(0.95),
        },
        "percentile": (below + 0.5 * ties) / len(samples) * 100,
        "tail_estimate": (1 + exceed) / (len(samples) + 1),
    }
