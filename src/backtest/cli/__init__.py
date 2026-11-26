from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from backtest import __version__
from backtest.benchmarks.runner import run_benchmarks
from backtest.config import ExperimentConfig, ImportConfig
from backtest.data import import_csv, load_manifest
from backtest.data.dataset import validate_dataset
from backtest.domain.errors import (
    BacktestError,
    ConfigError,
    DataError,
    ReportError,
    SimulationError,
    StrategyError,
)
from backtest.engine import execute_run, validate_run
from backtest.experiments.batch import WindowBatchConfig, is_window_batch
from backtest.reporting import write_run
from backtest.reporting.writer import list_runs

EXIT_CODES = {
    ConfigError: 2,
    DataError: 3,
    StrategyError: 4,
    SimulationError: 5,
    ReportError: 6,
}


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backtest", description="Historical market simulator")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    data = commands.add_parser("data", help="import and inspect market data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    data_import = data_commands.add_parser("import", help="normalize a CSV dataset")
    data_import.add_argument("--config", type=Path, required=True)
    for name in ("validate", "inspect"):
        item = data_commands.add_parser(name, help=f"{name} a dataset")
        item.add_argument("--dataset", type=Path, required=True)

    strategy = commands.add_parser("strategy", help="validate a strategy configuration")
    strategy_commands = strategy.add_subparsers(dest="strategy_command", required=True)
    strategy_validate = strategy_commands.add_parser("validate")
    strategy_validate.add_argument("--config", type=Path, required=True)

    run = commands.add_parser("run", help="run one experiment")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")

    batch = commands.add_parser("batch", help="run each experiment config in order")
    batch.add_argument("--config", type=Path, action="append", required=True)

    replay = commands.add_parser("replay", help="print recorded execution events")
    replay.add_argument("--run", type=Path, required=True)
    replay.add_argument("--step", action="store_true")

    report = commands.add_parser("report", help="show a stored run summary")
    report.add_argument("--run", type=Path, required=True)

    compare = commands.add_parser("compare", help="compare stored run summaries")
    compare.add_argument("--runs", type=Path, nargs="+", required=True)

    listing = commands.add_parser("list-runs", help="list indexed runs")
    listing.add_argument("--directory", type=Path, default=Path("runs"))
    return parser


def _run_with_progress(config: ExperimentConfig) -> tuple[Any, Path]:
    print(
        f"Starting simulation: {config.start.isoformat()} to {config.end.isoformat()}",
        file=sys.stderr, flush=True,
    )
    last_update = time.monotonic()

    def progress(count: int, timestamp: datetime) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update >= 2:
            print(
                f"Simulating: {count:,} bars, {timestamp.isoformat()}",
                file=sys.stderr, flush=True,
            )
            last_update = now

    result = execute_run(config, progress=progress)
    print(f"Simulation complete: {result.source_bars:,} bars", file=sys.stderr, flush=True)
    if config.benchmark.enabled:
        print("Running benchmarks...", file=sys.stderr, flush=True)
    def benchmark_progress(count: int, total: int) -> None:
        print(f"Benchmark trials: {count}/{total}", file=sys.stderr, flush=True)

    run_benchmarks(result, progress=benchmark_progress)
    print("Writing results...", file=sys.stderr, flush=True)
    return result, write_run(result)


def _execute(args: argparse.Namespace) -> int:
    if args.command == "data":
        if args.data_command == "import":
            manifest = import_csv(ImportConfig.from_file(args.config.resolve()))
            _json(manifest.to_dict())
        elif args.data_command == "validate":
            _json(validate_dataset(args.dataset.resolve()))
        else:
            _json(load_manifest(args.dataset.resolve()).to_dict())
        return 0
    if args.command == "strategy":
        _json(validate_run(ExperimentConfig.from_file(args.config.resolve())))
        return 0
    if args.command == "run":
        config = ExperimentConfig.from_file(args.config.resolve())
        if args.dry_run:
            _json(validate_run(config))
            return 0
        result, run_dir = _run_with_progress(config)
        _json({"run_id": result.run_id, "status": result.status, "directory": str(run_dir)})
        return 0
    if args.command == "batch":
        outcomes = []
        failed = False
        for item in args.config:
            configs = (
                WindowBatchConfig.from_file(item.resolve()).experiments()
                if is_window_batch(item.resolve())
                else (ExperimentConfig.from_file(item.resolve()),)
            )
            for config in configs:
                try:
                    print(f"Batch config: {item}", file=sys.stderr, flush=True)
                    result, directory = _run_with_progress(config)
                    outcomes.append(
                        {
                            "config": str(item),
                            "window": [config.start.isoformat(), config.end.isoformat()],
                            "seed": config.seed,
                            "run_id": result.run_id,
                            "directory": str(directory),
                        }
                    )
                except BacktestError as exc:
                    failed = True
                    outcomes.append({"config": str(item), "error": str(exc)})
        _json(outcomes)
        return 5 if failed else 0
    if args.command == "replay":
        events_path = args.run.resolve() / "events.parquet"
        rows = pq.read_table(events_path).to_pylist()
        for row in rows:
            print(json.dumps(row, default=str, sort_keys=True))
            if args.step and sys.stdin.isatty():
                input()
        return 0
    if args.command == "report":
        _json(json.loads((args.run.resolve() / "summary.json").read_text(encoding="utf-8")))
        return 0
    if args.command == "compare":
        summaries = []
        for run in args.runs:
            summary = json.loads((run.resolve() / "summary.json").read_text(encoding="utf-8"))
            summaries.append(
                {
                    "run_id": summary["run_id"],
                    "net_return_percent": summary["net_return_percent"],
                    "max_drawdown_percent": summary["max_drawdown_percent"],
                    "closed_trades": summary["closed_trades"],
                }
            )
        _json(summaries)
        return 0
    if args.command == "list-runs":
        _json(list_runs(args.directory.resolve()))
        return 0
    raise ConfigError("unknown command")


def main(argv: list[str] | None = None) -> int:
    try:
        return _execute(_parser().parse_args(argv))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BacktestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return next((code for error, code in EXIT_CODES.items() if isinstance(exc, error)), 1)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


__all__ = ["main"]
