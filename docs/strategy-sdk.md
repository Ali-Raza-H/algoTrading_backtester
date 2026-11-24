# Strategy SDK

A strategy module exports a factory such as `build_strategy(parameters)`. The factory
must return a fresh object for every run. Implement these callbacks:

- `on_start(ctx)`
- `on_warmup(event, ctx)`
- `on_market(event, ctx)`
- `on_order_update(event, ctx)`
- `on_fill(event, ctx)`
- `on_timer(event, ctx)` when virtual timers are used
- `on_finish(ctx)`

The context exposes only causal state: `ctx.now`, completed `ctx.bars(...)`, account,
positions, orders, a seeded `ctx.rng`, logging, order commands, position closing, and
virtual timers. Warmup orders are rejected. Timer timestamps must be in the future.

Submit typed `OrderRequest` values. Each request ID is idempotent: an identical replay
returns the original order; reuse with a different payload fails. Market, limit, stop,
and stop-limit orders are supported. Quantity and prices must align with the configured
instrument steps.

Use virtual time and `ctx.rng`; do not use wall-clock time, `sleep`, unseeded randomness,
network calls, or direct dataset access. Strategies are trusted local Python code, not
sandboxed plugins. See `strategies/moving_average.py` and `strategies/random_policy.py`.

