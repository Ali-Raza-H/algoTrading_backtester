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

# The self-contained HTML/CSS template intentionally keeps some long lines
# together so the generated document remains easy to scan in this module.
# ruff: noqa: E501


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


def _write_parquet(
    path: Path, rows: list[dict[str, Any]], *, schema: pa.Schema | None = None
) -> None:
    if not rows:
        pq.write_table(pa.table({"empty": pa.array([], type=pa.string())}), path)
        return
    try:
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression="zstd")
    except pa.ArrowException as exc:
        raise ReportError(f"cannot write {path.name}: {exc}") from exc


def _series_svg(result: RunResult) -> str:
    snapshots = result.portfolio.snapshots
    if len(snapshots) < 2:
        return '<p class="muted">Insufficient equity samples for a curve.</p>'

    values = [float(item.equity) for item in snapshots]
    balances = [float(item.balance) for item in snapshots]
    low = min(*values, *balances)
    high = max(*values, *balances)
    spread = high - low or 1.0
    left, right, top, bottom = 56, 784, 20, 224

    def points(series: list[float]) -> str:
        return " ".join(
            f"{left + index / (len(series) - 1) * (right - left):.1f},"
            f"{bottom - ((value - low) / spread * (bottom - top)):.1f}"
            for index, value in enumerate(series)
        )

    rules = "".join(
        f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" class="chart-rule"/>'
        for y in (top, (top + bottom) / 2, bottom)
    )
    labels = (
        f'<text x="0" y="{top + 5}" class="axis">{high:,.2f}</text>'
        f'<text x="0" y="{bottom}" class="axis">{low:,.2f}</text>'
    )
    return (
        '<svg viewBox="0 0 800 260" role="img" aria-label="Balance and equity curve">'
        f'{rules}{labels}'
        f'<polyline points="{points(balances)}" class="balance-line"/>'
        f'<polyline points="{points(values)}" class="equity-line"/>'
        '</svg><div class="legend"><span><i class="swatch equity"></i>Equity</span>'
        '<span><i class="swatch balance"></i>Balance</span></div>'
    )


def _html(result: RunResult, summary: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value))

    def number(value: Any, suffix: str = "") -> str:
        if value is None:
            return "—"
        return f"{float(value):,.2f}{suffix}"

    def metric_row(label: str, value: Any, detail: str = "") -> str:
        return (f'<div class="metric-row"><dt>{esc(label)}</dt><dd>{esc(value)}'
                f'<small>{esc(detail)}</small></dd></div>')

    def metric_value(value: Any, suffix: str = "") -> str:
        if value is None:
            return "—"
        return f"{float(value):,.2f}{suffix}"

    def metric_status(metric: dict[str, Any], suffix: str = "") -> str:
        return metric_value(metric.get("value"), suffix) if metric.get("value") is not None else metric.get("status", "—")

    def section(title: str, content: str, intro: str = "") -> str:
        return f'<section><h2>{esc(title)}</h2>{f"<p class=\"section-intro\">{esc(intro)}</p>" if intro else ""}{content}</section>'

    currency = result.config.account.currency
    performance = "".join([
        metric_row("Net return", number(summary["net_return_percent"], "%"), "Final equity vs initial balance"),
        metric_row("Final equity", f'{number(summary["final_equity"])} {currency}', "Marked to market"),
        metric_row("Final balance", f'{number(summary["final_balance"])} {currency}', "Realized cash balance"),
        metric_row("Max drawdown", number(summary["max_drawdown_percent"], "%"), f'{summary["drawdown_duration_samples"]:,} samples at longest'),
        metric_row("Sharpe", metric_status(summary["sharpe_daily_utc"]), "Daily UTC samples; annualized x sqrt(365)"),
        metric_row("Sortino", metric_status(summary["sortino_daily_utc"]), "Daily UTC samples; downside deviation"),
    ])
    trading = "".join([
        metric_row("Closed trades", summary["closed_trades"], "Matched entry / exit lots"),
        metric_row("Win rate", metric_status(summary["win_rate_percent"], "%"), f'{summary["wins"]} wins · {summary["losses"]} losses · {summary["breakeven"]} break-even'),
        metric_row("Profit factor", metric_status(summary["profit_factor"]), "Gross profit / gross loss"),
        metric_row("Expectancy", f'{number(summary["expectancy"]["value"])} {currency}' if summary["expectancy"]["value"] is not None else "undefined", "Average net P&L per closed lot"),
        metric_row("Holding time", f'{summary["holding_seconds"]["median"] / 3600:.1f} h median' if summary["holding_seconds"]["median"] is not None else "—", "Median duration of closed trades"),
        metric_row("Trade frequency", number(summary["trades_per_calendar_day"]), "Closed trades per calendar day"),
    ])
    costs = "".join([
        metric_row("Realized net P&L", f'{number(summary["realized_net_pnl"])} {currency}', "Ledger total"),
        metric_row("Commissions", f'{number(summary["commissions"])} {currency}', "Recorded commission entries"),
        metric_row("Financing", f'{number(summary["financing"])} {currency}', "Recorded financing entries"),
        metric_row("Max used margin", f'{number(summary["max_used_margin"])} {currency}', "Peak sampled margin"),
        metric_row("Open positions", summary["open_positions"], "Excluded from realized trade statistics"),
        metric_row("Ambiguous trades", summary["ambiguous_trades"], "Execution outcome depended on intrabar policy"),
    ])
    comparison = result.benchmark_comparison
    if comparison is None:
        benchmark = '<p class="muted">No random-policy comparison is available for this run.</p>'
    else:
        benchmark = ('<dl class="metrics benchmark-metrics">'
            + metric_row("Strategy return", number(comparison["observed"], "%"))
            + metric_row("Random median", number(comparison["median"], "%"))
            + metric_row("Random mean", number(comparison["mean"], "%"))
            + metric_row("Percentile", number(comparison["percentile"]), f'Among {comparison["sample_size"]} random trials')
            + '</dl><p class="muted">Percentile uses half weight for ties. '
            + 'Random policy returns use the configured benchmark settings. '
            + f'5th-95th percentile: {number(comparison["quantiles"]["5"], "%")} to '
            + f'{number(comparison["quantiles"]["95"], "%")}.</p>')
    warnings = "".join(f"<li>{esc(item)}</li>" for item in result.warnings)
    config = result.config
    context = "".join([
        f'<div><dt>Dataset</dt><dd>{esc(result.dataset_id)}</dd></div>',
        f'<div><dt>Instrument</dt><dd>{esc(config.instrument.id)} · {esc(config.instrument.currency)}</dd></div>',
        f'<div><dt>Coverage</dt><dd>{esc(config.start.isoformat())} → {esc(config.end.isoformat())}</dd></div>',
        f'<div><dt>Strategy</dt><dd>{esc(config.strategy.strategy_id)} · v{esc(config.strategy.version)}</dd></div>',
        f'<div><dt>Engine status</dt><dd>{esc(result.status)}</dd></div>',
        f'<div><dt>Source</dt><dd>{summary["source_bars"]:,} bars · {summary["market_events"]:,} market events</dd></div>',
    ])
    assumptions = "".join([
        f'<div><dt>Price basis</dt><dd>{esc(config.instrument.spread)} spread · {esc(config.instrument.price_tick)} tick</dd></div>',
        f'<div><dt>Execution</dt><dd>{esc(config.execution.intrabar_policy)} · {esc(config.execution.slippage_ticks)} slippage ticks</dd></div>',
        f'<div><dt>Account</dt><dd>{esc(config.account.position_mode.value)} · {number(config.account.starting_balance)} {esc(currency)} · {esc(config.instrument.leverage)}x leverage</dd></div>',
        f'<div><dt>Costs</dt><dd>{number(config.execution.commission_per_lot_per_side)} {esc(currency)}/lot/side · financing {esc(config.execution.financing_timezone)}</dd></div>',
    ])
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, 
initial-scale=1">
<title>Backtest report: {esc(result.run_id)}</title>
<style>
:root{{color-scheme:light}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f5f4ef;color:#202321;font:15px/1.55 Inter,ui-sans-serif,system-ui,sans-serif}}
main{{max-width:1180px;margin:auto;padding:52px 34px 72px}}
h1{{font-size:clamp(2rem,5vw,4.2rem);line-height:1.02;letter-spacing:-.055em;margin:6px 0 12px;font-weight:650}}
h2{{border-top:2px solid #202321;margin:48px 0 0;padding:13px 0 9px;font-size:.9rem;text-transform:uppercase;letter-spacing:.12em;font-weight:750}}
p{{margin:8px 0}}
.eyebrow{{color:#147a5e;text-transform:uppercase;letter-spacing:.18em;font-size:.72rem;font-weight:800}}
.muted,small,.section-intro{{color:#737872}}
.section-intro{{max-width:680px;margin:0 0 16px}}
.lede{{font-size:1.02rem;color:#525952;max-width:720px}}
.run-id{{font:12px/1.4 ui-monospace,SFMono-Regular,monospace;color:#737872;overflow-wrap:anywhere}}
.status{{display:inline-flex;align-items:center;gap:7px;color:#147a5e;font-weight:750;text-transform:uppercase;letter-spacing:.1em;font-size:.72rem}}
.status::before{{content:"";width:8px;height:8px;background:#147a5e;display:inline-block}}
.metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border-bottom:1px solid #c9ccc5}}
.metric-row{{display:grid;grid-template-columns:minmax(120px,1fr) minmax(150px,1.2fr);gap:14px;align-items:baseline;border-top:1px solid #c9ccc5;padding:12px 0}}
.metric-row dt,.meta dt{{color:#737872;font-size:.83rem}}
.metric-row dd{{margin:0;font-variant-numeric:tabular-nums;font-weight:700;text-align:right}}
.metric-row small{{display:block;font-size:.72rem;font-weight:400;margin-top:2px}}
.metrics .metric-row{{border-top:0;border-bottom:1px solid #c9ccc5;padding:13px 14px 13px 0}}
.metrics .metric-row:nth-child(even){{padding-left:14px;border-left:1px solid #c9ccc5}}
.chart-wrap{{border-bottom:1px solid #c9ccc5;padding:12px 0 14px}}
svg{{width:100%;height:auto;display:block}}
.chart-rule{{stroke:#d5d8d1;stroke-width:1}}
.axis{{fill:#737872;font:11px ui-monospace,SFMono-Regular,monospace}}
.equity-line{{fill:none;stroke:#147a5e;stroke-width:2.5}}
.balance-line{{fill:none;stroke:#929890;stroke-width:1.5;stroke-dasharray:5 5}}
.legend{{display:flex;gap:20px;color:#737872;font-size:.78rem}}
.swatch{{display:inline-block;width:20px;border-top:2px solid #147a5e;margin:0 7px 3px 0}}
.swatch.balance{{border-top:2px dashed #929890}}
.columns{{display:grid;grid-template-columns:1fr 1fr;gap:34px}}
.meta{{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #c9ccc5}}
.meta>div{{border-top:1px solid #c9ccc5;padding:11px 12px 11px 0}}
.meta>div:nth-child(even){{padding-left:12px;border-left:1px solid #c9ccc5}}
.meta dd{{margin:3px 0 0;font-size:.9rem}}
.benchmark-metrics{{max-width:720px}}
.warning-list{{margin:0;padding:12px 0 12px 20px;border-bottom:1px solid #c9ccc5}}
code{{overflow-wrap:anywhere}}
footer{{border-top:1px solid #c9ccc5;margin-top:48px;padding-top:14px;color:#737872;font-size:.78rem}}
@media (max-width:760px){{main{{padding:34px 20px 54px}}.metrics,.columns,.meta{{grid-template-columns:1fr}}.metrics .metric-row:nth-child(even),.meta>div:nth-child(even){{padding-left:0;border-left:0}}}}
</style></head><body><main><div class="eyebrow">Performance report</div>
<h1>{esc(result.config.strategy.strategy_id)}</h1>
<p class="lede">{esc(config.start.isoformat())} to {esc(config.end.isoformat())} · <span class="status">{esc(result.status)}</span></p>
<p class="run-id">{esc(result.run_id)}</p>
{section("Performance", f'<div class="metrics">{performance}</div>', "The small set of outcomes to read first. Equity includes unrealized P&L; balance does not.")}
{section("Equity path", f'<div class="chart-wrap">{_series_svg(result)}</div>', "Balance and marked equity sampled through the run. Drawdown is calculated from the equity samples.")}
<div class="columns">{section("Trading activity", f'<dl class="metrics">{trading}</dl>', "Closed-lot statistics; partial closes may create multiple matched lots.")}{section("Costs & exposure", f'<dl class="metrics">{costs}</dl>', "Cash movements and exposure that shape the result.")}</div>
{section("Benchmark", f'<div>{benchmark}</div>', "Random-policy context is descriptive, not evidence of a persistent edge.")}
<div class="columns">{section("Run context", f'<dl class="meta">{context}</dl>')}{section("Model assumptions", f'<dl class="meta">{assumptions}</dl>')}</div>
{section("Warnings", f'<ul class="warning-list">{warnings or "<li>None</li>"}</ul>')}
<footer>Historical simulation results are not evidence of future performance.</footer>
</main>
</body>
</html>'''


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
    benchmark_schema = pa.schema([
        ("policy", pa.string()),
        ("trial", pa.int64()),
        ("seed", pa.uint64()),
        ("net_return_percent", pa.float64()),
        ("max_drawdown_percent", pa.float64()),
        ("closed_trades", pa.int64()),
    ])
    _write_parquet(run_dir / "benchmarks.parquet", result.benchmarks, schema=benchmark_schema)
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
