"""Build a read-only GitHub Pages projection of immutable Deribit Parquet archives.

Runs in GitHub Actions after a verified archive publication (and during daily
Pages builds). Never invokes Deribit live APIs, mutates market-data, backfills
missing slots, or publishes derived values as native exchange observations.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pyarrow.parquet as pq
import requests

from nasset.deribit_publish import git_blob_sha, MANIFEST_MAX_BYTES
from nasset.deribit_analytics import derive_analytics

RECEIPT_PREFIX = b"deribit-slot-v1\n"
MANIFEST_PREFIX = b"deribit-manifest-v1\n"
SLOT_PATTERN = re.compile(r"^deribit/slots/(\d{4})/(\d{2})/(\d{2})/(\d{2})00\.receipt$")
MAX_SLOTS = 24


class ArchiveIntegrityError(RuntimeError):
    """The published archive did not pass the manifest / receipt contract."""


class GitHubReader:
    def __init__(self, repository: str, token: str, session=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid GitHub repository")
        self.base = f"https://api.github.com/repos/{repository}"
        self.repository = repository
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def get(self, path: str):
        response = self.session.get(self.base + path, timeout=30)
        response.raise_for_status()
        return response.json()

    def tree(self):
        # Pin a commit and its tree so concurrent hourly writes cannot mix versions.
        ref = self.get("/git/ref/heads/market-data")
        commit_sha = ref["object"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", commit_sha):
            raise ArchiveIntegrityError("invalid market-data commit")
        commit = self.get("/git/commits/" + commit_sha)
        tree_sha = commit["tree"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", tree_sha):
            raise ArchiveIntegrityError("invalid market-data tree")
        data = self.get("/git/trees/" + tree_sha + "?recursive=1")
        if data.get("truncated") or not isinstance(data.get("tree"), list):
            raise ArchiveIntegrityError("archive Git tree is truncated or missing")
        data["archive_commit"] = commit_sha
        return data

    def blob(self, sha: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ArchiveIntegrityError("invalid blob SHA")
        data = self.get("/git/blobs/" + sha)
        if data.get("encoding") != "base64":
            raise ArchiveIntegrityError("unexpected GitHub blob encoding")
        raw = base64.b64decode(data["content"], validate=False)
        if git_blob_sha(raw) != sha:
            raise ArchiveIntegrityError("Git blob integrity mismatch")
        return raw


def _marker(raw: bytes, prefix: bytes, name: str) -> dict:
    # The publisher permits up to 16 MiB: daily native event-key inventories
    # regularly exceed 1 MiB even though hourly-only manifests are tiny.
    # Keep the receipt bound narrow, but share the manifest bound with publisher.
    max_size = MANIFEST_MAX_BYTES if name == "manifest" else 16 * 1024
    if not raw.startswith(prefix) or len(raw) > max_size:
        raise ArchiveIntegrityError("invalid " + name + " marker")
    try:
        doc = json.loads(raw[len(prefix):])
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArchiveIntegrityError("invalid " + name + " JSON") from exc
    if not isinstance(doc, dict) or doc.get("version") != 1 or doc.get("provider") != "deribit":
        raise ArchiveIntegrityError("unsupported " + name + " version or provider")
    return doc


def _slot_iso(receipt_path: str) -> str:
    match = SLOT_PATTERN.fullmatch(receipt_path)
    if not match:
        raise ArchiveIntegrityError("invalid UTC receipt path")
    year, month, day, hour = map(int, match.groups())
    return datetime(year, month, day, hour, tzinfo=timezone.utc).isoformat()


def _finite(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def parquet_rows(reader: GitHubReader, sha: str, dataset: str, slot: str, columns: list[str]) -> list[dict]:
    raw = reader.blob(sha)
    if not raw.startswith(b"PAR1") or not raw.endswith(b"PAR1"):
        raise ArchiveIntegrityError("invalid Parquet payload")
    table = pq.read_table(BytesIO(raw), columns=["snapshot_slot_at", "captured_at", "dataset", *columns])
    if table.num_rows == 0:
        raise ArchiveIntegrityError("empty archive Parquet part")
    if table.num_rows > 20000:
        raise ArchiveIntegrityError("unexpectedly large archive Parquet part")
    rows = table.to_pylist()
    for row in rows:
        observed = row.pop("snapshot_slot_at")
        capture = row.pop("captured_at")
        observed_iso = observed.astimezone(timezone.utc).isoformat() if observed else None
        row["captured_at"] = capture.astimezone(timezone.utc).isoformat() if capture else None
        if row.pop("dataset") != dataset or observed_iso != slot:
            raise ArchiveIntegrityError("Parquet dataset/slot identity mismatch")
        for key, value in row.items():
            if isinstance(value, float):
                row[key] = _finite(value)
    return rows


def verified_slots(reader: GitHubReader, tree: dict, limit: int = MAX_SLOTS):
    blobs = {obj["path"]: obj for obj in tree["tree"] if obj.get("type") == "blob"}
    receipt_paths = sorted(p for p in blobs if SLOT_PATTERN.fullmatch(p))
    slots = []
    for receipt_path in receipt_paths[-limit:]:
        stamp = receipt_path.removesuffix(".receipt")
        manifest_path = stamp + ".manifest.json"
        if manifest_path not in blobs:
            raise ArchiveIntegrityError("slot missing canonical manifest: " + stamp)
        receipt = _marker(reader.blob(blobs[receipt_path]["sha"]), RECEIPT_PREFIX, "receipt")
        manifest = _marker(reader.blob(blobs[manifest_path]["sha"]), MANIFEST_PREFIX, "manifest")
        slot_iso = _slot_iso(receipt_path)
        if (receipt.get("utc_slot") != slot_iso or manifest.get("receipt_path") != receipt_path
                or manifest.get("receipt_sha") != blobs[receipt_path]["sha"]):
            raise ArchiveIntegrityError("slot metadata mismatch: " + stamp)
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ArchiveIntegrityError("empty slot manifest: " + stamp)
        expected = {}
        for entry in artifacts:
            if not isinstance(entry, dict):
                raise ArchiveIntegrityError("invalid manifest inventory")
            path, sha = entry.get("path"), entry.get("sha")
            if not isinstance(path, str) or not isinstance(sha, str) or path in expected:
                raise ArchiveIntegrityError("invalid or duplicated manifest path")
            expected[path] = sha
            if blobs.get(path, {}).get("sha") != sha:
                raise ArchiveIntegrityError("missing or changed manifest artifact: " + path)
        required = receipt.get("required_paths")
        if (not isinstance(required, list) or not required
                or not set(required).issubset(expected)
                or expected.get(receipt_path) != blobs[receipt_path]["sha"]):
            raise ArchiveIntegrityError("receipt required paths missing from manifest")
        if any(not path.endswith(stamp.rsplit("/", 1)[-1] + ".parquet")
               for path in expected if path.endswith(".parquet")):
            raise ArchiveIntegrityError("unexpected batch filename in manifest")
        slots.append({
            "slot": slot_iso,
            "receipt_path": receipt_path,
            "manifest_path": manifest_path,
            "artifacts": expected,
            "artifact_count": len(artifacts),
            "parquet_bytes": sum(blobs[p].get("size", 0) for p in expected if p.endswith(".parquet")),
        })
    return slots, blobs


OPTION_FIELDS = [
    "instrument_name", "base_currency", "option_type", "expiration_timestamp",
    "strike", "bid_price", "ask_price", "mark_price", "mark_iv", "underlying_price",
    "underlying_index", "open_interest", "volume_usd", "settlement_currency",
]
FUTURE_FIELDS = [
    "instrument_name", "base_currency", "expiration_timestamp", "mark_price",
    "bid_price", "ask_price", "open_interest", "volume_usd", "settlement_currency",
]
INDEX_FIELDS = ["index_name", "base_currency", "quote_currency", "index_price"]
DVOL_FIELDS = ["base_currency", "event_timestamp", "close"]


def project_archive(reader: GitHubReader, *, max_slots: int = MAX_SLOTS, now: datetime | None = None):
    if not 1 <= max_slots <= 48:
        raise ValueError("max_slots must be between 1 and 48")
    tree = reader.tree()
    slots, blobs = verified_slots(reader, tree, max_slots)
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    result = {
        "version": 1,
        "source": "Deribit / verified GitHub market-data archive",
        "generated_at": generated_at,
        "archive_commit": tree.get("archive_commit"),
        "latest_slot": slots[-1]["slot"] if slots else None,
        "slots": [],
        "history": [],
        "latest": {"option_chain": [], "futures_curve": [], "index_price": [], "volatility_index": []},
    }
    if not slots:
        result["analytics"] = derive_analytics(result["latest"], [], None)
        return result
    latest_slot = slots[-1]["slot"]
    for slot in slots:
        iso, entries = slot["slot"], slot["artifacts"]
        summary = {
            "slot": iso, "artifact_count": slot["artifact_count"],
            "parquet_bytes": slot["parquet_bytes"],
            "receipt_path": slot["receipt_path"], "manifest_path": slot["manifest_path"],
        }
        result["slots"].append(summary)
        point = {"slot": iso, "indexes": [], "dvol": [], "futures": []}
        for path, sha in sorted(entries.items()):
            if path.startswith("deribit/index_price/") and path.endswith(".parquet"):
                rows = parquet_rows(reader, sha, "index_price", iso, INDEX_FIELDS)
                point["indexes"].extend(rows)
            if path.startswith("deribit/volatility_index/") and path.endswith(".parquet"):
                rows = parquet_rows(reader, sha, "volatility_index", iso, DVOL_FIELDS)
                point["dvol"].extend(rows)
            if iso == latest_slot and path.startswith("deribit/option_chain/") and path.endswith(".parquet"):
                rows = parquet_rows(reader, sha, "option_chain", iso, OPTION_FIELDS)
                result["latest"]["option_chain"].extend(rows)
            if path.startswith("deribit/futures_curve/") and path.endswith(".parquet"):
                rows = parquet_rows(reader, sha, "futures_curve", iso, FUTURE_FIELDS)
                point["futures"].extend(rows)
                if iso == latest_slot:
                    result["latest"]["futures_curve"].extend(rows)
        if not point["indexes"] or not point["dvol"]:
            raise ArchiveIntegrityError("hourly index or DVOL dataset missing: " + iso)
        result["history"].append(point)
    result["latest"]["index_price"] = result["history"][-1]["indexes"]
    result["latest"]["volatility_index"] = result["history"][-1]["dvol"]
    if not result["latest"]["option_chain"] or not result["latest"]["futures_curve"]:
        raise ArchiveIntegrityError("latest option/futures coverage incomplete")
    result["analytics"] = derive_analytics(result["latest"], result["history"], latest_slot)
    # Historic curve rows are consumed into the analytics; browsers need only
    # compact tenor grids, not another copy of every native futures quote.
    for point in result["history"]:
        point.pop("futures", None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "BestNathan/nasset"))
    parser.add_argument("--output", default="site/data/deribit-dashboard.json")
    parser.add_argument("--max-slots", type=int, default=MAX_SLOTS)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN is required for read-only market-data archive")
    reader = GitHubReader(args.repository, token)
    data = project_archive(reader, max_slots=args.max_slots)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement: incomplete fetch/validation never overwrites published dashboard data.
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(json.dumps({
        "status": "created", "latest_slot": data["latest_slot"],
        "observed_slots": len(data["slots"]),
        "options": len(data["latest"]["option_chain"]),
        "futures": len(data["latest"]["futures_curve"]),
        "output": str(target),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
