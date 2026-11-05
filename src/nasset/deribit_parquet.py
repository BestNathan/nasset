"""Immutable, typed Parquet/ZSTD parts for Deribit market-data snapshots.

No Git mutations occur here. Each part is prepared in memory and handed to a
separate publisher; source timestamps and native units must never be inferred.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA_VERSION = 1
IDENTITY = [
    ("provider", pa.string()), ("dataset", pa.string()),
    ("snapshot_slot_at", pa.timestamp("us", tz="UTC")),
    ("captured_at", pa.timestamp("us", tz="UTC")),
]
SCHEMAS = {
    "option_chain": [
        ("instrument_name", pa.string()), ("base_currency", pa.string()),
        ("quote_currency", pa.string()), ("settlement_currency", pa.string()),
        ("kind", pa.string()), ("option_type", pa.string()),
        ("expiration_timestamp", pa.int64()), ("strike", pa.float64()),
        ("contract_size", pa.float64()), ("tick_size", pa.float64()),
        ("bid_price", pa.float64()), ("ask_price", pa.float64()),
        ("mark_price", pa.float64()), ("last", pa.float64()),
        ("mark_iv", pa.float64()), ("underlying_price", pa.float64()),
        ("underlying_index", pa.string()), ("open_interest", pa.float64()),
        ("volume", pa.float64()), ("volume_usd", pa.float64()),
    ],
    "futures_curve": [
        ("instrument_name", pa.string()), ("base_currency", pa.string()),
        ("quote_currency", pa.string()), ("settlement_currency", pa.string()),
        ("kind", pa.string()), ("expiration_timestamp", pa.int64()),
        ("contract_size", pa.float64()), ("tick_size", pa.float64()),
        ("bid_price", pa.float64()), ("ask_price", pa.float64()),
        ("mark_price", pa.float64()), ("last", pa.float64()),
        ("open_interest", pa.float64()), ("volume", pa.float64()),
        ("volume_usd", pa.float64()),
    ],
    "index_price": [
        ("index_name", pa.string()), ("base_currency", pa.string()),
        ("quote_currency", pa.string()), ("index_price", pa.float64()),
        ("estimated_delivery_price", pa.float64()),
    ],
    "delivery_price": [
        ("index_name", pa.string()), ("base_currency", pa.string()),
        ("quote_currency", pa.string()), ("event_timestamp", pa.int64()),
        ("delivery_price", pa.float64()),
    ],
    "volatility_index": [
        ("base_currency", pa.string()), ("event_timestamp", pa.int64()),
        ("open", pa.float64()), ("high", pa.float64()),
        ("low", pa.float64()), ("close", pa.float64()),
    ],
    "historical_volatility": [
        ("base_currency", pa.string()), ("event_timestamp", pa.int64()),
        ("volatility", pa.float64()),
    ],
}


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def part_path(dataset: str, slot: datetime, *, underlying: str | None = None) -> str:
    if dataset not in SCHEMAS:
        raise ValueError(f"unsupported dataset: {dataset}")
    stamp = _utc(slot, "slot")
    if stamp.minute or stamp.second or stamp.microsecond:
        raise ValueError("slot must be aligned to an exact UTC hour")
    if dataset in {"option_chain", "futures_curve", "volatility_index", "historical_volatility"}:
        if not underlying or not underlying.isascii() or not underlying.isalnum() or not underlying.isupper():
            raise ValueError(f"{dataset} requires uppercase underlying")
        prefix = f"deribit/{dataset}/{underlying}"
    else:
        if underlying is not None:
            raise ValueError(f"{dataset} is a shared per-slot part")
        prefix = f"deribit/{dataset}"
    return f"{prefix}/{stamp:%Y/%m/%d/%H00}.parquet"


def encode_part(
    dataset: str, rows: Sequence[Mapping[str, Any]], *,
    slot: datetime, captured_at: datetime, compression_level: int = 6,
) -> bytes:
    """Produce a standalone Parquet file; reject empty/ambiguous snapshots.

    Event-only datasets should not call this on an empty incremental poll.
    Unknown fields are deliberately rejected rather than silently discarded.
    """
    if dataset not in SCHEMAS:
        raise ValueError(f"unsupported dataset: {dataset}")
    stamp = _utc(slot, "slot")
    captured = _utc(captured_at, "captured_at")
    if stamp.minute or stamp.second or stamp.microsecond:
        raise ValueError("slot must be aligned to an exact UTC hour")
    if not 1 <= compression_level <= 22:
        raise ValueError("invalid ZSTD compression level")
    if not rows:
        raise ValueError("refusing to publish empty Parquet snapshot")
    fields = dict(IDENTITY + SCHEMAS[dataset])
    allowed = set(fields)
    normalized = []
    for row in rows:
        unknown = set(row) - allowed
        if unknown:
            raise ValueError(f"unknown {dataset} fields: {sorted(unknown)}")
        if dataset in {"option_chain", "futures_curve"} and not row.get("instrument_name"):
            raise ValueError("instrument_name required")
        if dataset in {"index_price", "delivery_price"} and not row.get("index_name"):
            raise ValueError("index_name required")
        if dataset in {"delivery_price", "volatility_index", "historical_volatility"} and row.get("event_timestamp") is None:
            raise ValueError("native event_timestamp required")
        normalized.append({
            **row, "provider": "deribit", "dataset": dataset,
            "snapshot_slot_at": stamp, "captured_at": captured,
        })
    schema = pa.schema(
        [pa.field(name, typ, nullable=(name not in {
            "provider", "dataset", "snapshot_slot_at", "captured_at",
        })) for name, typ in IDENTITY + SCHEMAS[dataset]],
        metadata={
            b"schema_version": str(SCHEMA_VERSION).encode(),
            b"provider": b"deribit", b"dataset": dataset.encode(),
            b"record_count": str(len(rows)).encode(),
            b"captured_at": captured.isoformat().encode(),
        },
    )
    table = pa.Table.from_pylist(normalized, schema=schema)
    output = BytesIO()
    pq.write_table(table, output, compression="zstd",
                   compression_level=compression_level, use_dictionary=True)
    return output.getvalue()
