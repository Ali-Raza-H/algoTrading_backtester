from __future__ import annotations

from decimal import Decimal

from backtest.domain.models import Bar, Fill, Order, OrderRequest, OrderType, Side, TimerEvent
from backtest.sdk import StrategyContext


class RandomPolicy:
    def __init__(self, parameters: dict[str, object]) -> None:
        self.probability = float(parameters.get("entry_probability", 0.01))
        self.quantity = Decimal(str(parameters.get("quantity", "0.01")))
        self.holding_bars = int(parameters.get("holding_bars", 12))
        self.timeframe = str(parameters.get("timeframe", "1h"))
        self.sequence = 0
        self.age: dict[str, int] = {}

    def on_start(self, ctx: StrategyContext) -> None:
        return None

    def on_warmup(self, event: Bar, ctx: StrategyContext) -> None:
        return None

    def on_market(self, event: Bar, ctx: StrategyContext) -> None:
        if event.timeframe != self.timeframe:
            return
        for position in ctx.positions(event.instrument_id):
            age = self.age.get(position.id, 0) + 1
            self.age[position.id] = age
            if age >= self.holding_bars:
                ctx.close_position(position.id)
        if not ctx.positions(event.instrument_id) and ctx.rng.random() < self.probability:
            self.sequence += 1
            side = Side.BUY if ctx.rng.random() < 0.5 else Side.SELL
            ctx.submit_order(
                OrderRequest(
                    request_id=f"random-{self.sequence}",
                    instrument_id=event.instrument_id,
                    side=side,
                    type=OrderType.MARKET,
                    quantity=self.quantity,
                )
            )

    def on_order_update(self, event: Order, ctx: StrategyContext) -> None:
        return None

    def on_fill(self, event: Fill, ctx: StrategyContext) -> None:
        return None

    def on_timer(self, event: TimerEvent, ctx: StrategyContext) -> None:
        return None

    def on_finish(self, ctx: StrategyContext) -> None:
        return None


def build_strategy(parameters: dict[str, object]) -> RandomPolicy:
    return RandomPolicy(parameters)
