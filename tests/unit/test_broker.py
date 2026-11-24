from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from datetime import time as clock_time
from decimal import Decimal

import pytest

from backtest.account import Portfolio
from backtest.broker import Broker
from backtest.config import ExecutionConfig
from backtest.domain.errors import SimulationError
from backtest.domain.models import (
    Instrument,
    OrderRequest,
    OrderStatus,
    OrderType,
    PositionMode,
    Side,
)
from tests.conftest import make_bar


def request(
    request_id: str,
    side: Side,
    order_type: OrderType = OrderType.MARKET,
    **prices: Decimal,
) -> OrderRequest:
    return OrderRequest(
        request_id=request_id,
        instrument_id="XAUUSD",
        side=side,
        type=order_type,
        quantity=Decimal("0.05"),
        **prices,
    )


def test_close_signal_fills_on_next_bar_and_reconciles_long_pnl(broker: Broker) -> None:
    now = datetime(2024, 1, 2, 0, 1, tzinfo=UTC)
    entry = broker.submit(request("entry", Side.BUY), now)
    assert not broker.process_bar(make_bar(0))
    fills = broker.process_bar(make_bar(1, open_price="2650"))
    assert fills[0].price == Decimal("2650")
    position = next(iter(broker.portfolio.positions.values()))
    close = OrderRequest(
        request_id="close",
        instrument_id="XAUUSD",
        side=Side.SELL,
        type=OrderType.MARKET,
        quantity=Decimal("0.05"),
        reduce_only=True,
        position_id=position.id,
    )
    broker.submit(close, datetime(2024, 1, 2, 0, 2, tzinfo=UTC))
    broker.process_bar(make_bar(2, open_price="2652"))
    assert entry.status is OrderStatus.FILLED
    assert broker.portfolio.balance == Decimal("10010.00")
    assert broker.portfolio.trades[0].gross_pnl == Decimal("10.00")
    broker.portfolio.assert_reconciled()


def test_short_pnl_is_symmetric(broker: Broker) -> None:
    broker.submit(request("short", Side.SELL), datetime(2024, 1, 2, 0, 0, tzinfo=UTC))
    broker.process_bar(make_bar(0, open_price="2652"))
    position = next(iter(broker.portfolio.positions.values()))
    broker.submit(
        OrderRequest(
            request_id="cover",
            instrument_id="XAUUSD",
            side=Side.BUY,
            type=OrderType.MARKET,
            quantity=Decimal("0.05"),
            reduce_only=True,
            position_id=position.id,
        ),
        datetime(2024, 1, 2, 0, 1, tzinfo=UTC),
    )
    broker.process_bar(make_bar(1, open_price="2650"))
    assert broker.portfolio.balance == Decimal("10010.00")


def test_limit_price_improves_and_never_violates_limit(broker: Broker) -> None:
    order = broker.submit(
        request("limit", Side.BUY, OrderType.LIMIT, limit_price=Decimal("2650")),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )
    broker.process_bar(make_bar(0, open_price="2649", high="2651", low="2648"))
    assert order.average_fill_price == Decimal("2649")
    assert order.average_fill_price <= Decimal("2650")


def test_stop_gaps_to_adverse_open(broker: Broker) -> None:
    order = broker.submit(
        request("stop", Side.BUY, OrderType.STOP, stop_price=Decimal("2650")),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )
    broker.process_bar(make_bar(0, open_price="2655", high="2656", low="2654"))
    assert order.average_fill_price == Decimal("2655")


def test_stop_limit_can_trigger_without_filling(broker: Broker) -> None:
    order = broker.submit(
        request(
            "stop-limit",
            Side.BUY,
            OrderType.STOP_LIMIT,
            stop_price=Decimal("2650"),
            limit_price=Decimal("2651"),
        ),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )
    broker.process_bar(make_bar(0, open_price="2655", high="2656", low="2654"))
    assert order.status is OrderStatus.TRIGGERED
    assert not broker.fills


def test_cancel_prevents_later_fill(broker: Broker) -> None:
    order = broker.submit(
        request("limit", Side.BUY, OrderType.LIMIT, limit_price=Decimal("2600")),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )
    broker.cancel(order.id, datetime(2024, 1, 2, 0, 0, 30, tzinfo=UTC))
    broker.process_bar(make_bar(0, open_price="2600"))
    assert order.status is OrderStatus.CANCELLED
    assert not broker.fills


def test_request_id_is_idempotent_and_conflicts_reject(broker: Broker) -> None:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    original = request("same", Side.BUY)
    assert broker.submit(original, now) is broker.submit(original, now)
    with pytest.raises(SimulationError, match="different payload"):
        broker.submit(request("same", Side.SELL), now)


def test_warmup_order_is_rejected(broker: Broker) -> None:
    order = broker.submit(
        request("warmup", Side.BUY), datetime(2024, 1, 2, tzinfo=UTC), warmup=True
    )
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_code == "WARMUP_ORDER"


def test_market_closed_order_is_rejected(broker: Broker) -> None:
    order = broker.submit(request("closed", Side.BUY), datetime(2024, 1, 3, tzinfo=UTC))
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_code == "MARKET_CLOSED"


def test_submission_reserves_margin(broker: Broker) -> None:
    broker.process_bar(make_bar(0, open_price="2650"))
    oversized = OrderRequest(
        request_id="oversized",
        instrument_id="XAUUSD",
        side=Side.BUY,
        type=OrderType.MARKET,
        quantity=Decimal("2.00"),
    )
    order = broker.submit(oversized, datetime(2024, 1, 2, 0, 1, tzinfo=UTC))
    assert order.status is OrderStatus.REJECTED
    assert order.rejection_code == "INSUFFICIENT_MARGIN"


def test_daily_financing_posts_to_ledger(
    instrument: Instrument, execution: ExecutionConfig
) -> None:
    portfolio = Portfolio(
        starting_balance=Decimal("10000"),
        instrument=instrument,
        position_mode=PositionMode.NETTING,
    )
    financed = Broker(
        instrument=instrument,
        execution=replace(
            execution,
            financing_long_per_lot_daily=Decimal("1"),
            financing_rollover_time=clock_time(0, 2),
        ),
        portfolio=portfolio,
        stop_out_percent=Decimal("50"),
    )
    financed.submit(request("entry", Side.BUY), datetime(2024, 1, 2, tzinfo=UTC))
    financed.process_bar(make_bar(0))
    financed.process_bar(make_bar(1))
    assert portfolio.balance == Decimal("9999.95")
    assert portfolio.ledger[-1].kind == "FINANCING"


def test_partial_close_preserves_remaining_quantity(broker: Broker) -> None:
    broker.submit(request("entry", Side.BUY), datetime(2024, 1, 2, tzinfo=UTC))
    broker.process_bar(make_bar(0, open_price="2650"))
    position = next(iter(broker.portfolio.positions.values()))
    broker.submit(
        OrderRequest(
            request_id="partial",
            instrument_id="XAUUSD",
            side=Side.SELL,
            type=OrderType.MARKET,
            quantity=Decimal("0.02"),
            reduce_only=True,
            position_id=position.id,
        ),
        datetime(2024, 1, 2, 0, 1, tzinfo=UTC),
    )
    broker.process_bar(make_bar(1, open_price="2652"))
    assert position.quantity == Decimal("0.03")
    assert broker.portfolio.trades[0].gross_pnl == Decimal("4.00")


def test_stop_wins_when_stop_and_take_profit_share_bar(broker: Broker) -> None:
    broker.submit(
        request(
            "protected",
            Side.BUY,
            stop_loss=Decimal("2649"),
            take_profit=Decimal("2651"),
        ),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    broker.process_bar(make_bar(0, open_price="2650"))
    broker.process_bar(make_bar(1, open_price="2650", high="2652", low="2648"))
    trade = broker.portfolio.trades[0]
    assert trade.exit_price == Decimal("2649")
    assert trade.ambiguous is True


def test_expiry_precedes_matching_at_same_timestamp(broker: Broker) -> None:
    order = broker.submit(
        OrderRequest(
            request_id="expires",
            instrument_id="XAUUSD",
            side=Side.BUY,
            type=OrderType.LIMIT,
            quantity=Decimal("0.05"),
            limit_price=Decimal("2650"),
            expire_time=datetime(2024, 1, 2, 0, 1, tzinfo=UTC),
        ),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    broker.process_bar(make_bar(0, open_price="2650"))
    assert order.status is OrderStatus.EXPIRED
    assert not broker.fills
