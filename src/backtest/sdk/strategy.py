from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from backtest.domain.errors import StrategyError
from backtest.domain.models import Bar, Fill, Order, TimerEvent

if TYPE_CHECKING:
    from backtest.sdk.context import StrategyContext


@runtime_checkable
class Strategy(Protocol):
    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_warmup(self, event: Bar, ctx: StrategyContext) -> None: ...

    def on_market(self, event: Bar, ctx: StrategyContext) -> None: ...

    def on_order_update(self, event: Order, ctx: StrategyContext) -> None: ...

    def on_fill(self, event: Fill, ctx: StrategyContext) -> None: ...

    def on_timer(self, event: TimerEvent, ctx: StrategyContext) -> None: ...

    def on_finish(self, ctx: StrategyContext) -> None: ...


def load_strategy(entrypoint: str, parameters: dict[str, object]) -> Strategy:
    if ":" not in entrypoint:
        raise StrategyError("strategy.entrypoint must use module:function syntax")
    module_name, factory_name = entrypoint.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        strategy: Any = factory(parameters)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise StrategyError(f"cannot load strategy {entrypoint}: {exc}") from exc
    required = ("on_start", "on_warmup", "on_market", "on_order_update", "on_fill", "on_finish")
    missing = [name for name in required if not callable(getattr(strategy, name, None))]
    if missing:
        raise StrategyError(f"strategy is missing callbacks: {', '.join(missing)}")
    return cast(Strategy, strategy)
