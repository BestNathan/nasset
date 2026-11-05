"""Immutable event identities for incremental Deribit historical datasets.

Only Git tree metadata is consulted for already archived event keys; no
historical Parquet bodies or full recursive archive are downloaded.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence, Any

from .deribit_publish import PublishConflict, git_blob_sha

PREFIX = b"deribit-event-v1\n"
ExistingLookup = Callable[[tuple[str, ...]], Mapping[str, str]]
EVENT_VALUE = {"historical_volatility": "volatility", "delivery_price": "delivery_price"}


def event_marker(dataset: str, identity: str, row: Mapping[str, Any],
                 *, observed_at: datetime) -> tuple[str, bytes]:
    """Encode a stable index/asset + native timestamp + numeric-value event key.

    The event date partitions the marker; publication date partitions its
    Parquet part. Thus a late event can be discovered in any later poll.
    """
    if dataset not in EVENT_VALUE:
        raise ValueError("dataset has no incremental event semantics")
    if not isinstance(identity, str) or not identity or any(
        not (ch.isascii() and (ch.isalnum() or ch == "_")) for ch in identity
    ):
        raise ValueError("invalid event identity")
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    timestamp = row.get("event_timestamp")
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("native event timestamp required")
    event_at = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    if event_at > observed_at.astimezone(timezone.utc):
        raise ValueError("event timestamp is later than capture time")
    value = row.get(EVENT_VALUE[dataset])
    if type(value) not in (int, float):
        raise ValueError("native event numeric value required")
    # JSON forbids NaN/Inf; source adapters reject negative values.
    payload = PREFIX + json.dumps(
        {"dataset": dataset, "identity": identity, "timestamp": timestamp,
         "value": value},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    marker = (f"deribit/event_keys/{dataset}/{identity}/"
              f"{event_at:%Y/%m/%d}/{timestamp}.key")
    return marker, payload


def unseen_events(
    dataset: str, identity: str, rows: Sequence[Mapping[str, Any]],
    *, observed_at: datetime, existing: ExistingLookup,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    """Classify all returned events before publishing anything.

    Existing identity+value is a no-op, a revised historical value is a hard
    conflict, and absent identities become immutable markers in the same Git
    commit as their Parquet part. Ref movement between lookup and publication
    is caught by the publisher's non-forced ref update / path conflict check.
    """
    pending: dict[str, tuple[dict[str, Any], bytes]] = {}
    for row in rows:
        path, blob = event_marker(dataset, identity, row, observed_at=observed_at)
        if path in pending and pending[path][1] != blob:
            raise PublishConflict(f"conflicting source events for {path}")
        pending[path] = (dict(row), blob)
    if not pending:
        return [], {}
    found = existing(tuple(sorted(pending)))
    unexpected = set(found) - set(pending)
    if unexpected:
        raise ValueError(f"lookup returned unrelated event paths: {sorted(unexpected)[:3]}")
    fresh = []
    markers = {}
    for path in sorted(pending):
        row, data = pending[path]
        previous = found.get(path)
        if previous is not None:
            if previous != git_blob_sha(data):
                raise PublishConflict(f"historical source event changed: {path}")
            continue
        fresh.append(row)
        markers[path] = data
    return fresh, markers
