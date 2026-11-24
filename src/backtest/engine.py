from __future__ import annotations

import hashlib
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from backtest.account import Portfolio
from backtest.broker import Broker
from backtest.config import ExperimentConfig
from backtest.data import iter_bars, load_manifest
from backtest.domain.errors import DataError, SimulationError, StrategyError
from backtest.domain.models import Fill
from backtest.market.aggregation import TimeframeAggregator
from backtest.market.timeframes import parse_timeframe
from backtest.sdk import StrategyContext, load_strategy


@dataclass(slots=True)
class RunResult:
    run_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    config: ExperimentConfig
    portfolio: Portfolio
    broker: Broker
    dataset_id: str
    source_bars: int = 0
    market_events: int = 0
    warnings: list[str] = field(default_factory=list)
    benchmarks: list[dict[str, Any]] = field(default_factory=list)
    benchmark_comparison: dict[str, Any] | None = None
    error: str | None = None
    traceback: str | None = None


def _run_id(config: ExperimentConfig, dataset_id: str) -> str:
    material = "|".join(
        (
            dataset_id,
            config.strategy.strategy_id,
            config.strategy.version,
            config.start.isoformat(),
            config.end.isoformat(),
            str(config.seed),
        )
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:12]
    return f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{digest}"


def validate_run(config: ExperimentConfig) -> dict[str, Any]:
    manifest = load_manifest(config.source)
    if manifest.instrument_id != config.instrument.id:
        raise DataError("dataset instrument does not match experiment instrument")
    source_tf = parse_timeframe(manifest.timeframe)
    if source_tf.seconds != 60:
        raise DataError("v1 experiments require an authoritative 1m dataset")
    requested = [parse_timeframe(value) for value in config.strategy.timeframes]
    if any(item.seconds < source_tf.seconds for item in requested):
        raise DataError("strategy requests data finer than the source")
    first = datetime.fromisoformat(manifest.first_open_time)
    last = datetime.fromisoformat(manifest.last_close_time)
    warmup = _warmup_duration(config)
    if config.start - warmup < first:
        raise DataError("dataset has insufficient history for configured warmup")
    if config.end > last:
        raise DataError("dataset does not cover experiment end")
    load_strategy(config.strategy.entrypoint, config.strategy.parameters)
    return {
        "valid": True,
        "dataset_id": manifest.dataset_id,
        "warmup_start": (config.start - warmup).isoformat(),
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
    }


def _warmup_duration(config: ExperimentConfig) -> timedelta:
    seconds = 0
    for timeframe in config.strategy.timeframes:
        lookback = config.strategy.lookbacks.get(timeframe, 1)
        if lookback < 1:
            raise SimulationError(f"lookback for {timeframe} must be positive")
        seconds = max(seconds, parse_timeframe(timeframe).seconds * lookback)
    return timedelta(seconds=seconds)


def execute_run(config: ExperimentConfig) -> RunResult:
    validation = validate_run(config)
    manifest = load_manifest(config.source)
    portfolio = Portfolio(
        starting_balance=config.account.starting_balance,
        instrument=config.instrument,
        position_mode=config.account.position_mode,
    )
    broker = Broker(
        instrument=config.instrument,
        execution=config.execution,
        portfolio=portfolio,
        stop_out_percent=config.account.stop_out_percent,
    )
    result = RunResult(
        run_id=_run_id(config, manifest.dataset_id),
        status="running",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        config=config,
        portfolio=portfolio,
        broker=broker,
        dataset_id=manifest.dataset_id,
    )
    history_sizes = {
        timeframe: max(config.strategy.lookbacks.get(timeframe, 1) + 2, 2)
        for timeframe in config.strategy.timeframes
    }
    context = StrategyContext(
        broker=broker,
        portfolio=portfolio,
        seed=config.seed,
        history_sizes=history_sizes,
    )
    aggregator = TimeframeAggregator(manifest.timeframe, config.strategy.timeframes)
    strategy = load_strategy(config.strategy.entrypoint, config.strategy.parameters)
    warmup_start = datetime.fromisoformat(str(validation["warmup_start"]))
    seen_order_state: dict[str, tuple[str, int]] = {}
    seen_fills: set[str] = set()
    last_sample_date: object | None = None
    last_mid: Decimal | None = None

    def flush_order_updates() -> None:
        for _ in range(100):
            changed = False
            for order in broker.orders.values():
                state = (order.status.value, order.version)
                if seen_order_state.get(order.id) != state:
                    strategy.on_order_update(order, context)
                    seen_order_state[order.id] = state
                    changed = True
            if not changed:
                return
        raise SimulationError("order callback loop exceeded 100 iterations")

    try:
        context._advance(warmup_start, warmup=True)
        strategy.on_start(context)
        for source in iter_bars(config.source, start=warmup_start, end=config.end):
            result.source_bars += 1
            warmup = source.close_time <= config.start
            context._advance(source.close_time, warmup=warmup)
            new_fills: list[Fill] = []
            if not warmup:
                new_fills = broker.process_bar(source)
                last_mid = broker.current_mid
                flush_order_updates()
                for fill in new_fills:
                    if fill.id not in seen_fills:
                        strategy.on_fill(fill, context)
                        seen_fills.add(fill.id)
            timer_callback = getattr(strategy, "on_timer", None)
            for timer in context._due_timers(source.close_time):
                if callable(timer_callback):
                    timer_callback(timer, context)
            completed = aggregator.update(source)
            for event in completed:
                context._append_bar(event)
                result.market_events += 1
                if warmup:
                    strategy.on_warmup(event, context)
                else:
                    strategy.on_market(event, context)
            flush_order_updates()
            if not warmup and last_mid is not None and source.close_time.date() != last_sample_date:
                portfolio.snapshot(source.close_time, last_mid)
                last_sample_date = source.close_time.date()
        if result.source_bars == 0:
            raise SimulationError("experiment selected no source bars")
        for timeframe, required in config.strategy.lookbacks.items():
            if len(context.bars(config.instrument.id, timeframe, required)) < required:
                raise SimulationError(f"insufficient warmup for {timeframe}: need {required} bars")
        strategy.on_finish(context)
        if last_mid is not None and (
            not portfolio.snapshots or portfolio.snapshots[-1].timestamp < config.end
        ):
            portfolio.snapshot(config.end, last_mid)
        portfolio.assert_reconciled()
        result.status = "completed"
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        result.traceback = traceback.format_exc()
        if isinstance(exc, (DataError, StrategyError, SimulationError)):
            raise
        raise StrategyError(f"strategy run failed: {exc}") from exc
    finally:
        result.finished_at = datetime.now(UTC)
    return result
