from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from backtest.domain.errors import ConfigError

_DAYS = {
    name: index for index, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
}


@dataclass(frozen=True, slots=True)
class Session:
    weekday: int
    start: time
    end: time


class SessionCalendar:
    def __init__(self, timezone: str, definitions: tuple[str, ...]) -> None:
        self.timezone = ZoneInfo(timezone)
        self.sessions = tuple(self._parse(item) for item in definitions)

    @staticmethod
    def _clock(value: str) -> time:
        try:
            return time.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"invalid session time {value!r}") from exc

    @classmethod
    def _parse(cls, value: str) -> Session:
        try:
            day, interval = value.split(maxsplit=1)
            start, end = interval.split("-", maxsplit=1)
            weekday = _DAYS[day.title()]
        except (ValueError, KeyError) as exc:
            raise ConfigError(
                f"invalid session {value!r}; expected format 'Mon 00:00-23:59'"
            ) from exc
        return Session(weekday, cls._clock(start), cls._clock(end))

    def is_open(self, timestamp: datetime) -> bool:
        local = timestamp.astimezone(self.timezone)
        current = local.timetz().replace(tzinfo=None)
        for session in self.sessions:
            if session.start <= session.end:
                if local.weekday() == session.weekday and session.start <= current <= session.end:
                    return True
            else:
                if local.weekday() == session.weekday and current >= session.start:
                    return True
                if local.weekday() == (session.weekday + 1) % 7 and current <= session.end:
                    return True
        return False
