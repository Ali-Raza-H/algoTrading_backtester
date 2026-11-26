from __future__ import annotations

import math
from decimal import Decimal
from itertools import pairwise
from statistics import mean, median, stdev
from typing import Any

from backtest.engine import RunResult


def _money(value: Decimal) -> str:
    return format(value, "f")


def _drawdown(result: RunResult) -> tuple[Decimal, int]:
    peak: Decimal | None = None
    maximum = Decimal(0)
    duration = 0
    current_duration = 0
    for snapshot in result.portfolio.snapshots:
        if peak is None or snapshot.equity >= peak:
            peak = snapshot.equity
            current_duration = 0
            continue
        current_duration += 1
        duration = max(duration, current_duration)
        if peak > 0:
            maximum = max(maximum, (peak - snapshot.equity) / peak * Decimal(100))
    return maximum, duration


def calculate_metrics(result: RunResult) -> dict[str, Any]:
    portfolio = result.portfolio
    trades = portfolio.trades
    initial = portfolio.starting_balance
    final_balance = portfolio.balance
    final_equity = portfolio.snapshots[-1].equity if portfolio.snapshots else final_balance
    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]
    breakeven = [trade for trade in trades if trade.net_pnl == 0]
    gross_profit = sum((trade.net_pnl for trade in wins), Decimal(0))
    gross_loss = -sum((trade.net_pnl for trade in losses), Decimal(0))
    if gross_loss:
        profit_factor: dict[str, Any] = {
            "value": float(gross_profit / gross_loss),
            "status": "defined",
        }
    elif gross_profit:
        profit_factor = {"value": None, "status": "infinite"}
    else:
        profit_factor = {"value": None, "status": "undefined"}
    if trades:
        win_rate: dict[str, Any] = {
            "value": float(Decimal(len(wins)) / Decimal(len(trades)) * 100),
            "status": "defined",
        }
        expectancy: dict[str, Any] = {
            "value": _money(sum((item.net_pnl for item in trades), Decimal(0)) / len(trades)),
            "status": "defined",
        }
    else:
        win_rate = {"value": None, "status": "undefined"}
        expectancy = {"value": None, "status": "undefined"}
    maximum_drawdown, drawdown_days = _drawdown(result)
    durations = [(trade.closed_at - trade.opened_at).total_seconds() for trade in trades]
    realized = sum((entry.amount for entry in portfolio.ledger), Decimal(0))
    equity = [float(item.equity) for item in portfolio.snapshots]
    returns = [current / previous - 1 for previous, current in pairwise(equity) if previous]
    if len(returns) >= 2 and stdev(returns) > 0:
        sharpe: dict[str, Any] = {
            "value": mean(returns) / stdev(returns) * math.sqrt(365),
            "status": "defined",
        }
    else:
        sharpe = {"value": None, "status": "undefined"}
    downside = math.sqrt(mean([min(0.0, value) ** 2 for value in returns])) if returns else 0
    if returns and downside > 0:
        sortino: dict[str, Any] = {
            "value": mean(returns) / downside * math.sqrt(365),
            "status": "defined",
        }
    elif returns and mean(returns) > 0:
        sortino = {"value": None, "status": "infinite"}
    else:
        sortino = {"value": None, "status": "undefined"}
    commissions = -sum(
        (entry.amount for entry in portfolio.ledger if entry.kind == "COMMISSION"), Decimal(0)
    )
    financing = sum(
        (entry.amount for entry in portfolio.ledger if entry.kind == "FINANCING"), Decimal(0)
    )
    metrics = {
        "schema_version": 1,
        "run_id": result.run_id,
        "status": result.status,
        "initial_balance": _money(initial),
        "final_balance": _money(final_balance),
        "final_equity": _money(final_equity),
        "realized_net_pnl": _money(realized),
        "net_return_percent": float((final_equity / initial - 1) * 100),
        "closed_trades": len(trades),
        "trades_per_calendar_day": len(trades)
        / ((result.config.end - result.config.start).total_seconds() / 86400),
        "average_profit": _money(gross_profit / len(wins)) if wins else None,
        "average_loss": _money(-gross_loss / len(losses)) if losses else None,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate_percent": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "sharpe_daily_utc": sharpe,
        "sortino_daily_utc": sortino,
        "max_drawdown_percent": float(maximum_drawdown),
        "drawdown_duration_samples": drawdown_days,
        "holding_seconds": {
            "mean": mean(durations) if durations else None,
            "median": median(durations) if durations else None,
            "min": min(durations) if durations else None,
            "max": max(durations) if durations else None,
        },
        "orders": len(result.broker.orders),
        "fills": len(result.broker.fills),
        "open_positions": len(portfolio.positions),
        "ambiguous_trades": sum(1 for item in trades if item.ambiguous),
        "commissions": _money(commissions),
        "financing": _money(financing),
        "max_used_margin": _money(
            max((item.used_margin for item in portfolio.snapshots), default=Decimal(0))
        ),
        "source_bars": result.source_bars,
        "market_events": result.market_events,
    }
    if result.benchmark_comparison is not None:
        metrics["benchmark"] = result.benchmark_comparison
    return metrics
