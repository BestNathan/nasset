"""Fail-closed orchestration of the implemented Deribit live snapshot datasets.

This layer deliberately does not publish a subset of a due batch: DVOL,
historical volatility and delivery event adapters must be implemented before
a production run with the full configured scope can be enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Mapping

from .deribit_archive import ArchiveConfig
from .deribit_client import DeribitClient, DeribitError
from .deribit_parquet import SCHEMAS, encode_part, part_path

LIVE_DATASETS = frozenset({"option_chain", "index_price", "futures_curve", "volatility_index"})


@dataclass(frozen=True)
class PreparedBatch:
    slot: datetime
    parts: Mapping[str, bytes]
    datasets: tuple[str, ...]


def prepare_live_batch(config: ArchiveConfig, client: DeribitClient, now: datetime) -> PreparedBatch:
    """Prepare all due datasets or fail without publishing any file.

    The caller may publish the returned mapping once using
    GitHubArchivePublisher.publish(). No publication happens in this function.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    instant = now.astimezone(timezone.utc)
    slot = instant.replace(minute=0, second=0, microsecond=0)
    due = config.due(instant)
    if not due:
        raise ValueError("no due datasets")
    unsupported = sorted(ds.name for ds in due if ds.name not in LIVE_DATASETS)
    if unsupported:
        raise NotImplementedError(
            "refusing incomplete due batch; adapters missing: " + ", ".join(unsupported)
        )

    by_name = {ds.name: ds for ds in due}
    asset_sets = [set(ds.underlyings) for ds in due if ds.name != "volatility_index"]
    # DeribitClient.snapshot() requires a complete quote set for each selected
    # instrument kind, so gather the union and filter rows by dataset scope.
    assets = tuple(sorted(set().union(*asset_sets)))
    snapshot = None
    captured = instant
    if assets:
        snapshot = client.snapshot(
            assets,
            include_options="option_chain" in by_name,
            include_futures="futures_curve" in by_name,
            include_indices="index_price" in by_name,
        )
        captured = snapshot.captured_at
        if captured.tzinfo is None:
            raise DeribitError("client returned naive captured_at")
        if captured.astimezone(timezone.utc) > instant:
            raise DeribitError("snapshot capture time is later than requested time")
        if captured.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) != slot:
            raise DeribitError("snapshot capture hour differs from requested UTC slot")

    parts: dict[str, bytes] = {}
    for name, rows in (("option_chain", snapshot.options if snapshot else ()),
                       ("futures_curve", snapshot.futures if snapshot else ())):
        if name not in by_name:
            continue
        ds = by_name[name]
        for asset in ds.underlyings:
            selected = [row for row in rows if row.get("base_currency") == asset]
            if not selected:
                # An asset may have listed options but no listed futures.
                # Record capability absence rather than inventing a quote.
                if name == "futures_curve" and not any(
                    i.get("base_currency") == asset and i.get("kind") == "future"
                    for i in snapshot.instruments
                ):
                    continue
                raise DeribitError(f"{name}: no rows for configured {asset}")
            parts[part_path(name, slot, underlying=asset)] = encode_part(
                name, _project(name, selected), slot=slot, captured_at=captured,
                compression_level=config.compression_level,
            )

    if "index_price" in by_name:
        ds = by_name["index_price"]
        names = {row.get("price_index"): row.get("base_currency")
                 for row in snapshot.instruments if row.get("price_index")}
        indices = []
        for row in snapshot.indices:
            index_name = row["index_name"]
            asset = names.get(index_name)
            if asset in ds.underlyings:
                indices.append({**row, "base_currency": asset,
                                "quote_currency": index_name.rsplit("_", 1)[-1].upper()})
        if not indices:
            raise DeribitError("index_price: no relevant indices")
        covered = {row["base_currency"] for row in indices}
        if not set(ds.underlyings).issubset(covered):
            raise DeribitError("index_price: missing indices for configured assets")
        parts[part_path("index_price", slot)] = encode_part(
            "index_price", _project("index_price", indices), slot=slot,
            captured_at=captured, compression_level=config.compression_level,
        )
    if "volatility_index" in by_name:
        ds = by_name["volatility_index"]
        # Last fully closed hourly candle, not an incomplete current hour.
        end_ms = int(slot.timestamp() * 1000)
        start_ms = int((slot - timedelta(hours=1)).timestamp() * 1000)
        for asset in ds.underlyings:
            if asset not in {"BTC", "ETH"}:
                raise DeribitError(f"DVOL unsupported asset: {asset}")
            candles = client.volatility_candles(
                asset, start_ms=start_ms, end_ms=end_ms,
                resolution=str(ds.options["resolution"]),
            )
            parts[part_path("volatility_index", slot, underlying=asset)] = encode_part(
                "volatility_index", list(candles), slot=slot, captured_at=instant,
                compression_level=config.compression_level,
            )
    if not parts:
        raise DeribitError("no parts prepared for due datasets")
    return PreparedBatch(slot, parts, tuple(ds.name for ds in due))


def _project(dataset: str, rows: list[dict]) -> list[dict]:
    """Explicitly map exchange payloads to the versioned Parquet schema.

    Extra Deribit response fields must not leak into the schema, and missing
    optional quote fields stay null instead of being fabricated as zeros.
    """
    allowed = {field for field, _ in SCHEMAS[dataset]}
    projected = []
    for row in rows:
        item = {key: value for key, value in row.items() if key in allowed}
        if "last" in allowed and "last" not in item:
            item["last"] = row.get("last_price")
        projected.append(item)
    return projected
