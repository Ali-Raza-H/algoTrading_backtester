from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backtest.domain.models import Bar
from backtest.market.timeframes import Timeframe, parse_timeframe


@dataclass(slots=True)
class _Bucket:
    first: Bar
    timeframe: Timeframe
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    count: int

    def add(self, bar: Bar) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        if self.volume is not None and bar.volume is not None:
            self.volume += bar.volume
        else:
            self.volume = None
        self.count += 1

    def finish(self) -> Bar:
        open_time = self.timeframe.floor(self.first.open_time)
        return Bar(
            instrument_id=self.first.instrument_id,
            timeframe=self.timeframe.label,
            open_time=open_time,
            close_time=open_time + self.timeframe.duration,
            open=self.first.open,
            high=self.high,
            low=self.low,
            close=self.close,
            price_basis=self.first.price_basis,
            volume=self.volume,
            tick_count=self.count,
        )


class TimeframeAggregator:
    def __init__(self, source_timeframe: str, targets: tuple[str, ...]) -> None:
        self.source = parse_timeframe(source_timeframe)
        parsed = {parse_timeframe(item) for item in targets}
        parsed.add(self.source)
        if any(item.seconds < self.source.seconds for item in parsed):
            raise ValueError("target timeframe cannot be finer than source")
        if any(item.seconds % self.source.seconds for item in parsed):
            raise ValueError("target timeframe must align with source timeframe")
        self.targets = tuple(sorted(parsed))
        self._buckets: dict[str, _Bucket] = {}

    def update(self, source: Bar) -> list[Bar]:
        emitted: list[Bar] = []
        for timeframe in self.targets:
            if timeframe == self.source:
                emitted.append(source)
                continue
            bucket_open = timeframe.floor(source.open_time)
            key = timeframe.label
            bucket = self._buckets.get(key)
            if bucket is None or timeframe.floor(bucket.first.open_time) != bucket_open:
                bucket = _Bucket(
                    first=source,
                    timeframe=timeframe,
                    high=source.high,
                    low=source.low,
                    close=source.close,
                    volume=source.volume,
                    count=1,
                )
                self._buckets[key] = bucket
            elif bucket.first is not source:
                bucket.add(source)
            expected_close = bucket_open + timeframe.duration
            if source.close_time == expected_close:
                emitted.append(bucket.finish())
                del self._buckets[key]
        return sorted(emitted, key=lambda item: parse_timeframe(item.timeframe).seconds)
