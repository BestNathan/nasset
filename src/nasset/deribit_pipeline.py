"""Fail-closed orchestration of the implemented Deribit live snapshot datasets.

Snapshot and historical-event datasets are prepared together. Historical
data is filtered by immutable event identities already present in Git tree
metadata; no historical Parquet files need to be downloaded.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime, timezone, timedelta
from typing import Mapping

from .deribit_archive import ArchiveConfig
from .deribit_client import DeribitClient, DeribitError
from .deribit_parquet import SCHEMAS, encode_part, part_path
from .deribit_history import ExistingLookup, unseen_events

LIVE_DATASETS = frozenset({
    "option_chain", "index_price", "futures_curve", "volatility_index",
    "historical_volatility", "delivery_price",
})
HISTORY_DATASETS = frozenset({"historical_volatility", "delivery_price"})


@dataclass(frozen=True)
class PreparedBatch:
    slot: datetime
    parts: Mapping[str, bytes]
    datasets: tuple[str, ...]
    unlisted: tuple[tuple[str, str], ...] = ()


def prepare_live_batch(
    config: ArchiveConfig, client: DeribitClient, now: datetime,
    *, existing: ExistingLookup | None = None,
) -> PreparedBatch:
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
    if any(name in by_name for name in HISTORY_DATASETS) and existing is None:
        raise ValueError("daily history requires an event-key lookup; refusing undeduplicated publish")
    asset_sets = [set(ds.underlyings) for ds in due
                  if ds.name in {"option_chain", "futures_curve", "index_price"}]
    # DeribitClient.snapshot() requires a complete quote set for each selected
    # instrument kind, so gather the union and filter rows by dataset scope.
    assets = tuple(sorted(set().union(*asset_sets)))
    snapshot = None
    captured = instant
    if assets:
        allowed_unlisted = tuple(sorted({
            asset for ds in due if ds.name in {"option_chain", "futures_curve"}
            for asset in (
                ds.underlyings if ds.name == "futures_curve"
                else ds.options.get("allow_unlisted", [])
            )
            if asset in assets
        }))
        snapshot = client.snapshot(
            assets,
            include_options="option_chain" in by_name,
            include_futures="futures_curve" in by_name,
            include_indices="index_price" in by_name,
            allow_unlisted=allowed_unlisted,
            option_underlyings=(by_name["option_chain"].underlyings
                                if "option_chain" in by_name else ()),
            futures_underlyings=(by_name["futures_curve"].underlyings
                                 if "futures_curve" in by_name else ()),
        )
        captured = snapshot.captured_at
        if captured.tzinfo is None:
            raise DeribitError("client returned naive captured_at")
        # `now` is the scheduler invocation time, not an upper bound on API
        # completion. A successful request normally finishes after `now`.
        if captured.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) != slot:
            raise DeribitError("snapshot capture hour differs from requested UTC slot")

    parts: dict[str, bytes] = {}
    unlisted: list[tuple[str, str]] = []
    for name, rows in (("option_chain", snapshot.options if snapshot else ()),
                       ("futures_curve", snapshot.futures if snapshot else ())):
        if name not in by_name:
            continue
        ds = by_name[name]
        for asset in ds.underlyings:
            selected = [row for row in rows if row.get("base_currency") == asset]
            if not selected:
                # Only a *proven zero-listing* may be absent. A missing quote
                # for even one listed contract is never an absent capability.
                listed = [
                    i for i in snapshot.instruments
                    if i.get("base_currency") == asset
                    and i.get("kind") == ("option" if name == "option_chain" else "future")
                ]
                if not listed and (
                    name == "futures_curve"
                    or asset in ds.options.get("allow_unlisted", [])
                ):
                    # Futures are an optional listed product for each base,
                    # unlike option-chain coverage (strict unless allowlisted).
                    unlisted.append((name, asset))
                    continue
                if not listed:
                    raise DeribitError(f"{name}: no listed contracts for configured {asset}")
                raise DeribitError(f"{name}: listed contracts have no quote rows for {asset}")
            parts[part_path(name, slot, underlying=asset)] = encode_part(
                name, _project(name, selected), slot=slot, captured_at=captured,
                compression_level=config.compression_level,
            )

    if "index_price" in by_name:
        ds = by_name["index_price"]
        # A full index identifier must resolve to exactly one base asset.
        # Dictionary-comprehension overwrite would silently misattribute a
        # shared or malformed source index to whichever instrument came last.
        names: dict[str, str] = {}
        for instrument in snapshot.instruments:
            index_name = instrument.get("price_index")
            if not index_name:
                continue
            asset = instrument.get("base_currency")
            previous = names.setdefault(index_name, asset)
            if previous != asset:
                raise DeribitError(
                    f"index maps to multiple base assets: {index_name}"
                )
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
    if "historical_volatility" in by_name:
        ds = by_name["historical_volatility"]
        for asset in ds.underlyings:
            rows = client.historical_volatility(asset)
            fresh, markers = unseen_events(
                "historical_volatility", asset, rows,
                observed_at=instant, existing=existing,
            )
            if fresh:
                parts[part_path("historical_volatility", slot, underlying=asset)] = encode_part(
                    "historical_volatility", _project("historical_volatility", fresh),
                    slot=slot, captured_at=instant,
                    compression_level=config.compression_level,
                )
                parts.update(markers)

    if "delivery_price" in by_name:
        ds = by_name["delivery_price"]
        # Reuse metadata obtained for live snapshots, or discover independently
        # for a delivery-only configuration. No book summaries are necessary.
        instruments = snapshot.instruments if snapshot else client.instruments()
        index_assets: dict[str, str] = {}
        for instrument in instruments:
            asset, index_name = instrument.get("base_currency"), instrument.get("price_index")
            if asset not in ds.underlyings or not index_name:
                continue
            previous = index_assets.setdefault(index_name, asset)
            if previous != asset:
                raise DeribitError(f"index maps to multiple base assets: {index_name}")
        configured_names = ds.options.get("index_names", "auto")
        if configured_names != "auto":
            unknown = set(configured_names) - set(index_assets)
            if unknown:
                raise DeribitError(f"delivery: unverified index names: {sorted(unknown)}")
            index_assets = {k: v for k, v in index_assets.items() if k in configured_names}
        if set(index_assets.values()) != set(ds.underlyings):
            raise DeribitError("delivery: missing source index for configured underlying")
        fresh_events = []
        event_markers = {}
        for index_name, asset in sorted(index_assets.items()):
            original = client.delivery_prices(index_name)
            rows = [{**row, "base_currency": asset,
                     "quote_currency": index_name.rsplit("_", 1)[-1].upper()}
                    for row in original]
            fresh, markers = unseen_events(
                "delivery_price", index_name, rows,
                observed_at=instant, existing=existing,
            )
            fresh_events.extend(fresh)
            event_markers.update(markers)
        if fresh_events:
            parts[part_path("delivery_price", slot)] = encode_part(
                "delivery_price", _project("delivery_price", fresh_events),
                slot=slot, captured_at=instant,
                compression_level=config.compression_level,
            )
            parts.update(event_markers)

    # Persist explicit, verified absence beside the hourly data. Otherwise
    # a future reader could mistake "PAXG option contract not listed" for a
    # lost PAXG file or incomplete ingestion. This is not market-price data.
    if unlisted:
        coverage_path = f"deribit/coverage/{slot:%Y/%m/%d/%H00}.json"
        coverage = {
            "schema_version": 1,
            "provider": "deribit",
            "snapshot_slot_at": slot.isoformat(),
            "observed_at": captured.astimezone(timezone.utc).isoformat(),
            "unlisted": [
                {"dataset": dataset, "underlying": asset, "listed_count": 0}
                for dataset, asset in sorted(unlisted)
            ],
        }
        parts[coverage_path] = (json.dumps(
            coverage, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) + "\n").encode("utf-8")

    # Empty event-only increments are healthy. The caller must skip publish()
    # for an empty mapping; the publisher never accepts empty commits.
    if not parts and not set(by_name).issubset(HISTORY_DATASETS):
        raise DeribitError("no parts prepared for due snapshot datasets")
    return PreparedBatch(slot, parts, tuple(ds.name for ds in due),
                         tuple(sorted(unlisted)))


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
