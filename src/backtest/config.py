from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from backtest.domain.errors import ConfigError
from backtest.domain.models import Instrument, PositionMode, PriceBasis
from backtest.market.calendar import SessionCalendar
from backtest.market.timeframes import parse_timeframe


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a mapping")
    return cast(dict[str, Any], value)


def _strict(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown keys: {', '.join(unknown)}")


def _required(data: dict[str, Any], keys: set[str], path: str) -> None:
    missing = sorted(keys - set(data))
    if missing:
        raise ConfigError(f"{path}: missing required keys: {', '.join(missing)}")


def _decimal(value: object, path: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ConfigError(f"{path}: decimal values must be quoted strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ConfigError(f"{path}: invalid decimal {value!r}") from exc
    if not result.is_finite() or (positive and result <= 0):
        condition = "positive and finite" if positive else "finite"
        raise ConfigError(f"{path}: must be {condition}")
    return result


def _datetime(value: object, path: str) -> datetime:
    if not isinstance(value, str):
        raise ConfigError(f"{path}: expected ISO 8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError(f"{path}: invalid ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        raise ConfigError(f"{path}: timezone offset is required")
    return result


def _path(base: Path, value: object, path: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{path}: expected a non-empty path")
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def _timeframe_labels(values: list[Any], path: str) -> tuple[str, ...]:
    """Normalise declared timeframe labels and order them by duration.

    Strategies compare timeframe labels verbatim against ``event.timeframe``, so
    ``1H`` must never reach the engine as anything other than ``1h``.
    """
    labels: dict[int, str] = {}
    for value in values:
        timeframe = parse_timeframe(str(value))
        existing = labels.get(timeframe.seconds)
        if existing is not None:
            raise ConfigError(f"{path}: {existing!r} and {value!r} are the same duration")
        labels[timeframe.seconds] = timeframe.label
    return tuple(labels[seconds] for seconds in sorted(labels))


def _lookbacks(raw: object, timeframes: tuple[str, ...], path: str) -> dict[str, int]:
    """Require one positive lookback for every subscribed timeframe.

    A missing entry would silently shrink the history buffer to the engine default
    and leave the strategy guessing why it never sees enough bars.
    """
    declared = _mapping(raw, path)
    subscribed = {parse_timeframe(label).seconds: label for label in timeframes}
    lookbacks: dict[str, int] = {}
    for key, value in declared.items():
        timeframe = parse_timeframe(str(key))
        label = subscribed.get(timeframe.seconds)
        if label is None:
            raise ConfigError(
                f"{path}: {key!r} is not declared in strategy.timeframes {list(timeframes)}"
            )
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"{path}.{label}: expected a positive integer")
        lookbacks[label] = value
    missing = [label for label in timeframes if label not in lookbacks]
    if missing:
        raise ConfigError(f"{path}: missing entries for {missing}")
    return lookbacks


def _align_timeframe_parameter(
    parameters: dict[str, Any], timeframes: tuple[str, ...]
) -> dict[str, Any]:
    """Point a strategy's own ``timeframe`` parameter at a subscribed label.

    Strategies filter market events on that parameter, so a timeframe that is not
    subscribed, or spelled differently from the subscription, would ignore every
    bar and complete the run with no trades and no error.
    """
    if "timeframe" not in parameters:
        return parameters
    requested = parameters["timeframe"]
    wanted = parse_timeframe(str(requested))
    subscribed = {parse_timeframe(label).seconds: label for label in timeframes}
    label = subscribed.get(wanted.seconds)
    if label is None:
        raise ConfigError(
            f"strategy.parameters.timeframe: {requested!r} is not declared in "
            f"strategy.timeframes {list(timeframes)}"
        )
    return {**parameters, "timeframe": label}


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    return _mapping(content, str(path))


@dataclass(frozen=True, slots=True)
class CsvMapping:
    timestamp: str
    open: str
    high: str
    low: str
    close: str
    volume: str | None
    timestamp_semantics: str
    timestamp_format: str | None
    timezone: str
    delimiter: str
    price_basis: PriceBasis


@dataclass(frozen=True, slots=True)
class ImportConfig:
    source: Path
    output: Path
    instrument_id: str
    timeframe: str
    mapping: CsvMapping
    batch_size: int
    strict_gaps: bool

    @classmethod
    def from_file(cls, path: Path) -> ImportConfig:
        root = load_yaml(path)
        _strict(
            root,
            {
                "schema_version",
                "source",
                "output",
                "instrument",
                "timeframe",
                "csv",
                "batch_size",
                "strict_gaps",
            },
            "config",
        )
        _required(
            root, {"schema_version", "source", "output", "instrument", "timeframe", "csv"}, "config"
        )
        if root["schema_version"] != 1:
            raise ConfigError("schema_version: only version 1 is supported")
        raw = _mapping(root["csv"], "csv")
        allowed = {
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "timestamp_semantics",
            "timestamp_format",
            "timezone",
            "delimiter",
            "price_basis",
        }
        _strict(raw, allowed, "csv")
        _required(
            raw,
            {
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "timestamp_semantics",
                "timezone",
                "price_basis",
            },
            "csv",
        )
        semantics = str(raw["timestamp_semantics"])
        if semantics not in {"open", "close"}:
            raise ConfigError("csv.timestamp_semantics: expected 'open' or 'close'")
        try:
            ZoneInfo(str(raw["timezone"]))
        except ZoneInfoNotFoundError as exc:
            raise ConfigError("csv.timezone: unknown timezone") from exc
        delimiter = str(raw.get("delimiter", ","))
        if len(delimiter) != 1:
            raise ConfigError("csv.delimiter: expected exactly one character")
        mapping = CsvMapping(
            timestamp=str(raw["timestamp"]),
            open=str(raw["open"]),
            high=str(raw["high"]),
            low=str(raw["low"]),
            close=str(raw["close"]),
            volume=str(raw["volume"]) if raw.get("volume") is not None else None,
            timestamp_semantics=semantics,
            timestamp_format=str(raw["timestamp_format"]) if raw.get("timestamp_format") else None,
            timezone=str(raw["timezone"]),
            delimiter=delimiter,
            price_basis=PriceBasis(str(raw["price_basis"]).lower()),
        )
        batch_size = int(root.get("batch_size", 50_000))
        if batch_size < 1:
            raise ConfigError("batch_size: must be positive")
        return cls(
            source=_path(path.parent, root["source"], "source"),
            output=_path(path.parent, root["output"], "output"),
            instrument_id=str(root["instrument"]),
            timeframe=str(root["timeframe"]),
            mapping=mapping,
            batch_size=batch_size,
            strict_gaps=bool(root.get("strict_gaps", False)),
        )


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    entrypoint: str
    strategy_id: str
    version: str
    parameters: dict[str, Any]
    timeframes: tuple[str, ...]
    lookbacks: dict[str, int]


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    slippage_ticks: Decimal
    commission_per_lot_per_side: Decimal
    financing_long_per_lot_daily: Decimal
    financing_short_per_lot_daily: Decimal
    financing_rollover_time: time
    financing_timezone: str
    financing_triple_day: int
    intrabar_policy: str
    stale_after_seconds: int | None


@dataclass(frozen=True, slots=True)
class AccountConfig:
    currency: str
    starting_balance: Decimal
    position_mode: PositionMode
    stop_out_percent: Decimal


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    enabled: bool
    trials: int
    policies: tuple[str, ...]
    entry_probability: float
    holding_bars: int
    quantity: Decimal
    timeframe: str


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    source: Path
    instrument: Instrument
    strategy: StrategyConfig
    execution: ExecutionConfig
    account: AccountConfig
    benchmark: BenchmarkConfig
    start: datetime
    end: datetime
    seed: int
    workers: int
    output: Path
    full_market_trace: bool

    @classmethod
    def from_file(cls, path: Path) -> ExperimentConfig:
        root = load_yaml(path)
        allowed = {
            "schema_version",
            "market",
            "instrument",
            "strategy",
            "execution",
            "account",
            "experiment",
            "report",
            "benchmark",
        }
        _strict(root, allowed, "config")
        _required(root, allowed - {"report", "benchmark"}, "config")
        if root["schema_version"] != 1:
            raise ConfigError("schema_version: only version 1 is supported")
        base = path.parent

        market = _mapping(root["market"], "market")
        _strict(market, {"dataset"}, "market")
        _required(market, {"dataset"}, "market")

        raw_i = _mapping(root["instrument"], "instrument")
        i_keys = {
            "id",
            "currency",
            "contract_size",
            "price_tick",
            "quantity_step",
            "min_quantity",
            "max_quantity",
            "leverage",
            "spread",
            "session_timezone",
            "sessions",
        }
        _strict(raw_i, i_keys, "instrument")
        _required(raw_i, i_keys, "instrument")
        try:
            ZoneInfo(str(raw_i["session_timezone"]))
        except ZoneInfoNotFoundError as exc:
            raise ConfigError("instrument.session_timezone: unknown timezone") from exc
        sessions_raw = raw_i["sessions"]
        if not isinstance(sessions_raw, list) or not sessions_raw:
            raise ConfigError("instrument.sessions: expected a non-empty list")
        instrument = Instrument(
            id=str(raw_i["id"]),
            currency=str(raw_i["currency"]),
            contract_size=_decimal(
                raw_i["contract_size"], "instrument.contract_size", positive=True
            ),
            price_tick=_decimal(raw_i["price_tick"], "instrument.price_tick", positive=True),
            quantity_step=_decimal(
                raw_i["quantity_step"], "instrument.quantity_step", positive=True
            ),
            min_quantity=_decimal(raw_i["min_quantity"], "instrument.min_quantity", positive=True),
            max_quantity=_decimal(raw_i["max_quantity"], "instrument.max_quantity", positive=True),
            leverage=_decimal(raw_i["leverage"], "instrument.leverage", positive=True),
            spread=_decimal(raw_i["spread"], "instrument.spread"),
            session_timezone=str(raw_i["session_timezone"]),
            sessions=tuple(str(item) for item in sessions_raw),
        )
        SessionCalendar(instrument.session_timezone, instrument.sessions)
        if instrument.spread < 0 or instrument.min_quantity > instrument.max_quantity:
            raise ConfigError("instrument: invalid spread or quantity bounds")

        raw_s = _mapping(root["strategy"], "strategy")
        s_keys = {"entrypoint", "strategy_id", "version", "parameters", "timeframes", "lookbacks"}
        _strict(raw_s, s_keys, "strategy")
        _required(raw_s, s_keys, "strategy")
        tfs = raw_s["timeframes"]
        if not isinstance(tfs, list) or not tfs:
            raise ConfigError("strategy.timeframes: expected a non-empty list")
        timeframes = _timeframe_labels(tfs, "strategy.timeframes")
        parameters = _mapping(raw_s["parameters"], "strategy.parameters")
        strategy = StrategyConfig(
            entrypoint=str(raw_s["entrypoint"]),
            strategy_id=str(raw_s["strategy_id"]),
            version=str(raw_s["version"]),
            parameters=_align_timeframe_parameter(parameters, timeframes),
            timeframes=timeframes,
            lookbacks=_lookbacks(raw_s["lookbacks"], timeframes, "strategy.lookbacks"),
        )

        raw_e = _mapping(root["execution"], "execution")
        e_keys = {
            "slippage_ticks",
            "commission_per_lot_per_side",
            "financing_long_per_lot_daily",
            "financing_short_per_lot_daily",
            "financing_rollover_time",
            "financing_timezone",
            "financing_triple_day",
            "intrabar_policy",
            "stale_after_seconds",
        }
        _strict(raw_e, e_keys, "execution")
        _required(raw_e, e_keys, "execution")
        policy = str(raw_e["intrabar_policy"])
        if policy not in {"stop_first", "ohlc", "olhc", "strict"}:
            raise ConfigError("execution.intrabar_policy: unsupported policy")
        stale = raw_e["stale_after_seconds"]
        try:
            rollover_time = time.fromisoformat(str(raw_e["financing_rollover_time"]))
            ZoneInfo(str(raw_e["financing_timezone"]))
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ConfigError("execution: invalid financing rollover time or timezone") from exc
        weekdays = {
            name: index
            for index, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        }
        triple_day = weekdays.get(str(raw_e["financing_triple_day"]).title())
        if triple_day is None:
            raise ConfigError("execution.financing_triple_day: expected Mon through Sun")
        execution = ExecutionConfig(
            slippage_ticks=_decimal(raw_e["slippage_ticks"], "execution.slippage_ticks"),
            commission_per_lot_per_side=_decimal(
                raw_e["commission_per_lot_per_side"], "execution.commission_per_lot_per_side"
            ),
            financing_long_per_lot_daily=_decimal(
                raw_e["financing_long_per_lot_daily"],
                "execution.financing_long_per_lot_daily",
            ),
            financing_short_per_lot_daily=_decimal(
                raw_e["financing_short_per_lot_daily"],
                "execution.financing_short_per_lot_daily",
            ),
            financing_rollover_time=rollover_time,
            financing_timezone=str(raw_e["financing_timezone"]),
            financing_triple_day=triple_day,
            intrabar_policy=policy,
            stale_after_seconds=int(stale) if stale is not None else None,
        )
        if execution.slippage_ticks < 0 or execution.commission_per_lot_per_side < 0:
            raise ConfigError("execution: costs cannot be negative")

        raw_a = _mapping(root["account"], "account")
        a_keys = {"currency", "starting_balance", "position_mode", "stop_out_percent"}
        _strict(raw_a, a_keys, "account")
        _required(raw_a, a_keys, "account")
        account = AccountConfig(
            currency=str(raw_a["currency"]),
            starting_balance=_decimal(
                raw_a["starting_balance"], "account.starting_balance", positive=True
            ),
            position_mode=PositionMode(str(raw_a["position_mode"])),
            stop_out_percent=_decimal(
                raw_a["stop_out_percent"], "account.stop_out_percent", positive=True
            ),
        )
        if account.currency != instrument.currency:
            raise ConfigError("account.currency: must match instrument quote currency in v1")

        raw_b = _mapping(root.get("benchmark", {}), "benchmark")
        b_keys = {
            "enabled",
            "trials",
            "policies",
            "entry_probability",
            "holding_bars",
            "quantity",
            "timeframe",
        }
        _strict(raw_b, b_keys, "benchmark")
        enabled = bool(raw_b.get("enabled", False))
        if enabled:
            _required(raw_b, b_keys, "benchmark")
        policies_raw = raw_b.get("policies", ["random_policy", "cash"])
        if not isinstance(policies_raw, list):
            raise ConfigError("benchmark.policies: expected a list")
        policies = tuple(str(item) for item in policies_raw)
        unsupported = set(policies) - {"random_policy", "cash"}
        if unsupported:
            raise ConfigError(f"benchmark.policies: unsupported policies {sorted(unsupported)}")
        trials = int(raw_b.get("trials", 1000))
        probability = float(raw_b.get("entry_probability", 0.01))
        holding_bars = int(raw_b.get("holding_bars", 12))
        if trials < 1 or not 0 <= probability <= 1 or holding_bars < 1:
            raise ConfigError("benchmark: invalid trials, probability, or holding period")
        benchmark = BenchmarkConfig(
            enabled=enabled,
            trials=trials,
            policies=policies,
            entry_probability=probability,
            holding_bars=holding_bars,
            quantity=_decimal(raw_b.get("quantity", "0.01"), "benchmark.quantity", positive=True),
            timeframe=str(raw_b.get("timeframe", "1h")),
        )

        raw_x = _mapping(root["experiment"], "experiment")
        x_keys = {"start", "end", "seed", "workers"}
        _strict(raw_x, x_keys, "experiment")
        _required(raw_x, x_keys, "experiment")
        start = _datetime(raw_x["start"], "experiment.start")
        end = _datetime(raw_x["end"], "experiment.end")
        if start >= end:
            raise ConfigError("experiment: start must precede end")
        workers = int(raw_x["workers"])
        if workers < 1:
            raise ConfigError("experiment.workers: must be positive")

        report = _mapping(root.get("report", {}), "report")
        _strict(report, {"directory", "full_market_trace"}, "report")
        output = _path(base, report.get("directory", "../runs"), "report.directory")
        return cls(
            source=_path(base, market["dataset"], "market.dataset"),
            instrument=instrument,
            strategy=strategy,
            execution=execution,
            account=account,
            benchmark=benchmark,
            start=start,
            end=end,
            seed=int(raw_x["seed"]),
            workers=workers,
            output=output,
            full_market_trace=bool(report.get("full_market_trace", False)),
        )
