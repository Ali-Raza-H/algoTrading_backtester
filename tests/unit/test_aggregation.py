from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backtest.domain.models import Bar, PriceBasis
from backtest.market.aggregation import TimeframeAggregator


def source_bar(minute: int, price: int) -> Bar:
    start = datetime(2024, 1, 1, 9, minute, tzinfo=UTC)
    value = Decimal(price)
    return Bar(
        "XAUUSD",
        "1m",
        start,
        start + timedelta(minutes=1),
        value,
        value + 1,
        value - 1,
        value,
        PriceBasis.MID,
        Decimal("1"),
    )


def test_partial_bar_is_not_emitted_before_boundary() -> None:
    aggregator = TimeframeAggregator("1m", ("15m",))
    emitted = []
    for minute in range(45, 47):
        emitted.extend(aggregator.update(source_bar(minute, 100 + minute)))
    assert all(item.timeframe != "15m" for item in emitted)


def test_completed_bar_uses_only_half_open_interval() -> None:
    aggregator = TimeframeAggregator("1m", ("15m",))
    emitted = []
    for minute in range(45, 60):
        emitted.extend(aggregator.update(source_bar(minute, 100 + minute)))
    aggregate = next(item for item in emitted if item.timeframe == "15m")
    assert aggregate.open_time == datetime(2024, 1, 1, 9, 45, tzinfo=UTC)
    assert aggregate.close_time == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert aggregate.open == Decimal("145")
    assert aggregate.close == Decimal("159")
    assert aggregate.volume == Decimal("15")
