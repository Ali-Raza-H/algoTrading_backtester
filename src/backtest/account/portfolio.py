from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from backtest.domain.errors import SimulationError
from backtest.domain.models import (
    AccountSnapshot,
    Fill,
    Instrument,
    LedgerEntry,
    Position,
    PositionMode,
    Side,
    Trade,
)


class Portfolio:
    def __init__(
        self,
        *,
        starting_balance: Decimal,
        instrument: Instrument,
        position_mode: PositionMode,
    ) -> None:
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.instrument = instrument
        self.position_mode = position_mode
        self.positions: dict[str, Position] = {}
        self.ledger: list[LedgerEntry] = []
        self.trades: list[Trade] = []
        self.snapshots: list[AccountSnapshot] = []
        self.current_snapshot: AccountSnapshot | None = None
        self._ledger_sequence = 0
        self._position_sequence = 0

    def next_position_id(self) -> str:
        self._position_sequence += 1
        return f"pos-{self._position_sequence:08d}"

    def _post(
        self,
        timestamp: datetime,
        kind: str,
        amount: Decimal,
        *,
        order_id: str | None = None,
        position_id: str | None = None,
        description: str = "",
    ) -> None:
        self.balance += amount
        self._ledger_sequence += 1
        self.ledger.append(
            LedgerEntry(
                id=f"ledger-{self._ledger_sequence:09d}",
                timestamp=timestamp,
                kind=kind,
                amount=amount,
                balance_after=self.balance,
                order_id=order_id,
                position_id=position_id,
                description=description,
            )
        )

    def _gross_pnl(
        self, side: Side, entry: Decimal, exit_price: Decimal, quantity: Decimal
    ) -> Decimal:
        return (exit_price - entry) * Decimal(side.sign) * quantity * self.instrument.contract_size

    def _close(
        self,
        position: Position,
        fill: Fill,
        quantity: Decimal,
        reason: str,
        *,
        ambiguous: bool = False,
    ) -> None:
        if quantity <= 0 or quantity > position.quantity:
            raise SimulationError("invalid close quantity")
        original_quantity = position.quantity
        entry_cost = position.commission * quantity / original_quantity
        gross = self._gross_pnl(position.side, position.average_price, fill.price, quantity)
        self._post(
            fill.timestamp,
            "REALIZED_PNL",
            gross,
            order_id=fill.order_id,
            position_id=position.id,
            description=reason,
        )
        position.quantity -= quantity
        position.realized_pnl += gross
        position.commission -= entry_cost
        self.trades.append(
            Trade(
                position_id=position.id,
                instrument_id=position.instrument_id,
                side=position.side,
                opened_at=position.opened_at,
                closed_at=fill.timestamp,
                quantity=quantity,
                entry_price=position.average_price,
                exit_price=fill.price,
                gross_pnl=gross,
                costs=entry_cost + fill.commission,
                net_pnl=gross - entry_cost - fill.commission,
                reason=reason,
                ambiguous=ambiguous,
            )
        )
        if position.quantity == 0:
            del self.positions[position.id]

    def apply_fill(
        self,
        fill: Fill,
        *,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        reduce_only: bool = False,
        target_position_id: str | None = None,
        client_tag: str | None = None,
        reason: str | None = None,
        ambiguous: bool = False,
    ) -> str:
        self._post(
            fill.timestamp,
            "COMMISSION",
            -fill.commission,
            order_id=fill.order_id,
            position_id=target_position_id,
            description="fill commission",
        )
        if target_position_id is not None:
            position = self.positions.get(target_position_id)
            if position is None:
                raise SimulationError(f"position {target_position_id} is not open")
            if fill.side is not position.side.opposite:
                raise SimulationError("close fill side does not reduce target position")
            self._close(
                position,
                fill,
                min(fill.quantity, position.quantity),
                reason or fill.reason,
                ambiguous=ambiguous,
            )
            return target_position_id

        if self.position_mode is PositionMode.HEDGING:
            if reduce_only:
                candidates = sorted(
                    (
                        position
                        for position in self.positions.values()
                        if position.instrument_id == fill.instrument_id
                        and position.side is fill.side.opposite
                    ),
                    key=lambda item: (item.opened_at, item.id),
                )
                remaining = fill.quantity
                for position in candidates:
                    close_quantity = min(remaining, position.quantity)
                    allocated_fill = replace(
                        fill,
                        quantity=close_quantity,
                        commission=fill.commission * close_quantity / fill.quantity,
                    )
                    self._close(
                        position,
                        allocated_fill,
                        close_quantity,
                        reason or fill.reason,
                    )
                    remaining -= close_quantity
                    if remaining == 0:
                        return position.id
                if remaining > 0:
                    raise SimulationError("reduce-only fill exceeds open exposure")
            position_id = self.next_position_id()
            self.positions[position_id] = Position(
                id=position_id,
                instrument_id=fill.instrument_id,
                side=fill.side,
                quantity=fill.quantity,
                average_price=fill.price,
                opened_at=fill.timestamp,
                stop_loss=stop_loss,
                take_profit=take_profit,
                commission=fill.commission,
                client_tag=client_tag,
            )
            return position_id

        existing = next(
            (
                position
                for position in self.positions.values()
                if position.instrument_id == fill.instrument_id
            ),
            None,
        )
        if existing is None:
            if reduce_only:
                raise SimulationError("reduce-only fill has no open exposure")
            position_id = self.next_position_id()
            self.positions[position_id] = Position(
                id=position_id,
                instrument_id=fill.instrument_id,
                side=fill.side,
                quantity=fill.quantity,
                average_price=fill.price,
                opened_at=fill.timestamp,
                stop_loss=stop_loss,
                take_profit=take_profit,
                commission=fill.commission,
                client_tag=client_tag,
            )
            return position_id
        if existing.side is fill.side:
            if reduce_only:
                raise SimulationError("reduce-only fill would increase exposure")
            total = existing.quantity + fill.quantity
            existing.average_price = (
                existing.average_price * existing.quantity + fill.price * fill.quantity
            ) / total
            existing.quantity = total
            existing.commission += fill.commission
            existing.stop_loss = stop_loss if stop_loss is not None else existing.stop_loss
            existing.take_profit = take_profit if take_profit is not None else existing.take_profit
            return existing.id

        closed = min(existing.quantity, fill.quantity)
        close_fill = replace(
            fill,
            quantity=closed,
            commission=fill.commission * closed / fill.quantity,
        )
        self._close(
            existing,
            close_fill,
            closed,
            reason or fill.reason,
            ambiguous=ambiguous,
        )
        remainder = fill.quantity - closed
        if remainder > 0:
            if reduce_only:
                raise SimulationError("reduce-only fill would reverse exposure")
            position_id = self.next_position_id()
            remaining_commission = fill.commission * remainder / fill.quantity
            self.positions[position_id] = Position(
                id=position_id,
                instrument_id=fill.instrument_id,
                side=fill.side,
                quantity=remainder,
                average_price=fill.price,
                opened_at=fill.timestamp,
                stop_loss=stop_loss,
                take_profit=take_profit,
                commission=remaining_commission,
                client_tag=client_tag,
            )
            return position_id
        return existing.id

    def executable_prices(self, mid: Decimal) -> tuple[Decimal, Decimal]:
        half = self.instrument.spread / Decimal(2)
        return mid - half, mid + half

    def apply_financing(
        self,
        timestamp: datetime,
        *,
        long_per_lot: Decimal,
        short_per_lot: Decimal,
        multiplier: int,
    ) -> Decimal:
        total = Decimal(0)
        for position in sorted(self.positions.values(), key=lambda item: item.id):
            rate = long_per_lot if position.side is Side.BUY else short_per_lot
            amount = -(rate * position.quantity * multiplier)
            if amount:
                self._post(
                    timestamp,
                    "FINANCING",
                    amount,
                    position_id=position.id,
                    description=f"daily financing x{multiplier}",
                )
                total += amount
        return total

    def unrealized_pnl(self, mid: Decimal) -> Decimal:
        bid, ask = self.executable_prices(mid)
        total = Decimal(0)
        for position in self.positions.values():
            exit_price = bid if position.side is Side.BUY else ask
            total += self._gross_pnl(
                position.side, position.average_price, exit_price, position.quantity
            )
        return total

    def used_margin(self, mid: Decimal) -> Decimal:
        return sum(
            (
                position.quantity * self.instrument.contract_size * mid / self.instrument.leverage
                for position in self.positions.values()
            ),
            Decimal(0),
        )

    def snapshot(
        self, timestamp: datetime, mid: Decimal, *, record: bool = True
    ) -> AccountSnapshot:
        unrealized = self.unrealized_pnl(mid)
        margin = self.used_margin(mid)
        equity = self.balance + unrealized
        free = equity - margin
        level = equity / margin * Decimal(100) if margin else None
        snapshot = AccountSnapshot(timestamp, self.balance, equity, unrealized, margin, free, level)
        self.current_snapshot = snapshot
        if record:
            self.snapshots.append(snapshot)
        return snapshot

    def assert_reconciled(self) -> None:
        expected = self.starting_balance + sum((entry.amount for entry in self.ledger), Decimal(0))
        if expected != self.balance:
            raise SimulationError(f"ledger does not reconcile: {expected} != {self.balance}")
