# Architecture Decisions

## Accepted for v1

- Python 3.14, an in-process Python SDK, and the `backtest` CLI.
- XAUUSD first, with all instrument economics and trading sessions required in YAML.
- The supplied one-minute CSV is authoritative. Higher timeframes are causally aggregated
  from it; supplied higher-timeframe files may be used for validation only.
- Both hedging and netting account modes are supported.
- UTC internal time with configurable source timezone and session calendar.
- Orders submitted by callbacks become eligible on the next source observation.
- Bar ambiguity uses conservative stop-first handling and is reported.
- Open positions are marked to market at termination by default.
- Full market event traces are opt-in.

## Deferred

Trailing stops, IOC/FOK, capacity-based partial order fills, multi-currency conversion,
network adapters, a GUI, and ML training are post-v1 extensions.

