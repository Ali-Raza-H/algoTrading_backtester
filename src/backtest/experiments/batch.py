from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from backtest.config import (
    ExperimentConfig,
    _datetime,
    _path,
    _required,
    _strict,
    load_yaml,
)
from backtest.experiments.windows import sample_windows


@dataclass(frozen=True, slots=True)
class WindowBatchConfig:
    base: ExperimentConfig
    population_start: datetime
    population_end: datetime
    duration: timedelta
    count: int
    seed: int
    non_overlap: bool

    @classmethod
    def from_file(cls, path: Path) -> WindowBatchConfig:
        root = load_yaml(path)
        keys = {
            "schema_version",
            "base_config",
            "population_start",
            "population_end",
            "duration_days",
            "count",
            "seed",
            "non_overlap",
        }
        _strict(root, keys, "window_batch")
        _required(root, keys, "window_batch")
        if root["schema_version"] != 1:
            raise ValueError("only window batch schema version 1 is supported")
        population_start = _datetime(root["population_start"], "population_start")
        population_end = _datetime(root["population_end"], "population_end")
        duration = timedelta(days=float(root["duration_days"]))
        count = int(root["count"])
        return cls(
            base=ExperimentConfig.from_file(_path(path.parent, root["base_config"], "base_config")),
            population_start=population_start,
            population_end=population_end,
            duration=duration,
            count=count,
            seed=int(root["seed"]),
            non_overlap=bool(root["non_overlap"]),
        )

    def experiments(self) -> tuple[ExperimentConfig, ...]:
        windows = sample_windows(
            population_start=self.population_start,
            population_end=self.population_end,
            duration=self.duration,
            count=self.count,
            seed=self.seed,
            non_overlap=self.non_overlap,
        )
        configs = []
        for index, (start, end) in enumerate(windows):
            material = f"{self.seed}|window|{index}|{start.isoformat()}".encode()
            child_seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            configs.append(replace(self.base, start=start, end=end, seed=child_seed))
        return tuple(configs)


def is_window_batch(path: Path) -> bool:
    root = load_yaml(path)
    return "base_config" in root
