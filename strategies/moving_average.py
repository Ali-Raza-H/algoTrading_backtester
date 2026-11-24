from __future__ import annotations

from decimal import Decimal

from backtest.domain.models import Bar, Fill, Order, OrderRequest, OrderType, Side, TimerEvent
from backtest.sdk import StrategyContext


class MovingAverageCross:
    def __init__(self, parameters: dict[str, object]) -> None:
        self.fast = int(parameters.get("fast_period", 20))
        self.slow = int(parameters.get("slow_period", 50))
        self.quantity = Decimal(str(parameters.get("quantity", "0.01")))
        self._sequence = 0
        self._last_relation: int | None = None

    def on_start(self, ctx: StrategyContext) -> None:
        ctx.log("info", "moving-average strategy started")

    def on_warmup(self, event: Bar, ctx: StrategyContext) -> None:
        return None

    def on_market(self, event: Bar, ctx: StrategyContext) -> None:
        if event.timeframe != "1h":
            return
        bars = ctx.bars(event.instrument_id, "1h", self.slow)
        if len(bars) < self.slow:
            return
        fast = sum((item.close for item in bars[-self.fast :]), Decimal(0)) / self.fast
        slow = sum((item.close for item in bars), Decimal(0)) / self.slow
        relation = 1 if fast > slow else -1 if fast < slow else 0
        if self._last_relation is not None and relation and relation != self._last_relation:
            self._sequence += 1
            ctx.submit_order(
                OrderRequest(
                    request_id=f"ma-{self._sequence}",
                    instrument_id=event.instrument_id,
                    side=Side.BUY if relation > 0 else Side.SELL,
                    type=OrderType.MARKET,
                    quantity=self.quantity,
                )
            )
        if relation:
            self._last_relation = relation

    def on_order_update(self, event: Order, ctx: StrategyContext) -> None:
        return None

    def on_fill(self, event: Fill, ctx: StrategyContext) -> None:
        return None

    def on_timer(self, event: TimerEvent, ctx: StrategyContext) -> None:
        return None

    def on_finish(self, ctx: StrategyContext) -> None:
        ctx.log("info", "moving-average strategy finished")


def build_strategy(parameters: dict[str, object]) -> MovingAverageCross:
    return MovingAverageCross(parameters)
