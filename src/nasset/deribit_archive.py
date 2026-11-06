"""Validated Deribit archive configuration and deterministic hourly scheduling.

No network calls or writes happen at import time. This module is intentionally
independent of PyArrow/GitHub publishing so the due-gate can run cheaply.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

SUPPORTED_DATASETS = frozenset({
    "option_chain", "index_price", "delivery_price", "volatility_index",
    "historical_volatility", "futures_curve", "funding_rate",
})
INTERVALS = frozenset({1, 2, 3, 4, 6, 8, 12, 24})


@dataclass(frozen=True)
class Dataset:
    name: str
    interval_hours: int
    underlyings: tuple[str, ...]
    options: Mapping[str, Any]


@dataclass(frozen=True)
class ArchiveConfig:
    branch: str
    compression_level: int
    datasets: tuple[Dataset, ...]

    def due(self, now: datetime) -> tuple[Dataset, ...]:
        if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
            raise ValueError("due() requires a UTC-aware datetime")
        return tuple(ds for ds in self.datasets if now.hour % ds.interval_hours == 0)

    @staticmethod
    def slot(now: datetime) -> str:
        if now.tzinfo is None:
            raise ValueError("slot() requires timezone-aware datetime")
        return now.astimezone(timezone.utc).strftime("%Y/%m/%d/%H00")


def _symbols(raw: object, key: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{key} must be a non-empty list")
    if any(not isinstance(x, str) or not x.isascii() or not x.isupper()
           or not x.isalnum() for x in raw):
        raise ValueError(f"{key} must contain uppercase asset identifiers")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{key} contains duplicates")
    return tuple(raw)


def parse_config(doc: Mapping[str, Any]) -> ArchiveConfig:
    if not isinstance(doc, dict) or type(doc.get("version")) is not int or doc["version"] != 1:
        raise ValueError("unsupported config version")
    storage = doc.get("storage")
    if not isinstance(storage, dict) or storage.get("branch") != "market-data":
        raise ValueError("storage.branch must be market-data")
    if storage.get("format") != "parquet" or storage.get("compression") != "zstd":
        raise ValueError("storage requires parquet with zstd")
    level = storage.get("compression_level")
    if type(level) is not int or not 1 <= level <= 22:
        raise ValueError("compression_level must be an integer from 1 to 22")
    sources = doc.get("sources")
    deribit = sources.get("deribit") if isinstance(sources, dict) else None
    if not isinstance(deribit, dict) or deribit.get("enabled") is not True:
        raise ValueError("Deribit must be enabled")
    underlyings = _symbols(deribit.get("underlyings"), "deribit.underlyings")
    raw_datasets = deribit.get("datasets")
    if not isinstance(raw_datasets, dict) or not raw_datasets:
        raise ValueError("datasets must be a non-empty mapping")
    if set(raw_datasets) - SUPPORTED_DATASETS:
        raise ValueError("unsupported datasets: " + ", ".join(sorted(set(raw_datasets) - SUPPORTED_DATASETS)))
    datasets = []
    for name, spec in raw_datasets.items():
        if not isinstance(spec, dict) or type(spec.get("enabled")) is not bool:
            raise ValueError(f"{name}.enabled must be boolean")
        hours = spec.get("interval_hours")
        if type(hours) is not int or hours not in INTERVALS:
            raise ValueError(f"{name}.interval_hours must divide 24")
        selected = _symbols(spec.get("underlyings", list(underlyings)), f"{name}.underlyings")
        if not set(selected).issubset(underlyings):
            raise ValueError(f"{name}.underlyings must be within shared underlyings")
        if "allow_unlisted" in spec:
            if name not in {"option_chain", "futures_curve"}:
                raise ValueError(f"{name}.allow_unlisted is only for listed derivative datasets")
            raw_allow = spec["allow_unlisted"]
            # [] is meaningful: no asset may be silently unlisted.
            if not isinstance(raw_allow, list) or any(
                not isinstance(asset, str) or asset not in selected for asset in raw_allow
            ) or len(set(raw_allow)) != len(raw_allow):
                raise ValueError(f"{name}.allow_unlisted must be unique configured underlying assets")
        if name in {"index_price", "delivery_price"}:
            index_names = spec.get("index_names")
            if index_names != "auto" and (not isinstance(index_names, list) or
                                            not index_names or any(not isinstance(n, str) or not n for n in index_names)):
                raise ValueError(f"{name}.index_names must be auto or a list")
        if name in {"volatility_index", "historical_volatility"} and not set(selected).issubset({"BTC", "ETH"}):
            raise ValueError(f"{name}.underlyings supports BTC/ETH only")
        if name == "volatility_index" and spec.get("resolution") not in ("3600", 3600):
            raise ValueError("volatility_index.resolution must be hourly")
        if spec["enabled"]:
            datasets.append(Dataset(name, hours, selected, dict(spec)))
    if not datasets:
        raise ValueError("at least one dataset must be enabled")
    return ArchiveConfig(storage["branch"], level, tuple(datasets))


def load_config(path: str | Path) -> ArchiveConfig:
    with Path(path).open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return parse_config(raw)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Cheap UTC due-gate for Deribit datasets")
    parser.add_argument("--config", default="config/market-data.yml")
    parser.add_argument("--at", help="UTC ISO timestamp; defaults to current UTC time")
    args = parser.parse_args()
    instant = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else datetime.now(timezone.utc)
    cfg = load_config(args.config)
    print(json.dumps({"slot": cfg.slot(instant), "datasets": [d.name for d in cfg.due(instant)]}))
