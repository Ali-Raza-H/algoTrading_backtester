from datetime import UTC, datetime, timedelta
from pathlib import Path

from backtest.benchmarks import empirical_comparison
from backtest.experiments import WindowBatchConfig, sample_windows
from tests.unit.test_config import valid_config


def test_random_windows_are_reproducible_and_contiguous() -> None:
    arguments = {
        "population_start": datetime(2020, 1, 1, tzinfo=UTC),
        "population_end": datetime(2021, 1, 1, tzinfo=UTC),
        "duration": timedelta(days=30),
        "count": 5,
        "seed": 42,
    }
    first = sample_windows(**arguments)
    assert first == sample_windows(**arguments)
    assert all(end - start == timedelta(days=30) for start, end in first)


def test_empirical_statistics_include_tie_adjustment() -> None:
    result = empirical_comparison(2.0, [1.0, 2.0, 3.0])
    assert result["percentile"] == 50.0
    assert result["tail_estimate"] == 0.75


def test_window_batch_has_stable_child_seeds(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(valid_config(tmp_path / "manifest.json"))
    batch = tmp_path / "windows.yaml"
    batch.write_text(
        f"""schema_version: 1
base_config: {experiment}
population_start: '2024-01-01T00:00:00Z'
population_end: '2024-02-01T00:00:00Z'
duration_days: 5
count: 3
seed: 42
non_overlap: false
"""
    )
    first = WindowBatchConfig.from_file(batch).experiments()
    second = WindowBatchConfig.from_file(batch).experiments()
    assert [(item.start, item.end, item.seed) for item in first] == [
        (item.start, item.end, item.seed) for item in second
    ]
