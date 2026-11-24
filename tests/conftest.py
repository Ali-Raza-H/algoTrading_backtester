from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from backtest.account import Portfolio
from backtest.broker import Broker
from backtest.config import ExecutionConfig
from backtest.domain.models import Bar, Instrument, PositionMode, PriceBasis


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(
        id="XAUUSD",
        currency="USD",
        contract_size=Decimal("100"),
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.01"),
        min_quantity=Decimal("0.01"),
        max_quantity=Decimal("100"),
        leverage=Decimal("30"),
        spread=Decimal("0"),
        session_timezone="UTC",
        sessions=("Tue 00:00-23:59",),
    )


@pytest.fixture
def execution() -> ExecutionConfig:
    return ExecutionConfig(
        slippage_ticks=Decimal("0"),
        commission_per_lot_per_side=Decimal("0"),
        financing_long_per_lot_daily=Decimal("0"),
        financing_short_per_lot_daily=Decimal("0"),
        financing_rollover_time=time(22, 0),
        financing_timezone="UTC",
        financing_triple_day=2,
        intrabar_policy="stop_first",
        stale_after_seconds=None,
    )


@pytest.fixture
def broker(instrument: Instrument, execution: ExecutionConfig) -> Broker:
    portfolio = Portfolio(
        starting_balance=Decimal("10000"),
        instrument=instrument,
        position_mode=PositionMode.NETTING,
    )
    return Broker(
        instrument=instrument,
        execution=execution,
        portfolio=portfolio,
        stop_out_percent=Decimal("50"),
    )


def make_bar(
    minute: int,
    *,
    open_price: str = "2650",
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
) -> Bar:
    start = datetime(2024, 1, 2, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(
        instrument_id="XAUUSD",
        timeframe="1m",
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=Decimal(open_price),
        high=Decimal(high or open_price),
        low=Decimal(low or open_price),
        close=Decimal(close or open_price),
        price_basis=PriceBasis.MID,
    )
