"""Read-only Deribit live contract and Parquet-size probe.

This command makes public REST requests only. It never creates Git commits,
writes the market-data branch, or uses a GitHub token. A failed contract check
must block production activation rather than silently dropping an underlying.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any

import pyarrow.parquet as pq

from .deribit_archive import ArchiveConfig, Dataset, load_config
from .deribit_client import DeribitClient, DeribitError
from .deribit_pipeline import prepare_live_batch

LIVE_NAMES = frozenset({"option_chain", "index_price", "futures_curve", "volatility_index"})


def normalize_supported_indices(data: Any) -> set[str]:
    """Parse source index identifiers, accepting documented basic/extended forms."""
    if not isinstance(data, list) or not data:
        raise DeribitError("get_index_price_names: expected nonempty list")
    indices = set()
    for entry in data:
        name = entry if isinstance(entry, str) else (
            entry.get("index_name", entry.get("name")) if isinstance(entry, dict) else None
        )
        if not isinstance(name, str) or not name:
            raise DeribitError("get_index_price_names: invalid identifier record")
        if name in indices:
            raise DeribitError(f"get_index_price_names: duplicate {name}")
        indices.add(name)
    return indices


def probe(config: ArchiveConfig, client: DeribitClient,
          now: datetime) -> dict[str, Any]:
    """Validate current source coverage and compute actual in-memory ZSTD bytes.

    Fixture tests can inject client.transport; on a live run, all observations
    come from public REST calls. No synthetic PAXG product/price is generated.
    """
    if now.tzinfo is None:
        raise ValueError("probe time must be timezone-aware")
    observed = now.astimezone(timezone.utc)
    live = ArchiveConfig(config.branch, config.compression_level,
                         tuple(ds for ds in config.datasets if ds.name in LIVE_NAMES))
    if not live.datasets:
        raise ValueError("no enabled live datasets to probe")
    scope = sorted({asset for ds in live.datasets for asset in ds.underlyings})
    catalog = client.instruments()
    coverage: dict[str, Any] = {}
    price_indexes: set[str] = set()
    for asset in scope:
        selected = [i for i in catalog if i.get("base_currency") == asset]
        option_names = [i for i in selected if i.get("kind") == "option"]
        future_names = [i for i in selected if i.get("kind") == "future"]
        index_names = {i.get("price_index") for i in selected if i.get("price_index")}
        price_indexes.update(index_names)
        coverage[asset] = {
            "options_listed": len(option_names),
            "futures_listed": len(future_names),
            "option_active": sum(i.get("is_active") is True for i in option_names),
            "future_active": sum(i.get("is_active") is True for i in future_names),
            "settlement_currencies": sorted({i["settlement_currency"] for i in selected
                                             if i.get("settlement_currency")}),
            "referenced_indexes": sorted(index_names),
        }
    supported = normalize_supported_indices(client.call("get_index_price_names"))
    unsupported_indexes = price_indexes - supported
    if unsupported_indexes:
        raise DeribitError(f"instrument references unsupported index names: {sorted(unsupported_indexes)}")

    capability_gaps: list[dict[str, str]] = []
    sample_datasets = []
    for ds in live.datasets:
        eligible = []
        for asset in ds.underlyings:
            reason = None
            if ds.name == "option_chain" and coverage[asset]["options_listed"] == 0:
                reason = "no option instruments listed"
            elif ds.name == "futures_curve" and coverage[asset]["futures_listed"] == 0:
                reason = "no futures listed"
            elif ds.name == "index_price" and not coverage[asset]["referenced_indexes"]:
                reason = "no instrument-referenced index"
            if reason:
                capability_gaps.append({
                    "underlying": asset, "dataset": ds.name, "reason": reason,
                    "severity": ("informational"
                                 if asset in ds.options.get("allow_unlisted", [])
                                 else "blocking"),
                })
            else:
                eligible.append(asset)
        if eligible:
            sample_datasets.append(Dataset(
                ds.name, ds.interval_hours, tuple(eligible), ds.options,
            ))

    # Cache source catalog responses during the second snapshot read to avoid
    # unnecessarily repeating rate-limited get_instruments in the same probe.
    original_call = client.call
    cache = {(kind,): [dict(i) for i in catalog if i.get("kind") == kind]
             for kind in ("option", "future")}
    def cached_transport(method: str, params: dict) -> Any:
        if method == "get_instruments" and (params.get("kind"),) in cache:
            return cache[(params["kind"],)]
        return original_call(method, **params)

    snapshot_client = DeribitClient(transport=cached_transport)
    sizes: dict[str, dict[str, int]] = defaultdict(
        lambda: {"parquet_parts": 0, "rows": 0, "zstd_bytes": 0}
    )
    source_failures: dict[str, str] = {}
    for ds in sample_datasets:
        # Isolate source-contract failures by dataset, without pretending
        # that a partial source sample is a publishable unified batch.
        try:
            batch = prepare_live_batch(
                ArchiveConfig(config.branch, config.compression_level, (ds,)),
                snapshot_client, observed,
            )
        except (DeribitError, ValueError) as exc:
            source_failures[ds.name] = str(exc)
            continue
        for path, payload in sorted(batch.parts.items()):
            if not path.endswith(".parquet"):
                raise DeribitError("live probe unexpectedly generated non-Parquet data")
            file_meta = pq.read_metadata(BytesIO(payload))
            dataset = file_meta.metadata[b"dataset"].decode()
            sizes[dataset]["parquet_parts"] += 1
            sizes[dataset]["rows"] += file_meta.num_rows
            sizes[dataset]["zstd_bytes"] += len(payload)

    # Check representative native history/delivery contracts without full
    # multi-year backfill; daily ingestion is tested separately offline.
    history = {}
    for asset in ("BTC", "ETH"):
        if asset not in scope:
            continue
        try:
            series = client.historical_volatility(asset)
        except DeribitError as exc:
            source_failures[f"historical_volatility/{asset}"] = str(exc)
            continue
        history[asset] = {"observations": len(series)}
    # Validate complete pagination, not just the first two rows. A first-page
    # sample cannot reveal repeated offsets, changed totals or late bad pages.
    # This remains read-only and never publishes historical events.
    settlement_samples = {}
    for index in sorted(price_indexes):
        try:
            rows = client.delivery_prices(index)
        except DeribitError as exc:
            source_failures[f"delivery_price/{index}"] = str(exc)
            continue
        for row in rows:
            if date.fromisoformat(row["event_date"]) > observed.date():
                source_failures[f"delivery_price/{index}"] = "future native delivery date"
                break
        if f"delivery_price/{index}" not in source_failures:
            settlement_samples[index] = {
                "validated_rows": len(rows),
                "pagination_complete": True,
            }

    blocked = any(g["severity"] == "blocking" for g in capability_gaps) or bool(source_failures)
    return {
        "status": "blocked" if blocked else "ok",
        "capability_gaps": capability_gaps,
        "source_failures": source_failures,
        "measured_scope": {ds.name: sorted(ds.underlyings) for ds in sample_datasets},
        "checked_at": observed.isoformat(),
        "utc_slot": observed.replace(minute=0, second=0, microsecond=0).isoformat(),
        "underlyings": coverage,
        "supported_index_names_count": len(supported),
        "referenced_index_names": sorted(price_indexes),
        "compressed_parquet": dict(sorted(sizes.items())),
        "total_zstd_bytes": sum(v["zstd_bytes"] for v in sizes.values()),
        "historical_volatility": history,
        "delivery_price_samples": settlement_samples,
        "publisher_invoked": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Deribit live API contract probe")
    parser.add_argument("--config", default="config/market-data.yml")
    args = parser.parse_args()
    config = load_config(args.config)
    result = probe(config, DeribitClient(), datetime.now(timezone.utc))
    print(json.dumps(result, sort_keys=True), flush=True)
    if result["status"] != "ok":
        parser.exit(1, "Deribit live source contract incomplete; production remains disabled\n")


if __name__ == "__main__":
    main()
