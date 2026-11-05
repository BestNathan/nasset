"""Deterministic, immutable UTC-slot completion receipt for Deribit batches.

The receipt depends only on the due configuration, never captured_at or
the current exchange response. This makes a duplicate scheduler run a true
no-op even if live prices have changed since the first publish.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .deribit_archive import ArchiveConfig

SLOT_PREFIX = b"deribit-slot-v1\n"


def slot_receipt(config: ArchiveConfig, now: datetime) -> tuple[str, bytes]:
    """Compute an immutable Git blob for one UTC slot and its due contract.

    Dataset order is normalized so harmless YAML reordering cannot fabricate
    an incompatible slot. Dataset options, asset scope, interval and ZSTD
    settings are included, so materially changed configurations fail closed.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("slot time must be timezone-aware")
    instant = now.astimezone(timezone.utc)
    slot = instant.replace(minute=0, second=0, microsecond=0)
    due = config.due(slot)
    if not due:
        raise ValueError("no due datasets")
    datasets = [
        {
            "name": dataset.name,
            "interval_hours": dataset.interval_hours,
            "underlyings": sorted(dataset.underlyings),
            "options": dataset.options,
        }
        for dataset in sorted(due, key=lambda dataset: dataset.name)
    ]
    document = {
        "version": 1,
        "provider": "deribit",
        "utc_slot": slot.isoformat(),
        "compression": "zstd",
        "compression_level": config.compression_level,
        "datasets": datasets,
    }
    payload = SLOT_PREFIX + json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    path = f"deribit/slots/{slot:%Y/%m/%d/%H00}.receipt"
    return path, payload
