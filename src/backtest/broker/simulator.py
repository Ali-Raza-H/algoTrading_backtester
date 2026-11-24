from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from backtest.account import Portfolio
from backtest.config import ExecutionConfig
from backtest.domain.errors import SimulationError
from backtest.domain.models import (
    ZERO,
    Bar,
    Fill,
    Instrument,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    PriceBasis,
    Side,
)
from backtest.market.calendar import SessionCalendar


class Broker:
    def __init__(
        self,
        *,
        instrument: Instrument,
        execution: ExecutionConfig,
        portfolio: Portfolio,
        stop_out_percent: Decimal,
    ) -> None:
        self.instrument = instrument
        self.execution = execution
        self.portfolio = portfolio
        self.stop_out_percent = stop_out_percent
        self.calendar = SessionCalendar(instrument.session_timezone, instrument.sessions)
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.events: list[dict[str, Any]] = []
        self._order_sequence = 0
        self._fill_sequence = 0
        self._requests: dict[str, tuple[OrderRequest, str]] = {}
        self.current_mid: Decimal | None = None
        self._previous_bar_close: datetime | None = None

    def _event(self, timestamp: datetime, kind: str, **fields: Any) -> None:
        self.events.append({"timestamp": timestamp.isoformat(), "kind": kind, **fields})

    def _reject(self, order: Order, code: str, reason: str) -> Order:
        order.status = OrderStatus.REJECTED
        order.rejection_code = code
        order.rejection_reason = reason
        order.history.append({"status": order.status.value, "code": code, "reason": reason})
        self._event(
            order.submitted_at, "order_rejected", order_id=order.id, code=code, reason=reason
        )
        return order

    def submit(self, request: OrderRequest, now: datetime, *, warmup: bool = False) -> Order:
        prior = self._requests.get(request.request_id)
        if prior is not None:
            previous_request, order_id = prior
            if previous_request == request:
                return self.orders[order_id]
            raise SimulationError(
                f"request_id {request.request_id!r} reused with different payload"
            )
        self._order_sequence += 1
        order = Order(
            id=f"order-{self._order_sequence:08d}",
            request=request,
            submitted_at=now,
            eligible_after=now,
        )
        self.orders[order.id] = order
        self._requests[request.request_id] = (request, order.id)
        if warmup:
            return self._reject(order, "WARMUP_ORDER", "orders are disabled during warmup")
        if not self.calendar.is_open(now):
            return self._reject(order, "MARKET_CLOSED", "instrument session is closed")
        reason = self._validate(request, now)
        if reason is not None:
            return self._reject(order, reason[0], reason[1])
        order.status = OrderStatus.ACTIVE
        order.history.append({"status": order.status.value, "timestamp": now.isoformat()})
        self._event(now, "order_accepted", order_id=order.id)
        return order

    def _validate(self, request: OrderRequest, now: datetime) -> tuple[str, str] | None:
        if request.instrument_id != self.instrument.id:
            return "UNKNOWN_INSTRUMENT", "instrument is not subscribed"
        if (
            request.quantity < self.instrument.min_quantity
            or request.quantity > self.instrument.max_quantity
        ):
            return "INVALID_QUANTITY", "quantity is outside instrument bounds"
        if request.quantity % self.instrument.quantity_step != 0:
            return "INVALID_QUANTITY_STEP", "quantity does not align to quantity_step"
        if request.type is OrderType.MARKET and (request.limit_price or request.stop_price):
            return "INVALID_FIELDS", "market orders cannot have limit or stop prices"
        if request.type is OrderType.LIMIT and request.limit_price is None:
            return "MISSING_LIMIT_PRICE", "limit order requires limit_price"
        if request.type is OrderType.STOP and request.stop_price is None:
            return "MISSING_STOP_PRICE", "stop order requires stop_price"
        if request.type is OrderType.STOP_LIMIT and (
            request.stop_price is None or request.limit_price is None
        ):
            return "MISSING_TRIGGER_PRICE", "stop-limit requires stop_price and limit_price"
        if request.expire_time is not None and (
            request.expire_time.tzinfo is None or request.expire_time <= now
        ):
            return "INVALID_EXPIRY", "expiry must be timezone-aware and in the future"
        for label, value in (
            ("limit_price", request.limit_price),
            ("stop_price", request.stop_price),
            ("stop_loss", request.stop_loss),
            ("take_profit", request.take_profit),
        ):
            if value is not None and (value <= 0 or value % self.instrument.price_tick != 0):
                return "INVALID_PRICE", f"{label} must be positive and aligned to price_tick"
        if request.position_id:
            position = self.portfolio.positions.get(request.position_id)
            if position is None:
                return "UNKNOWN_POSITION", "target position is not open"
            if request.quantity > position.quantity:
                return "INVALID_CLOSE_QUANTITY", "close quantity exceeds open position"
            if request.side is not position.side.opposite:
                return "INVALID_CLOSE_SIDE", "close side does not reduce target position"
        if not request.reduce_only and self.current_mid is not None:
            required = (
                request.quantity
                * self.instrument.contract_size
                * self.current_mid
                / self.instrument.leverage
            )
            snapshot = self.portfolio.snapshot(now, self.current_mid, record=False)
            reserved = self._reserved_margin()
            if required > snapshot.free_margin - reserved:
                return "INSUFFICIENT_MARGIN", "estimated margin exceeds free margin"
        return None

    def _reserved_margin(self) -> Decimal:
        if self.current_mid is None:
            return ZERO
        return sum(
            (
                order.remaining_quantity
                * self.instrument.contract_size
                * self.current_mid
                / self.instrument.leverage
                for order in self.orders.values()
                if order.status in {OrderStatus.ACTIVE, OrderStatus.TRIGGERED}
                and not order.request.reduce_only
            ),
            ZERO,
        )

    def cancel(self, order_id: str, now: datetime) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise SimulationError(f"unknown order {order_id}")
        if order.status.terminal:
            raise SimulationError(f"cannot cancel terminal order {order_id}")
        order.status = OrderStatus.CANCELLED
        order.history.append({"status": order.status.value, "timestamp": now.isoformat()})
        self._event(now, "order_cancelled", order_id=order.id)
        return order

    def modify(
        self,
        order_id: str,
        now: datetime,
        *,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> Order:
        order = self.orders.get(order_id)
        if order is None or order.status.terminal:
            raise SimulationError("only live orders can be modified")
        request = replace(
            order.request,
            limit_price=limit_price if limit_price is not None else order.request.limit_price,
            stop_price=stop_price if stop_price is not None else order.request.stop_price,
            stop_loss=stop_loss if stop_loss is not None else order.request.stop_loss,
            take_profit=take_profit if take_profit is not None else order.request.take_profit,
        )
        reason = self._validate(request, now)
        if reason:
            raise SimulationError(reason[1])
        order.request = request
        order.version += 1
        order.eligible_after = now
        order.history.append(
            {"status": order.status.value, "timestamp": now.isoformat(), "version": order.version}
        )
        self._event(now, "order_modified", order_id=order.id, version=order.version)
        return order

    def _basis_offset(self, side: Side) -> Decimal:
        if side is Side.BUY:
            return self.instrument.spread / 2
        return -self.instrument.spread / 2

    def _side_prices(self, bar: Bar, side: Side) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        if bar.price_basis is PriceBasis.MID:
            offset = self._basis_offset(side)
        elif bar.price_basis is PriceBasis.BID:
            offset = self.instrument.spread if side is Side.BUY else ZERO
        else:
            offset = ZERO if side is Side.BUY else -self.instrument.spread
        return bar.open + offset, bar.high + offset, bar.low + offset, bar.close + offset

    def _mid_close(self, bar: Bar) -> Decimal:
        if bar.price_basis is PriceBasis.MID:
            return bar.close
        if bar.price_basis is PriceBasis.BID:
            return bar.close + self.instrument.spread / 2
        return bar.close - self.instrument.spread / 2

    def _commission(self, quantity: Decimal) -> Decimal:
        return self.execution.commission_per_lot_per_side * quantity

    def _fill(
        self,
        order: Order,
        bar: Bar,
        price: Decimal,
        reason: str,
        *,
        ambiguous: bool = False,
    ) -> Fill | None:
        if not order.request.reduce_only:
            required = (
                order.remaining_quantity
                * self.instrument.contract_size
                * price
                / self.instrument.leverage
            )
            snapshot = self.portfolio.snapshot(bar.close_time, price, record=False)
            if required > snapshot.free_margin:
                self._reject(order, "INSUFFICIENT_MARGIN_AT_FILL", "margin changed before fill")
                return None
        self._fill_sequence += 1
        slippage = self.execution.slippage_ticks * self.instrument.price_tick
        fill = Fill(
            id=f"fill-{self._fill_sequence:09d}",
            order_id=order.id,
            instrument_id=order.request.instrument_id,
            position_id=order.request.position_id or "",
            timestamp=bar.open_time,
            known_at=bar.close_time,
            side=order.request.side,
            quantity=order.remaining_quantity,
            price=price,
            commission=self._commission(order.remaining_quantity),
            slippage=slippage,
            reason=reason,
            ambiguous=ambiguous,
        )
        position_id = self.portfolio.apply_fill(
            fill,
            stop_loss=order.request.stop_loss,
            take_profit=order.request.take_profit,
            reduce_only=order.request.reduce_only,
            target_position_id=order.request.position_id,
            client_tag=order.request.client_tag,
            reason=reason,
            ambiguous=ambiguous,
        )
        fill = replace(fill, position_id=position_id)
        self.fills.append(fill)
        order.filled_quantity += fill.quantity
        order.average_fill_price = fill.price
        order.status = OrderStatus.FILLED
        order.history.append(
            {"status": order.status.value, "timestamp": bar.close_time.isoformat()}
        )
        self._event(bar.close_time, "fill", fill_id=fill.id, order_id=order.id, price=str(price))
        return fill

    def _order_price(self, order: Order, bar: Bar) -> tuple[Decimal, str] | None:
        request = order.request
        open_price, high, low, _ = self._side_prices(bar, request.side)
        adverse = self.execution.slippage_ticks * self.instrument.price_tick
        if request.type is OrderType.MARKET:
            return open_price + adverse * request.side.sign, "market"
        if request.type is OrderType.LIMIT or order.status is OrderStatus.TRIGGERED:
            assert request.limit_price is not None
            if request.side is Side.BUY and low <= request.limit_price:
                return min(open_price, request.limit_price), "limit"
            if request.side is Side.SELL and high >= request.limit_price:
                return max(open_price, request.limit_price), "limit"
            return None
        if request.type is OrderType.STOP:
            assert request.stop_price is not None
            if request.side is Side.BUY and high >= request.stop_price:
                return max(open_price, request.stop_price) + adverse, "stop"
            if request.side is Side.SELL and low <= request.stop_price:
                return min(open_price, request.stop_price) - adverse, "stop"
            return None
        if request.type is OrderType.STOP_LIMIT:
            assert request.stop_price is not None
            triggered = (request.side is Side.BUY and high >= request.stop_price) or (
                request.side is Side.SELL and low <= request.stop_price
            )
            if triggered:
                order.status = OrderStatus.TRIGGERED
                order.history.append(
                    {"status": order.status.value, "timestamp": bar.close_time.isoformat()}
                )
                self._event(bar.close_time, "order_triggered", order_id=order.id)
            return None
        return None

    def _protective_price(self, position: Position, bar: Bar) -> tuple[Decimal, str, bool] | None:
        exit_side = position.side.opposite
        open_price, high, low, _ = self._side_prices(bar, exit_side)
        if position.side is Side.BUY:
            stop_hit = position.stop_loss is not None and low <= position.stop_loss
            take_hit = position.take_profit is not None and high >= position.take_profit
            if stop_hit:
                assert position.stop_loss is not None
                return min(open_price, position.stop_loss), "stop_loss", bool(take_hit)
            if take_hit:
                assert position.take_profit is not None
                return max(open_price, position.take_profit), "take_profit", False
        else:
            stop_hit = position.stop_loss is not None and high >= position.stop_loss
            take_hit = position.take_profit is not None and low <= position.take_profit
            if stop_hit:
                assert position.stop_loss is not None
                return max(open_price, position.stop_loss), "stop_loss", bool(take_hit)
            if take_hit:
                assert position.take_profit is not None
                return min(open_price, position.take_profit), "take_profit", False
        return None

    def _close_position(
        self,
        position: Position,
        bar: Bar,
        price: Decimal,
        reason: str,
        *,
        ambiguous: bool = False,
    ) -> None:
        request = OrderRequest(
            request_id=f"{reason}-{position.id}-{bar.close_time.isoformat()}",
            instrument_id=position.instrument_id,
            side=position.side.opposite,
            type=OrderType.MARKET,
            quantity=position.quantity,
            reduce_only=True,
            position_id=position.id,
        )
        self._order_sequence += 1
        order = Order(
            id=f"order-{self._order_sequence:08d}",
            request=request,
            submitted_at=bar.open_time,
            eligible_after=bar.open_time,
            status=OrderStatus.ACTIVE,
        )
        self.orders[order.id] = order
        self._fill(order, bar, price, reason, ambiguous=ambiguous)

    def process_bar(self, bar: Bar) -> list[Fill]:
        before = len(self.fills)
        self._apply_financing(bar.close_time)
        for order in list(self.orders.values()):
            if order.status.terminal:
                continue
            if (
                order.request.expire_time is not None
                and order.request.expire_time <= bar.close_time
            ):
                order.status = OrderStatus.EXPIRED
                order.history.append(
                    {"status": order.status.value, "timestamp": bar.close_time.isoformat()}
                )
                self._event(bar.close_time, "order_expired", order_id=order.id)

        if not self.calendar.is_open(bar.open_time):
            self.current_mid = self._mid_close(bar)
            self.portfolio.snapshot(bar.close_time, self.current_mid, record=False)
            return []

        for position in list(self.portfolio.positions.values()):
            protective = self._protective_price(position, bar)
            if protective is not None and position.id in self.portfolio.positions:
                price, reason, ambiguous = protective
                self._close_position(position, bar, price, reason, ambiguous=ambiguous)

        for order in list(self.orders.values()):
            if order.status not in {OrderStatus.ACTIVE, OrderStatus.TRIGGERED}:
                continue
            if bar.close_time <= order.eligible_after:
                continue
            order_match = self._order_price(order, bar)
            if order_match is not None:
                price, reason = order_match
                self._fill(order, bar, price, reason)

        current_mid = self._mid_close(bar)
        self.current_mid = current_mid
        snapshot = self.portfolio.snapshot(bar.close_time, current_mid, record=False)
        if (
            snapshot.margin_level is not None
            and snapshot.margin_level <= self.stop_out_percent
            and self.portfolio.positions
        ):
            for order in self.orders.values():
                if not order.status.terminal:
                    order.status = OrderStatus.CANCELLED
            positions = sorted(
                self.portfolio.positions.values(),
                key=lambda item: self._position_unrealized(item, current_mid),
            )
            for position in positions:
                if position.id not in self.portfolio.positions:
                    continue
                exit_side = position.side.opposite
                price = self._side_prices(bar, exit_side)[3]
                self._close_position(position, bar, price, "stop_out")
                current = self.portfolio.snapshot(bar.close_time, current_mid, record=False)
                if current.margin_level is None or current.margin_level > self.stop_out_percent:
                    break
        self.portfolio.assert_reconciled()
        return self.fills[before:]

    def _position_unrealized(self, position: Position, mid: Decimal) -> Decimal:
        bid, ask = self.portfolio.executable_prices(mid)
        exit_price = bid if position.side is Side.BUY else ask
        return (
            (exit_price - position.average_price)
            * Decimal(position.side.sign)
            * position.quantity
            * self.instrument.contract_size
        )

    def _apply_financing(self, current: datetime) -> None:
        previous = self._previous_bar_close
        self._previous_bar_close = current
        if previous is None or not self.portfolio.positions:
            return
        timezone = ZoneInfo(self.execution.financing_timezone)
        previous_local = previous.astimezone(timezone)
        current_local = current.astimezone(timezone)
        day = previous_local.date()
        while day <= current_local.date():
            scheduled = datetime.combine(
                day, self.execution.financing_rollover_time, tzinfo=timezone
            )
            if previous_local < scheduled <= current_local:
                multiplier = 3 if scheduled.weekday() == self.execution.financing_triple_day else 1
                amount = self.portfolio.apply_financing(
                    scheduled,
                    long_per_lot=self.execution.financing_long_per_lot_daily,
                    short_per_lot=self.execution.financing_short_per_lot_daily,
                    multiplier=multiplier,
                )
                if amount:
                    self._event(
                        scheduled,
                        "financing",
                        amount=str(amount),
                        multiplier=multiplier,
                    )
            day += timedelta(days=1)
