from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from backtest.benchmarks.runner import run_benchmarks
from backtest.config import ExperimentConfig, ImportConfig
from backtest.data import import_csv
from backtest.engine import execute_run, validate_run
from backtest.reporting import write_run
from tests.unit.test_config import valid_config
from tests.unit.test_data import import_yaml


def test_import_run_and_report_are_reproducible(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    lines = ["time,open,high,low,close"]
    for minute in range(301):
        timestamp = start + timedelta(minutes=minute)
        price = 2000 + (minute // 60) * 2 + (minute % 7) / 100
        lines.append(
            f"{timestamp.isoformat()},{price:.2f},{price + 0.10:.2f},{price - 0.10:.2f},{price:.2f}"
        )
    source.write_text("\n".join(lines) + "\n")
    dataset = tmp_path / "dataset"
    import_path = tmp_path / "import.yaml"
    import_path.write_text(import_yaml(source, dataset))
    import_csv(ImportConfig.from_file(import_path))

    experiment_path = tmp_path / "experiment.yaml"
    experiment_path.write_text(valid_config(dataset / "manifest.json"))
    config = ExperimentConfig.from_file(experiment_path)
    validation = validate_run(config)
    assert validation["valid"] is True

    result = execute_run(config)
    assert result.status == "completed"
    assert result.source_bars == 180
    run_dir = write_run(result)
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["run_id"] == result.run_id
    assert summary["status"] == "completed"
    assert (run_dir / "report.html").read_text().startswith("<!doctype html>")
    assert pq.read_table(run_dir / "equity.parquet").num_rows >= 1

    benchmarked = execute_run(
        replace(config, benchmark=replace(config.benchmark, enabled=True, trials=2))
    )
    run_benchmarks(benchmarked)
    assert len(benchmarked.benchmarks) == 3
    assert benchmarked.benchmark_comparison is not None
    # The deterministic seed is an unsigned 64-bit integer, potentially above int64.
    benchmarked.benchmarks[1]["seed"] = 2**64 - 1
    benchmark_dir = write_run(benchmarked)
    rows = pq.read_table(benchmark_dir / "benchmarks.parquet").to_pylist()
    assert rows[0]["seed"] is None
    assert rows[1]["seed"] == 2**64 - 1
