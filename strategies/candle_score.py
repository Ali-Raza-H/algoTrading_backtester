from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from backtest.domain.models import Bar, Fill, Order, OrderRequest, OrderType, Side, TimerEvent
from backtest.sdk import StrategyContext


class CandleScoreStrategy:
    """Trade when recent candle momentum exceeds its trailing average."""

    def __init__(self, parameters: dict[str, object]) -> None:
        self.timeframe = str(parameters.get("timeframe", "1h"))
        self.candle_count = int(parameters.get("candle_count", 12))
        self.average_periods = int(parameters.get("average_periods", 5))
        self.big_move_percent = Decimal(str(parameters.get("big_move_percent", "1.0")))
        self.atr_period = int(parameters.get("atr_period", 14))
        self.atr_multiplier = Decimal(str(parameters.get("atr_multiplier", "1.5")))
        self.risk_reward = Decimal(str(parameters.get("risk_reward", "2.0")))
        self.quantity = Decimal(str(parameters.get("quantity", "0.01")))
        self.sequence = 0

        if self.candle_count < 1 or self.average_periods < 1 or self.atr_period < 1:
            raise ValueError("candle_count, average_periods, and atr_period must be positive")
        if self.big_move_percent < 0 or self.atr_multiplier <= 0 or self.risk_reward <= 0:
            raise ValueError(
                "big_move_percent must be non-negative; ATR and RR values must be positive"
            )
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")

    def on_start(self, ctx: StrategyContext) -> None:
        ctx.log("info", "candle-score strategy started", {"timeframe": self.timeframe})

    def on_warmup(self, event: Bar, ctx: StrategyContext) -> None:
        return None

    def on_market(self, event: Bar, ctx: StrategyContext) -> None:
        if event.timeframe != self.timeframe or ctx.positions(event.instrument_id):
            return

        required = self.candle_count + self.average_periods + self.atr_period
        bars = ctx.bars(event.instrument_id, self.timeframe, required + 1)
        if len(bars) < required + 1:
            return

        current_total = self._score_total(bars[-self.candle_count :])
        previous_totals = [
            self._score_total(bars[-self.candle_count - offset : -offset])
            for offset in range(1, self.average_periods + 1)
        ]
        average_total = sum(previous_totals, Decimal("0")) / self.average_periods

        if current_total == average_total:
            return

        atr = self._atr(bars[-self.atr_period - 1 :])
        stop_distance = atr * self.atr_multiplier
        side = Side.BUY if current_total > average_total else Side.SELL
        entry_reference = event.close
        if side is Side.BUY:
            stop_loss = entry_reference - stop_distance
            take_profit = entry_reference + stop_distance * self.risk_reward
        else:
            stop_loss = entry_reference + stop_distance
            take_profit = entry_reference - stop_distance * self.risk_reward

        instrument = ctx.instrument
        stop_loss = self._round_price(stop_loss, instrument.price_tick)
        take_profit = self._round_price(take_profit, instrument.price_tick)
        self.sequence += 1
        ctx.submit_order(
            OrderRequest(
                request_id=f"candle-score-{self.sequence}",
                instrument_id=event.instrument_id,
                side=side,
                type=OrderType.MARKET,
                quantity=self.quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_tag="candle-score",
            )
        )

    def on_order_update(self, event: Order, ctx: StrategyContext) -> None:
        return None

    def on_fill(self, event: Fill, ctx: StrategyContext) -> None:
        return None

    def on_timer(self, event: TimerEvent, ctx: StrategyContext) -> None:
        return None

    def on_finish(self, ctx: StrategyContext) -> None:
        ctx.log("info", "candle-score strategy finished")

    def _score_total(self, bars: tuple[Bar, ...]) -> Decimal:
        total = Decimal("0")
        for bar in bars:
            if bar.close == bar.open:
                continue
            score = Decimal("1") if bar.close > bar.open else Decimal("-1")
            move_percent = abs(bar.close - bar.open) / bar.open * Decimal("100")
            multiplier = Decimal("2") if move_percent >= self.big_move_percent else Decimal("1")
            total += score * multiplier
        return total

    @staticmethod
    def _atr(bars: tuple[Bar, ...]) -> Decimal:
        true_ranges = []
        for index, bar in enumerate(bars):
            previous_close = bars[index - 1].close if index else bar.open
            true_ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        return sum(true_ranges, Decimal("0")) / len(true_ranges)

    @staticmethod
    def _round_price(value: Decimal, tick: Decimal) -> Decimal:
        return (value / tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick


def build_strategy(parameters: dict[str, object]) -> CandleScoreStrategy:
    return CandleScoreStrategy(parameters)
