# Trading Backtester

A deterministic, strategy-independent historical market simulator for XAUUSD and
other configurable linear instruments. The implementation follows
[`backtesting-platform-spec.md`](backtesting-platform-spec.md).

## Development

```bash
uv sync --locked
uv run backtest --help
uv run pytest
uv run ruff check .
uv run mypy src
```

Start with synthetic data:

```bash
uv run backtest data import --config config/import.example.yaml
uv run backtest run --config config/experiment.example.yaml --dry-run
```

Run and batch commands show simulation and benchmark progress on stderr; their final JSON results remain on stdout.

Market data and run artifacts are ignored by Git. Instrument economics and CSV
semantics are always explicit in YAML; the engine has no hidden broker defaults.

See [Configuration](docs/configuration.md), [Strategy SDK](docs/strategy-sdk.md),
and [Execution Model](docs/execution-model.md) before running research data.

The project is also registered with the local project manager:

```bash
dev install Trading_Backtester
dev test Trading_Backtester
dev run Trading_Backtester
```

