from pathlib import Path

import pytest

from backtest.config import ExperimentConfig
from backtest.domain.errors import ConfigError


def valid_config(dataset: Path) -> str:
    return f"""schema_version: 1
market:
  dataset: {dataset}
instrument:
  id: XAUUSD
  currency: USD
  contract_size: '100'
  price_tick: '0.01'
  quantity_step: '0.01'
  min_quantity: '0.01'
  max_quantity: '100'
  leverage: '30'
  spread: '0.20'
  session_timezone: UTC
  sessions: ['Mon 00:00-23:59']
strategy:
  entrypoint: strategies.moving_average:build_strategy
  strategy_id: test
  version: '1'
  parameters: {{fast_period: 1, slow_period: 2, quantity: '0.01'}}
  timeframes: [1m, 1h]
  lookbacks: {{1m: 1, 1h: 2}}
execution:
  slippage_ticks: '0'
  commission_per_lot_per_side: '0'
  financing_long_per_lot_daily: '0'
  financing_short_per_lot_daily: '0'
  financing_rollover_time: '22:00'
  financing_timezone: UTC
  financing_triple_day: Wed
  intrabar_policy: stop_first
  stale_after_seconds: null
account:
  currency: USD
  starting_balance: '10000'
  position_mode: netting
  stop_out_percent: '50'
benchmark:
  enabled: false
  trials: 10
  policies: [random_policy, cash]
  entry_probability: 0.01
  holding_bars: 12
  quantity: '0.01'
  timeframe: 1h
experiment:
  start: '2024-01-01T03:00:00Z'
  end: '2024-01-01T04:00:00Z'
  seed: 42
  workers: 1
report:
  directory: runs
  full_market_trace: false
"""


def test_financial_values_must_be_quoted(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        valid_config(tmp_path / "manifest.json").replace(
            "contract_size: '100'", "contract_size: 100"
        )
    )
    with pytest.raises(ConfigError, match="quoted strings"):
        ExperimentConfig.from_file(path)


def test_instrument_fields_have_no_hidden_defaults(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(valid_config(tmp_path / "manifest.json").replace("  spread: '0.20'\n", ""))
    with pytest.raises(ConfigError, match="spread"):
        ExperimentConfig.from_file(path)


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(valid_config(tmp_path / "manifest.json") + "surprise: true\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        ExperimentConfig.from_file(path)
