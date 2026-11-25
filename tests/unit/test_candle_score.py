from datetime import UTC, datetime
from decimal import Decimal

from backtest.domain.models import Bar, PriceBasis
from strategies.candle_score import CandleScoreStrategy


def bar(price_open: str, price_close: str) -> Bar:
    value_open = Decimal(price_open)
    value_close = Decimal(price_close)
    return Bar(
        instrument_id="XAUUSD",
        timeframe="1h",
        open_time=datetime(2024, 1, 1, tzinfo=UTC),
        close_time=datetime(2024, 1, 1, 1, tzinfo=UTC),
        open=value_open,
        high=max(value_open, value_close),
        low=min(value_open, value_close),
        close=value_close,
        price_basis=PriceBasis.MID,
    )


def test_large_green_candle_is_worth_two_points() -> None:
    strategy = CandleScoreStrategy({"big_move_percent": "1.0"})
    assert strategy._score_total((bar("100", "101"),)) == Decimal("2")
