from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from backtest.domain.errors import ConfigError

_PATTERN = re.compile(r"^(\d+)([mhdw])$", re.IGNORECASE)


@dataclass(frozen=True, order=True, slots=True)
class Timeframe:
    seconds: int
    label: str

    @property
    def duration(self) -> timedelta:
        return timedelta(seconds=self.seconds)

    def floor(self, value: datetime) -> datetime:
        epoch = int(value.timestamp())
        return datetime.fromtimestamp(epoch - (epoch % self.seconds), tz=value.tzinfo)


def parse_timeframe(value: str) -> Timeframe:
    normalized = value.strip()
    match = _PATTERN.fullmatch(normalized)
    if match is None:
        raise ConfigError(f"unsupported fixed timeframe {value!r}")
    amount = int(match.group(1))
    if amount < 1:
        raise ConfigError("timeframe must be positive")
    unit = match.group(2).lower()
    multiplier = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}[unit]
    return Timeframe(amount * multiplier, f"{amount}{unit}")
