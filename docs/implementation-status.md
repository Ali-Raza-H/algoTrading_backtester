# Implementation Status

The current release implements the accepted XAUUSD candle-data workflow:

- streaming configurable CSV import to Parquet with a hashed manifest;
- causal one-minute replay and aligned intraday aggregation;
- strategy lifecycle, warmup, seeded randomness, commands, and virtual timers;
- market, limit, stop, stop-limit, SL/TP, cancellation, modification, and expiry;
- hedging/netting, partial position closes, margin, stop-out, fees, and financing;
- continuous and random-window runs, cash/random benchmarks, and stable seeds;
- JSON, Parquet, HTML, SQLite indexing, comparison, and event replay;
- strict YAML validation, Ruff, mypy, pytest, and synthetic acceptance fixtures.

The post-v1 items recorded in `docs/decisions.md` remain deferred. Quote ticks and
provider-specific CSV presets will be added when the real dataset schema is available.

