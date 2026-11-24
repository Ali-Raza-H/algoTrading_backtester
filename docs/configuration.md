# Configuration

The engine separates dataset import from experiments. Copy the files in `config/`
and replace every illustrative value before using broker data.

## CSV Import

`config/import.example.yaml` maps provider column names into the normalized one-minute
schema. Set `timestamp_semantics` to `open` or `close`, supply the source timezone,
and identify the candle `price_basis` as `bid`, `ask`, or `mid`. Date formats are never
guessed when `timestamp_format` is supplied.

```bash
uv run backtest data import --config config/import.yaml
uv run backtest data validate --dataset data/xauusd-1m/manifest.json
```

The importer rejects invalid OHLC values, non-monotonic rows, and conflicting
duplicates. It records exact duplicates and gaps. Enable `strict_gaps` to fail on gaps.

## Experiment YAML

Every instrument field is required. There are no broker defaults for contract size,
tick size, lot constraints, leverage, spread, timezone, or sessions. Monetary values
must be quoted decimal strings.

Costs are also explicit: commission, slippage, long/short daily financing, rollover
time, timezone, and triple day. A value of `'0'` is an intentional model assumption.

Use `benchmark.enabled` to run seeded random trials and a cash baseline. Use
`config/windows.example.yaml` with `backtest batch` for reproducible random windows.

```bash
uv run backtest strategy validate --config config/experiment.yaml
uv run backtest run --config config/experiment.yaml --dry-run
uv run backtest run --config config/experiment.yaml
uv run backtest batch --config config/windows.yaml
```

Dry-run checks the strategy, dataset coverage, source resolution, and warmup without
executing decisions.

