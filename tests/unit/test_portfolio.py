from datetime import UTC, datetime
from decimal import Decimal

from backtest.account import Portfolio
from backtest.domain.models import Fill, Instrument, PositionMode, Side


def fill(identifier: str, side: Side, quantity: str, price: str, position: str = "") -> Fill:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return Fill(
        identifier,
        f"order-{identifier}",
        "XAUUSD",
        position,
        now,
        now,
        side,
        Decimal(quantity),
        Decimal(price),
        Decimal("0"),
        Decimal("0"),
        "test",
    )


def test_netting_opposite_fill_reduces_then_reverses(instrument: Instrument) -> None:
    portfolio = Portfolio(
        starting_balance=Decimal("10000"),
        instrument=instrument,
        position_mode=PositionMode.NETTING,
    )
    first = portfolio.apply_fill(fill("1", Side.BUY, "0.05", "2650"))
    portfolio.apply_fill(fill("2", Side.SELL, "0.08", "2652"))
    assert portfolio.balance == Decimal("10010.00")
    remaining = next(iter(portfolio.positions.values()))
    assert remaining.id != first
    assert remaining.side is Side.SELL
    assert remaining.quantity == Decimal("0.03")
    portfolio.assert_reconciled()


def test_hedging_keeps_independent_positions(instrument: Instrument) -> None:
    portfolio = Portfolio(
        starting_balance=Decimal("10000"),
        instrument=instrument,
        position_mode=PositionMode.HEDGING,
    )
    portfolio.apply_fill(fill("1", Side.BUY, "0.01", "2650"))
    portfolio.apply_fill(fill("2", Side.SELL, "0.01", "2651"))
    assert len(portfolio.positions) == 2
