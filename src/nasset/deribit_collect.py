"""Manual Deribit archive collector (dry-run by default).

No Actions schedule is enabled here. Live source capability and index contracts
must be verified before a production workflow can safely pass --publish.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import pyarrow.parquet as pq
import requests

from .deribit_archive import ArchiveConfig, load_config
from .deribit_client import DeribitClient
from .deribit_pipeline import prepare_live_batch
from .deribit_publish import GitHubArchivePublisher
from .deribit_slot import slot_receipt

CAPACITY_REVIEW_KIB = 3 * 1024 * 1024


def collect_once(
    config: ArchiveConfig, client: DeribitClient,
    publisher: GitHubArchivePublisher, now: datetime, *,
    publish: bool = False,
) -> dict[str, Any]:
    """Prepare every due dataset, then optionally publish a single Git commit.

    The archive lookup may read Git tree metadata but not historical Parquet
    blobs. Any missing/changed history or source error aborts before publish.
    """
    due = config.due(now)
    if not due:
        return {"status": "not_due", "datasets": [], "paths": []}

    receipt = slot_receipt(config, now)
    if publish and publisher.completed_slot(receipt):
        return {
            "status": "already_published",
            "slot": now.astimezone(timezone.utc).replace(
                minute=0, second=0, microsecond=0,
            ).isoformat(),
            "datasets": [d.name for d in due],
            "paths": [],
            "note": "matching immutable UTC slot receipt; source was not queried",
        }

    batch = prepare_live_batch(config, client, now, existing=publisher.existing)
    summaries: dict[str, dict[str, int]] = defaultdict(lambda: {"rows": 0, "bytes": 0, "parts": 0})
    parquet_paths = []
    marker_count = 0
    for path, payload in sorted(batch.parts.items()):
        if path.endswith(".key"):
            marker_count += 1
            continue
        parquet_paths.append(path)
        meta = pq.read_metadata(BytesIO(payload))
        dataset = meta.metadata[b"dataset"].decode("utf-8")
        totals = summaries[dataset]
        totals["rows"] += meta.num_rows
        totals["bytes"] += len(payload)
        totals["parts"] += 1

    result: dict[str, Any] = {
        "status": "prepared",
        "slot": batch.slot.isoformat(),
        "datasets": list(batch.datasets),
        "paths": parquet_paths,
        "metrics": dict(sorted(summaries.items())),
        "event_keys": marker_count,
        "parquet_bytes": sum(len(batch.parts[p]) for p in parquet_paths),
        "batch_bytes": sum(map(len, batch.parts.values())),
    }
    if not batch.parts:
        result["status"] = "no_new_events"
    elif publish:
        saved = publisher.publish(batch.parts, branch=config.branch,
                                  slot_receipt=receipt)
        result["status"] = "created" if saved.created else "already_published"
        result["commit_sha"] = saved.commit_sha

    try:
        size = publisher.repository_size_kib()
        result["repository_size_kib"] = size
        result["capacity_review_required"] = size >= CAPACITY_REVIEW_KIB
    except (RuntimeError, KeyError, ValueError, OSError, requests.RequestException) as exc:
        # A committed batch must never be misreported as uncommitted simply
        # because a subsequent best-effort capacity check was unavailable.
        result["repository_size_warning"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Deribit P0/P1 collector (manual dry-run)")
    parser.add_argument("--config", default="config/market-data.yml")
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument("--at", help="UTC ISO-8601 time; default current UTC time")
    parser.add_argument("--publish", action="store_true",
                        help="Explicitly enable append-only Git publishing after contract verification")
    args = parser.parse_args()
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN with contents access is required")
    instant = (datetime.fromisoformat(args.at.replace("Z", "+00:00"))
               if args.at else datetime.now(timezone.utc))
    if instant.tzinfo is None or instant.utcoffset() != timezone.utc.utcoffset(instant):
        parser.error("--at must use a UTC-aware timestamp")
    config = load_config(args.config)
    result = collect_once(
        config, DeribitClient(),
        GitHubArchivePublisher(args.repository, token),
        instant, publish=args.publish,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
