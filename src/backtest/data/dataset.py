from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from backtest.config import ImportConfig
from backtest.domain.errors import DataError
from backtest.domain.models import Bar, PriceBasis
from backtest.market.timeframes import parse_timeframe

SCHEMA_VERSION = 1
BAR_SCHEMA = pa.schema(
    [
        ("instrument_id", pa.string()),
        ("timeframe", pa.string()),
        ("open_time_ns", pa.int64()),
        ("close_time_ns", pa.int64()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("price_basis", pa.string()),
        ("volume", pa.string()),
    ]
)


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    schema_version: int
    dataset_id: str
    instrument_id: str
    timeframe: str
    price_basis: str
    source: str
    source_sha256: str
    data_file: str
    row_count: int
    first_open_time: str
    last_close_time: str
    gap_count: int
    exact_duplicate_count: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DataError(f"cannot hash source {path}: {exc}") from exc
    return digest.hexdigest()


def _timestamp(raw: str, config: ImportConfig) -> datetime:
    try:
        if config.mapping.timestamp_format:
            parsed = datetime.strptime(raw, config.mapping.timestamp_format)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"invalid timestamp {raw!r}") from exc
    timezone = ZoneInfo(config.mapping.timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _price(row: dict[str, str], column: str, line: int) -> Decimal:
    try:
        value = Decimal(row[column])
    except KeyError as exc:
        raise DataError(f"CSV line {line}: missing column {column!r}") from exc
    except InvalidOperation as exc:
        raise DataError(f"CSV line {line}: invalid decimal in {column!r}") from exc
    if not value.is_finite():
        raise DataError(f"CSV line {line}: non-finite value in {column!r}")
    return value


def _bar(row: dict[str, str], line: int, config: ImportConfig) -> Bar:
    mapping = config.mapping
    try:
        observed = _timestamp(row[mapping.timestamp], config)
    except KeyError as exc:
        raise DataError(f"CSV line {line}: missing column {mapping.timestamp!r}") from exc
    duration = parse_timeframe(config.timeframe).duration
    if mapping.timestamp_semantics == "open":
        open_time, close_time = observed, observed + duration
    else:
        open_time, close_time = observed - duration, observed
    volume = _price(row, mapping.volume, line) if mapping.volume else None
    try:
        return Bar(
            instrument_id=config.instrument_id,
            timeframe=parse_timeframe(config.timeframe).label,
            open_time=open_time,
            close_time=close_time,
            open=_price(row, mapping.open, line),
            high=_price(row, mapping.high, line),
            low=_price(row, mapping.low, line),
            close=_price(row, mapping.close, line),
            price_basis=mapping.price_basis,
            volume=volume,
        )
    except ValueError as exc:
        raise DataError(f"CSV line {line}: {exc}") from exc


def _record(bar: Bar) -> dict[str, Any]:
    return {
        "instrument_id": bar.instrument_id,
        "timeframe": bar.timeframe,
        "open_time_ns": int(bar.open_time.timestamp() * 1_000_000_000),
        "close_time_ns": int(bar.close_time.timestamp() * 1_000_000_000),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "price_basis": bar.price_basis.value,
        "volume": str(bar.volume) if bar.volume is not None else "",
    }


def import_csv(config: ImportConfig) -> DatasetManifest:
    if not config.source.is_file():
        raise DataError(f"source CSV does not exist: {config.source}")
    config.output.mkdir(parents=True, exist_ok=True)
    data_name = "bars.parquet"
    data_path = config.output / data_name
    temp_path = config.output / f".{data_name}.tmp"
    writer: pq.ParquetWriter | None = None
    batch: list[dict[str, Any]] = []
    previous: Bar | None = None
    first: Bar | None = None
    last: Bar | None = None
    count = gaps = duplicates = 0
    try:
        with config.source.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter=config.mapping.delimiter)
            if reader.fieldnames is None:
                raise DataError("CSV has no header")
            required = {
                config.mapping.timestamp,
                config.mapping.open,
                config.mapping.high,
                config.mapping.low,
                config.mapping.close,
            }
            if config.mapping.volume:
                required.add(config.mapping.volume)
            missing = sorted(required - set(reader.fieldnames))
            if missing:
                raise DataError(f"CSV missing columns: {', '.join(missing)}")
            writer = pq.ParquetWriter(temp_path, BAR_SCHEMA, compression="zstd")
            expected = parse_timeframe(config.timeframe).duration
            for line, row in enumerate(reader, start=2):
                bar = _bar(row, line, config)
                if previous is not None:
                    if bar.open_time < previous.open_time:
                        raise DataError(f"CSV line {line}: timestamps are not monotonic")
                    if bar.open_time == previous.open_time:
                        if bar == previous:
                            duplicates += 1
                            continue
                        raise DataError(f"CSV line {line}: conflicting duplicate timestamp")
                    if bar.open_time > previous.open_time + expected:
                        gaps += 1
                        if config.strict_gaps:
                            raise DataError(f"CSV line {line}: material data gap")
                first = first or bar
                last = previous = bar
                batch.append(_record(bar))
                count += 1
                if len(batch) >= config.batch_size:
                    writer.write_table(pa.Table.from_pylist(batch, schema=BAR_SCHEMA))
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=BAR_SCHEMA))
    except OSError as exc:
        raise DataError(f"cannot import CSV: {exc}") from exc
    finally:
        if writer is not None:
            writer.close()
    if first is None or last is None:
        temp_path.unlink(missing_ok=True)
        raise DataError("CSV contains no data rows")
    os.replace(temp_path, data_path)
    source_hash = _sha256(config.source)
    manifest = DatasetManifest(
        schema_version=SCHEMA_VERSION,
        dataset_id=f"{config.instrument_id.lower()}-{config.timeframe.lower()}-{source_hash[:12]}",
        instrument_id=config.instrument_id,
        timeframe=parse_timeframe(config.timeframe).label,
        price_basis=config.mapping.price_basis.value,
        source=str(config.source),
        source_sha256=source_hash,
        data_file=data_name,
        row_count=count,
        first_open_time=first.open_time.isoformat(),
        last_close_time=last.close_time.isoformat(),
        gap_count=gaps,
        exact_duplicate_count=duplicates,
        created_at=datetime.now(UTC).isoformat(),
    )
    manifest_path = config.output / "manifest.json"
    temporary_manifest = config.output / ".manifest.json.tmp"
    temporary_manifest.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary_manifest, manifest_path)
    return manifest


def load_manifest(path: Path) -> DatasetManifest:
    manifest_path = path / "manifest.json" if path.is_dir() else path
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = DatasetManifest(**raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise DataError(f"cannot load dataset manifest {manifest_path}: {exc}") from exc
    if manifest.schema_version != SCHEMA_VERSION:
        raise DataError(f"unsupported dataset schema {manifest.schema_version}")
    return manifest


def iter_bars(
    manifest_path: Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[Bar]:
    manifest = load_manifest(manifest_path)
    root = manifest_path if manifest_path.is_dir() else manifest_path.parent
    data_path = root / manifest.data_file
    try:
        parquet = pq.ParquetFile(data_path)
    except (OSError, pa.ArrowException) as exc:
        raise DataError(f"cannot read dataset {data_path}: {exc}") from exc
    for batch in parquet.iter_batches(batch_size=65_536):
        for row in batch.to_pylist():
            open_time = datetime.fromtimestamp(row["open_time_ns"] / 1_000_000_000, UTC)
            close_time = datetime.fromtimestamp(row["close_time_ns"] / 1_000_000_000, UTC)
            if start is not None and close_time <= start:
                continue
            if end is not None and open_time >= end:
                return
            yield Bar(
                instrument_id=row["instrument_id"],
                timeframe=row["timeframe"],
                open_time=open_time,
                close_time=close_time,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                price_basis=PriceBasis(row["price_basis"]),
                volume=Decimal(row["volume"]) if row["volume"] else None,
            )


def validate_dataset(path: Path) -> dict[str, Any]:
    manifest = load_manifest(path)
    count = 0
    previous: Bar | None = None
    for bar in iter_bars(path):
        if previous is not None and bar.open_time <= previous.open_time:
            raise DataError("dataset order or uniqueness validation failed")
        previous = bar
        count += 1
    if count != manifest.row_count:
        raise DataError(f"manifest row_count={manifest.row_count}, observed={count}")
    return {"valid": True, "rows": count, "dataset_id": manifest.dataset_id}
