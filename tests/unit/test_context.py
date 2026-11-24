from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest.broker import Broker
from backtest.domain.models import OrderRequest, OrderType, Side
from backtest.sdk import StrategyContext
from tests.conftest import make_bar


def test_virtual_timers_are_ordered_and_cancellable(broker: Broker) -> None:
    context = StrategyContext(
        broker=broker,
        portfolio=broker.portfolio,
        seed=42,
        history_sizes={"1m": 2},
    )
    now = datetime(2024, 1, 2, tzinfo=UTC)
    context._advance(now, warmup=False)
    later = context.schedule_at(now + timedelta(minutes=2), "later")
    first = context.schedule_at(now + timedelta(minutes=1), "first")
    context.cancel_timer(later)
    assert [item.tag for item in context._due_timers(now + timedelta(minutes=3))] == ["first"]
    assert first == "timer-00000002"


def test_virtual_timer_rejects_non_future_time(broker: Broker) -> None:
    context = StrategyContext(
        broker=broker,
        portfolio=broker.portfolio,
        seed=42,
        history_sizes={"1m": 2},
    )
    now = datetime(2024, 1, 2, tzinfo=UTC)
    context._advance(now, warmup=False)
    with pytest.raises(ValueError, match="future"):
        context.schedule_at(now, "invalid")


def test_context_returns_current_account_and_defensive_positions(broker: Broker) -> None:
    context = StrategyContext(
        broker=broker,
        portfolio=broker.portfolio,
        seed=42,
        history_sizes={"1m": 2},
    )
    now = datetime(2024, 1, 2, tzinfo=UTC)
    context._advance(now, warmup=False)
    context.submit_order(
        OrderRequest("entry", "XAUUSD", Side.BUY, OrderType.MARKET, Decimal("0.01"))
    )
    broker.process_bar(make_bar(0))
    assert context.account() is not None
    snapshot = context.positions()[0]
    snapshot.quantity = Decimal("9")
    assert next(iter(broker.portfolio.positions.values())).quantity == Decimal("0.01")
