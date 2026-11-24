from pathlib import Path

import pytest

from backtest.config import ImportConfig
from backtest.data import import_csv, iter_bars, load_manifest
from backtest.domain.errors import DataError


def import_yaml(source: Path, output: Path, *, strict: bool = False) -> str:
    return f"""schema_version: 1
source: {source}
output: {output}
instrument: XAUUSD
timeframe: 1m
strict_gaps: {str(strict).lower()}
csv:
  timestamp: time
  open: open
  high: high
  low: low
  close: close
  volume: null
  timestamp_semantics: open
  timestamp_format: null
  timezone: UTC
  delimiter: ','
  price_basis: mid
"""


def test_csv_import_writes_manifest_and_deduplicates_exact_rows(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "time,open,high,low,close\n"
        "2024-01-01T00:00:00Z,100,101,99,100\n"
        "2024-01-01T00:00:00Z,100,101,99,100\n"
        "2024-01-01T00:01:00Z,100,102,100,101\n"
    )
    config_path = tmp_path / "import.yaml"
    config_path.write_text(import_yaml(source, tmp_path / "dataset"))
    manifest = import_csv(ImportConfig.from_file(config_path))
    assert manifest.row_count == 2
    assert manifest.exact_duplicate_count == 1
    assert len(list(iter_bars(tmp_path / "dataset"))) == 2
    assert load_manifest(tmp_path / "dataset").source_sha256 == manifest.source_sha256


def test_conflicting_duplicate_fails(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "time,open,high,low,close\n"
        "2024-01-01T00:00:00Z,100,101,99,100\n"
        "2024-01-01T00:00:00Z,100,102,99,101\n"
    )
    config_path = tmp_path / "import.yaml"
    config_path.write_text(import_yaml(source, tmp_path / "dataset"))
    with pytest.raises(DataError, match="conflicting duplicate"):
        import_csv(ImportConfig.from_file(config_path))


def test_strict_gap_policy_fails(tmp_path: Path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "time,open,high,low,close\n"
        "2024-01-01T00:00:00Z,100,101,99,100\n"
        "2024-01-01T00:02:00Z,100,101,99,100\n"
    )
    config_path = tmp_path / "import.yaml"
    config_path.write_text(import_yaml(source, tmp_path / "dataset", strict=True))
    with pytest.raises(DataError, match="material data gap"):
        import_csv(ImportConfig.from_file(config_path))
