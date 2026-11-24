from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

ZERO = Decimal("0")


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    GTD = "GTD"
    DAY = "DAY"


class OrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"

    @property
    def terminal(self) -> bool:
        return self in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        }


class PositionMode(StrEnum):
    HEDGING = "hedging"
    NETTING = "netting"


class PriceBasis(StrEnum):
    BID = "bid"
    ASK = "ask"
    MID = "mid"


@dataclass(frozen=True, slots=True)
class Instrument:
    id: str
    currency: str
    contract_size: Decimal
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    leverage: Decimal
    spread: Decimal
    session_timezone: str
    sessions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    price_basis: PriceBasis
    volume: Decimal | None = None
    tick_count: int | None = None
    synthetic: bool = False

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None or self.close_time.tzinfo is None:
            raise ValueError("bar timestamps must be timezone-aware")
        if self.open_time >= self.close_time:
            raise ValueError("bar open_time must precede close_time")
        if min(self.open, self.high, self.low, self.close) <= ZERO:
            raise ValueError("bar prices must be positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("bar violates OHLC consistency")
        if self.low > self.high:
            raise ValueError("bar low exceeds high")

    @property
    def event_time(self) -> datetime:
        return self.close_time.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    request_id: str
    instrument_id: str
    side: Side
    type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    expire_time: datetime | None = None
    client_tag: str | None = None
    reduce_only: bool = False
    position_id: str | None = None


@dataclass(slots=True)
class Order:
    id: str
    request: OrderRequest
    submitted_at: datetime
    eligible_after: datetime
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_quantity: Decimal = ZERO
    average_fill_price: Decimal | None = None
    rejection_code: str | None = None
    rejection_reason: str | None = None
    version: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def remaining_quantity(self) -> Decimal:
        return self.request.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class Fill:
    id: str
    order_id: str
    instrument_id: str
    position_id: str
    timestamp: datetime
    known_at: datetime
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    slippage: Decimal
    reason: str
    modeled: bool = True
    ambiguous: bool = False


@dataclass(slots=True)
class Position:
    id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    average_price: Decimal
    opened_at: datetime
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    realized_pnl: Decimal = ZERO
    commission: Decimal = ZERO
    client_tag: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: str
    timestamp: datetime
    kind: str
    amount: Decimal
    balance_after: Decimal
    order_id: str | None = None
    position_id: str | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    timestamp: datetime
    balance: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    used_margin: Decimal
    free_margin: Decimal
    margin_level: Decimal | None


@dataclass(frozen=True, slots=True)
class Trade:
    position_id: str
    instrument_id: str
    side: Side
    opened_at: datetime
    closed_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    reason: str
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class TimerEvent:
    id: str
    timestamp: datetime
    tag: str


def utc_now() -> datetime:
    return datetime.now(UTC)
