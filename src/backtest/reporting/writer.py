from __future__ import annotations

import hashlib
import html
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from backtest import __version__
from backtest.analytics import calculate_metrics
from backtest.domain.errors import ReportError
from backtest.engine import RunResult


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(_json_value(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _rows(items: list[Any]) -> list[dict[str, Any]]:
    return [_json_value(item) for item in items]


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        pq.write_table(pa.table({"empty": pa.array([], type=pa.string())}), path)
        return
    try:
        pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")
    except pa.ArrowException as exc:
        raise ReportError(f"cannot write {path.name}: {exc}") from exc


def _equity_svg(result: RunResult) -> str:
    values = [float(item.equity) for item in result.portfolio.snapshots]
    if len(values) < 2:
        return "<p>Insufficient equity samples for a curve.</p>"
    low, high = min(values), max(values)
    spread = high - low or 1.0
    points = []
    for index, value in enumerate(values):
        x = index / (len(values) - 1) * 760 + 20
        y = 180 - ((value - low) / spread * 150)
        points.append(f"{x:.1f},{y:.1f}")
    return (
        '<svg viewBox="0 0 800 200" role="img" aria-label="Equity curve">'
        '<rect width="800" height="200" fill="#101820"/>'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#31c48d" stroke-width="2"/>'
        "</svg>"
    )


def _html(result: RunResult, summary: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in summary.items()
        if not isinstance(value, dict)
    )
    warnings = "".join(f"<li>{html.escape(item)}</li>" for item in result.warnings)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Backtest report: {html.escape(result.run_id)}</title>
<style>
body{{font:15px system-ui;margin:2rem;max-width:1000px;color:#17202a}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}}
th{{width:40%}}svg{{width:100%;height:auto}}code{{background:#eee;padding:.15rem}}
</style></head>
<body><h1>Backtest report</h1><p><code>{html.escape(result.run_id)}</code></p>
<h2>Summary</h2><table>{rows}</table><h2>Equity</h2>{_equity_svg(result)}
<h2>Warnings</h2><ul>{warnings or "<li>None</li>"}</ul>
<p>Execution is modeled from historical candles and is not evidence of future performance.</p>
</body></html>"""


def _update_index(root: Path, result: RunResult, summary: dict[str, Any], run_dir: Path) -> None:
    database = root / "runs.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL, strategy_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
            net_return REAL, path TEXT NOT NULL)"""
        )
        connection.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.run_id,
                result.status,
                result.started_at.isoformat(),
                result.finished_at.isoformat(),
                result.config.strategy.strategy_id,
                result.dataset_id,
                summary.get("net_return_percent"),
                str(run_dir),
            ),
        )
        connection.commit()


def write_run(result: RunResult) -> Path:
    root = result.config.output
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / result.run_id
    run_dir.mkdir(parents=False, exist_ok=False)
    summary = calculate_metrics(result)
    resolved = _json_value(result.config)
    config_bytes = json.dumps(resolved, sort_keys=True).encode()
    manifest = {
        "schema_version": 1,
        "run_id": result.run_id,
        "status": result.status,
        "engine_version": __version__,
        "python": __import__("sys").version,
        "dataset_id": result.dataset_id,
        "strategy_id": result.config.strategy.strategy_id,
        "strategy_version": result.config.strategy.version,
        "seed": result.config.seed,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }
    _write_json(run_dir / "resolved-config.json", resolved)
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "warnings.json", result.warnings)
    _write_parquet(run_dir / "orders.parquet", _rows(list(result.broker.orders.values())))
    _write_parquet(run_dir / "fills.parquet", _rows(result.broker.fills))
    _write_parquet(run_dir / "trades.parquet", _rows(result.portfolio.trades))
    _write_parquet(run_dir / "ledger.parquet", _rows(result.portfolio.ledger))
    _write_parquet(run_dir / "equity.parquet", _rows(result.portfolio.snapshots))
    _write_parquet(run_dir / "events.parquet", result.broker.events)
    _write_parquet(run_dir / "benchmarks.parquet", result.benchmarks)
    (run_dir / "report.html").write_text(_html(result, summary), encoding="utf-8")
    _update_index(root, result, summary, run_dir)
    return run_dir


def list_runs(root: Path) -> list[dict[str, Any]]:
    database = root / "runs.sqlite3"
    if not database.exists():
        return []
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
    return [dict(row) for row in rows]
