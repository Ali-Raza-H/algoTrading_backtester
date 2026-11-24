from __future__ import annotations

import heapq
import random
from collections import deque
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from backtest.account import Portfolio
from backtest.broker import Broker
from backtest.domain.models import (
    AccountSnapshot,
    Bar,
    Order,
    OrderRequest,
    OrderType,
    Position,
    TimerEvent,
)


class StrategyContext:
    def __init__(
        self,
        *,
        broker: Broker,
        portfolio: Portfolio,
        seed: int,
        history_sizes: dict[str, int],
    ) -> None:
        self._broker = broker
        self._portfolio = portfolio
        self._now: datetime | None = None
        self._warmup = True
        self._history: dict[str, deque[Bar]] = {
            timeframe: deque(maxlen=max(1, size)) for timeframe, size in history_sizes.items()
        }
        self._logs: list[dict[str, Any]] = []
        self._timers: list[tuple[datetime, int, str]] = []
        self._cancelled_timers: set[str] = set()
        self._timer_sequence = 0
        self.rng = random.Random(seed)

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("strategy clock has not started")
        return self._now

    @property
    def is_warmup(self) -> bool:
        return self._warmup

    def _advance(self, now: datetime, *, warmup: bool) -> None:
        if self._now is not None and now < self._now:
            raise RuntimeError("strategy clock cannot move backwards")
        self._now = now
        self._warmup = warmup

    def _append_bar(self, bar: Bar) -> None:
        history = self._history.get(bar.timeframe)
        if history is not None:
            history.append(bar)

    def bars(self, instrument: str, timeframe: str, count: int) -> tuple[Bar, ...]:
        if instrument != self._broker.instrument.id:
            return ()
        if count < 1:
            raise ValueError("count must be positive")
        history = self._history.get(timeframe)
        if history is None:
            raise ValueError(f"timeframe {timeframe!r} is not subscribed")
        return tuple(history)[-count:]

    def account(self) -> AccountSnapshot | None:
        return self._portfolio.current_snapshot

    def positions(self, instrument: str | None = None) -> tuple[Position, ...]:
        positions: Iterable[Position] = self._portfolio.positions.values()
        if instrument is not None:
            positions = (item for item in positions if item.instrument_id == instrument)
        return tuple(deepcopy(item) for item in positions)

    def orders(self, instrument: str | None = None) -> tuple[Order, ...]:
        orders: Iterable[Order] = self._broker.orders.values()
        if instrument is not None:
            orders = (item for item in orders if item.request.instrument_id == instrument)
        return tuple(deepcopy(item) for item in orders)

    def submit_order(self, request: OrderRequest) -> Order:
        return self._broker.submit(request, self.now, warmup=self._warmup)

    def cancel_order(self, order_id: str) -> Order:
        return self._broker.cancel(order_id, self.now)

    def close_position(self, position_id: str, quantity: Decimal | None = None) -> Order:
        position = self._portfolio.positions.get(position_id)
        if position is None:
            raise ValueError(f"position {position_id!r} is not open")
        requested = quantity if quantity is not None else position.quantity
        if requested <= 0 or requested > position.quantity:
            raise ValueError("close quantity must be positive and cannot exceed the position")
        return self.submit_order(
            OrderRequest(
                request_id=f"close-{position_id}-{self.now.isoformat()}-{requested}",
                instrument_id=position.instrument_id,
                side=position.side.opposite,
                type=OrderType.MARKET,
                quantity=requested,
                reduce_only=True,
                position_id=position_id,
            )
        )

    def modify_order(
        self,
        order_id: str,
        *,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> Order:
        return self._broker.modify(
            order_id,
            self.now,
            limit_price=limit_price,
            stop_price=stop_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def schedule_at(self, timestamp: datetime, tag: str) -> str:
        if timestamp.tzinfo is None:
            raise ValueError("timer timestamp must be timezone-aware")
        if timestamp <= self.now:
            raise ValueError("timer timestamp must be in the future")
        self._timer_sequence += 1
        timer_id = f"timer-{self._timer_sequence:08d}"
        heapq.heappush(self._timers, (timestamp, self._timer_sequence, tag))
        return timer_id

    def cancel_timer(self, timer_id: str) -> None:
        self._cancelled_timers.add(timer_id)

    def _due_timers(self, timestamp: datetime) -> tuple[TimerEvent, ...]:
        due = []
        while self._timers and self._timers[0][0] <= timestamp:
            scheduled, sequence, tag = heapq.heappop(self._timers)
            timer_id = f"timer-{sequence:08d}"
            if timer_id not in self._cancelled_timers:
                due.append(TimerEvent(timer_id, scheduled, tag))
        return tuple(due)

    def log(self, level: str, message: str, fields: dict[str, Any] | None = None) -> None:
        self._logs.append(
            {
                "timestamp": self.now.isoformat(),
                "level": level.upper(),
                "message": message,
                "fields": dict(fields or {}),
            }
        )

    @property
    def logs(self) -> tuple[MappingProxyType[str, Any], ...]:
        return tuple(MappingProxyType(item) for item in self._logs)
