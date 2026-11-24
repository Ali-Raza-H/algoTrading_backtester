from __future__ import annotations

import random
from datetime import datetime, timedelta


def sample_windows(
    *,
    population_start: datetime,
    population_end: datetime,
    duration: timedelta,
    count: int,
    seed: int,
    non_overlap: bool = False,
) -> tuple[tuple[datetime, datetime], ...]:
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    latest = population_end - duration
    if latest < population_start:
        raise ValueError("population is shorter than requested window")
    if count < 1:
        raise ValueError("count must be positive")
    possible_seconds = int((latest - population_start).total_seconds())
    rng = random.Random(seed)
    windows: list[tuple[datetime, datetime]] = []
    attempts = 0
    maximum_attempts = max(1_000, count * 1_000)
    while len(windows) < count and attempts < maximum_attempts:
        offset = rng.randint(0, possible_seconds) if possible_seconds else 0
        start = population_start + timedelta(seconds=offset)
        end = start + duration
        attempts += 1
        if non_overlap and any(
            start < existing_end and end > existing_start
            for existing_start, existing_end in windows
        ):
            continue
        windows.append((start, end))
    if len(windows) != count:
        raise ValueError("could not sample requested non-overlapping windows")
    return tuple(windows)
