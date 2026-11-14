"""Append-only GitHub Git Data API publisher for validated Parquet parts.

No checkout or fetch of the archive branch. A single commit contains all due
datasets, and an existing slot is never overwritten or silently repaired.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Mapping

import requests

from .deribit_slot import SLOT_PREFIX
from .deribit_rwa_lifecycle import parse_state

_PATH = re.compile(r"^deribit/[a-z_]+/(?:[A-Z0-9]+/)?[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.parquet$")
_EVENT_KEY_PATH = re.compile(
    r"^deribit/event_keys/(?:historical_volatility/[A-Z0-9]+|delivery_price/[a-z0-9_]+)/"
    r"[0-9]{4}/[0-9]{2}/[0-9]{2}/(?:[0-9]{10,16}|[0-9]{4}-[0-9]{2}-[0-9]{2})\.key$"
)
_EVENT_PREFIX = b"deribit-event-v1\n"
_COVERAGE_PATH = re.compile(
    r"^deribit/coverage/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.json$"
)
_RWA_STATE_PATH = re.compile(
    r"^deribit/rwa_state/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.json$"
)
_SLOT_PATH = re.compile(r"^deribit/slots/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.receipt$")
_MANIFEST_PATH = re.compile(
    r"^deribit/slots/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.manifest\.json$"
)
_MANIFEST_PREFIX = b"deribit-manifest-v1\n"
_MANIFEST_MAX_BYTES = 16 * 1024 * 1024
# Public bound shared with the read-only Pages consumer of this manifest contract.
MANIFEST_MAX_BYTES = _MANIFEST_MAX_BYTES
# Avoid one POST /git/blobs for each of thousands of historical event keys.
# Git Trees support inline UTF-8 content for blobs. Build small tree batches,
# and move the ref only after every batch succeeds.
TREE_BATCH_SIZE = 250
_EXPECTED_TIP_UNSET = object()
_RWA_STATE_MAX_BYTES = 1024 * 1024


class PublishConflict(RuntimeError):
    """Archive already has an incomplete/different slot or ref moved."""


@dataclass(frozen=True)
class PublishResult:
    commit_sha: str
    created: bool
    paths: tuple[str, ...]


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}".encode() + bytes([0])
    return hashlib.sha1(header + data).hexdigest()


def slot_manifest(parts: Mapping[str, bytes], receipt_path: str) -> tuple[str, bytes]:
    """Inventory every *actual* published blob, including conditional events.

    The deterministic receipt is retained for no-source preflight. The
    manifest is created only when the batch is ready, and committed atomically
    with all artifacts and that receipt.
    """
    if not _SLOT_PATH.fullmatch(receipt_path) or receipt_path not in parts:
        raise ValueError("slot manifest needs its receipt")
    manifest_path = receipt_path.removesuffix(".receipt") + ".manifest.json"
    if manifest_path in parts:
        raise ValueError("slot manifest path is reserved")
    document = {
        "version": 1,
        "provider": "deribit",
        "receipt_path": receipt_path,
        "receipt_sha": git_blob_sha(parts[receipt_path]),
        "artifacts": [
            {"path": path, "sha": git_blob_sha(data)}
            for path, data in sorted(parts.items())
        ],
    }
    payload = _MANIFEST_PREFIX + json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if len(payload) > _MANIFEST_MAX_BYTES:
        raise ValueError("slot manifest exceeds Git blob safety limit")
    return manifest_path, payload


class GitHubArchivePublisher:
    def __init__(self, repository: str, token: str, *, session: requests.Session | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        if not token:
            raise ValueError("GitHub token required")
        self.url = f"https://api.github.com/repos/{repository}"
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _api(self, method: str, path: str, *, payload: dict | None = None,
             missing_ok: bool = False) -> dict | None:
        # GitHub may transiently 502/503/504 during scoped tree lookups.
        # Retrying a GET is safe; never automatically replay POST/PATCH, whose
        # write may have succeeded before the response was lost. Any exhausted
        # GET failure aborts publication without touching the archive ref.
        max_attempts = 5 if method == "GET" else 1
        for attempt in range(max_attempts):
            try:
                response = self.session.request(
                    method, self.url + path, json=payload, timeout=30,
                )
            except requests.RequestException:
                if attempt + 1 == max_attempts:
                    raise
                time.sleep(min(4.0, 0.5 * 2**attempt))
                continue
            if (method == "GET" and response.status_code in (429, 500, 502, 503, 504)
                    and attempt + 1 < max_attempts):
                time.sleep(min(4.0, 0.5 * 2**attempt))
                continue
            if missing_ok and response.status_code == 404:
                return None
            if response.status_code in (409, 412, 422):
                raise PublishConflict(f"GitHub {method} {path}: HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()
        raise AssertionError("unreachable GitHub retry state")

    def repository_size_kib(self) -> int:
        """GitHub's repository object-store size estimate (KiB), all refs."""
        meta = self._api("GET", "")
        size = meta.get("size") if isinstance(meta, dict) else None
        if type(size) is not int or size < 0:
            raise RuntimeError("GitHub repository size unavailable")
        return size

    def existing(self, paths: tuple[str, ...]) -> dict[str, str]:
        """Read only Git tree SHAs of requested immutable event-key paths.

        Missing orphan ref means no historical identities exist yet. This is
        read-only and never retrieves historical Parquet contents.
        """
        if any(not _EVENT_KEY_PATH.fullmatch(path) for path in paths):
            raise ValueError("invalid incremental event-key path")
        if not paths:
            return {}
        ref = self._api("GET", "/git/ref/heads/market-data", missing_ok=True)
        if ref is None:
            return {}
        commit = self._api("GET", f"/git/commits/{ref['object']['sha']}")
        return self._existing_event_paths(commit["tree"]["sha"], paths)

    def _existing_event_paths(self, root_tree: str,
                              paths: tuple[str, ...]) -> dict[str, str]:
        """Resolve native historical keys using *year-scoped* recursive trees.

        Daily polls return years of native events; walking each event's day
        separately would cost thousands of GitHub Tree requests. A single
        bounded year subtree covers at most a year's events for one identity.
        Never use recursive Git trees from repository root, where GitHub
        truncates large archives. Reject any truncated scoped result.
        """
        direct_cache: dict[str, dict[str, dict]] = {}

        def direct(sha: str) -> dict[str, dict]:
            if sha not in direct_cache:
                page = self._api("GET", f"/git/trees/{sha}")
                if not isinstance(page, dict) or page.get("truncated"):
                    raise RuntimeError("GitHub tree truncated while resolving year scope")
                direct_cache[sha] = {
                    entry["path"]: entry for entry in page["tree"]
                }
            return direct_cache[sha]

        grouped: dict[tuple[str, ...], dict[str, str]] = {}
        for path in paths:
            segments = path.split("/")
            # e.g. deribit/event_keys/delivery_price/btc_usd/2026
            prefix = tuple(segments[:5])
            grouped.setdefault(prefix, {})["/".join(segments[5:])] = path

        found: dict[str, str] = {}
        for prefix, requested in sorted(grouped.items()):
            sha = root_tree
            for segment in prefix:
                child = direct(sha).get(segment)
                if child is None:
                    break
                if child.get("type") != "tree":
                    raise PublishConflict(
                        f"non-directory in historical key prefix: {'/'.join(prefix)}"
                    )
                sha = child["sha"]
            else:
                annual = self._api("GET", f"/git/trees/{sha}?recursive=1")
                if not isinstance(annual, dict) or annual.get("truncated"):
                    raise RuntimeError("GitHub historical year tree truncated; refusing data loss")
                for entry in annual["tree"]:
                    relative = entry["path"]
                    target = requested.get(relative)
                    if target is not None:
                        if entry.get("type") != "blob":
                            raise PublishConflict(f"non-blob historical event key: {target}")
                        found[target] = entry["sha"]
        return found

    def latest_rwa_state(self, before_slot) -> tuple[dict | None, str | None]:
        """Read just the latest immutable RWA checkpoint before a UTC slot.

        Never download historical Parquet. Descending tree traversal is bounded
        by calendar directory depth; each ancestor lookup is non-recursive.
        Return branch tip too, to bind publication to the SAME observed state.
        """
        from datetime import datetime, timezone

        if (not isinstance(before_slot, datetime) or before_slot.tzinfo is None
                or before_slot.utcoffset().total_seconds() != 0):
            raise ValueError("RWA checkpoint cutoff must be UTC")
        cutoff = before_slot.strftime("%Y/%m/%d/%H00.json")
        ref = self._api("GET", "/git/ref/heads/market-data", missing_ok=True)
        if ref is None:
            return None, None
        tip = ref["object"]["sha"]
        commit = self._api("GET", f"/git/commits/{tip}")
        tree_sha = commit["tree"]["sha"]

        def entries(sha):
            record = self._api("GET", f"/git/trees/{sha}")
            if (not isinstance(record, dict) or record.get("truncated")
                    or not isinstance(record.get("tree"), list)):
                raise PublishConflict("invalid/truncated RWA catalog tree")
            return record["tree"]

        for directory in ("deribit", "rwa_state"):
            item = next((x for x in entries(tree_sha) if x["path"] == directory), None)
            if item is None:
                return None, tip  # First deployment; no legacy state exists.
            if item["type"] != "tree":
                raise PublishConflict(f"RWA checkpoint directory is not a tree: {directory}")
            tree_sha = item["sha"]

        def newest(sha, prefix=(), depth=0):
            expected = (r"[0-9]{4}", r"[0-9]{2}", r"[0-9]{2}", r"[0-9]{4}\.json")[depth]
            for item in sorted(entries(sha), key=lambda x: x["path"], reverse=True):
                name = item["path"]
                if not re.fullmatch(expected, name):
                    raise PublishConflict("unknown path in immutable RWA checkpoint tree")
                next_prefix = (*prefix, name)
                key = "/".join(next_prefix)
                if depth == 3:
                    if key < cutoff:
                        if item["type"] != "blob":
                            raise PublishConflict("RWA checkpoint is not a blob")
                        return key, item["sha"]
                else:
                    if item["type"] != "tree":
                        raise PublishConflict("RWA checkpoint component is not a directory")
                    # Skip whole future calendar subtrees safely.
                    if key > "/".join(cutoff.split("/")[:depth + 1]):
                        continue
                    found = newest(item["sha"], next_prefix, depth + 1)
                    if found is not None:
                        return found
            return None

        found = newest(tree_sha)
        if found is None:
            return None, tip
        subpath, sha = found
        path = "deribit/rwa_state/" + subpath
        blob = self._api("GET", f"/git/blobs/{sha}")
        try:
            if not isinstance(blob, dict) or blob.get("encoding") != "base64":
                raise ValueError("RWA checkpoint blob encoding invalid")
            payload = base64.b64decode("".join(blob["content"].split()), validate=True)
            if len(payload) > _RWA_STATE_MAX_BYTES or git_blob_sha(payload) != sha:
                raise ValueError("RWA checkpoint corrupt or oversized")
            self._validate_rwa_state(path, payload)
            state = parse_state(json.loads(payload))
        except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
            raise PublishConflict("RWA checkpoint corrupted; refusing continuity inference") from exc
        return state, tip

    def completed_slot(self, receipt: tuple[str, bytes]) -> bool:
        """Check exact due-slot completion without fetching stored Parquet blobs.

        A changed due configuration is a hard conflict, not a reason to
        overwrite or silently accept an incompatible historical slot.
        """
        path, payload = receipt
        self._validate_receipt(path, payload)
        ref = self._api("GET", "/git/ref/heads/market-data", missing_ok=True)
        if ref is None:
            return False
        commit = self._api("GET", f"/git/commits/{ref['object']['sha']}")
        existing = self._existing_paths(commit["tree"]["sha"], (path,))
        if path not in existing:
            return False
        if existing[path] != git_blob_sha(payload):
            raise PublishConflict(f"UTC slot already published with different due config: {path}")
        self._verify_published_slot(commit["tree"]["sha"], path, payload)
        return True

    def _verify_published_slot(self, root_tree: str, receipt_path: str,
                               receipt_payload: bytes) -> None:
        """Fail closed unless EVERY committed artifact retains its Git blob SHA.

        Only the small versioned manifest blob is downloaded. Parquet files are
        never fetched; historical markers are validated via bounded year trees.
        This also rejects receipts predating the manifest contract.
        """
        manifest_path = receipt_path.removesuffix(".receipt") + ".manifest.json"
        manifest_sha = self._existing_paths(root_tree, (manifest_path,)).get(manifest_path)
        if manifest_sha is None:
            raise PublishConflict("UTC slot receipt exists without artifact manifest")
        blob = self._api("GET", f"/git/blobs/{manifest_sha}")
        try:
            if not isinstance(blob, dict) or blob.get("encoding") != "base64":
                raise ValueError("unexpected manifest blob encoding")
            encoded = blob["content"]
            if not isinstance(encoded, str):
                raise ValueError("invalid manifest blob content")
            payload = base64.b64decode("".join(encoded.split()), validate=True)
            if (len(payload) > _MANIFEST_MAX_BYTES
                    or git_blob_sha(payload) != manifest_sha
                    or not payload.startswith(_MANIFEST_PREFIX)):
                raise ValueError("invalid manifest blob checksum/prefix")
            manifest = json.loads(payload[len(_MANIFEST_PREFIX):])
        except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
            raise PublishConflict("malformed or corrupted UTC slot artifact manifest") from exc
        if (not isinstance(manifest, dict)
                or manifest.get("version") != 1
                or manifest.get("provider") != "deribit"
                or manifest.get("receipt_path") != receipt_path
                or manifest.get("receipt_sha") != git_blob_sha(receipt_payload)):
            raise PublishConflict("UTC slot artifact manifest disagrees with receipt")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise PublishConflict("UTC slot artifact manifest has no inventory")
        expected: dict[str, str] = {}
        stamp = receipt_path.removeprefix("deribit/slots/").removesuffix(".receipt")
        for item in artifacts:
            if not isinstance(item, dict) or set(item) != {"path", "sha"}:
                raise PublishConflict("invalid UTC slot artifact inventory entry")
            path, sha = item["path"], item["sha"]
            if (not isinstance(path, str) or not isinstance(sha, str)
                    or not re.fullmatch(r"[0-9a-f]{40}", sha)
                    or not (_PATH.fullmatch(path) or _EVENT_KEY_PATH.fullmatch(path)
                            or _COVERAGE_PATH.fullmatch(path) or _RWA_STATE_PATH.fullmatch(path)
                            or _SLOT_PATH.fullmatch(path))
                    or path in expected):
                raise PublishConflict("invalid/duplicate UTC slot artifact inventory path")
            if (_PATH.fullmatch(path) and not path.endswith(stamp + ".parquet")):
                raise PublishConflict("manifest contains a Parquet file from another UTC slot")
            if (_COVERAGE_PATH.fullmatch(path) and not path.endswith(stamp + ".json")):
                raise PublishConflict("manifest contains coverage from another UTC slot")
            if (_RWA_STATE_PATH.fullmatch(path) and not path.endswith(stamp + ".json")):
                raise PublishConflict("manifest contains RWA state from another UTC slot")
            if _SLOT_PATH.fullmatch(path) and path != receipt_path:
                raise PublishConflict("manifest contains a different UTC slot receipt")
            expected[path] = sha
        if list(expected) != sorted(expected):
            raise PublishConflict("UTC slot artifact inventory is not canonical")
        if (expected.get(receipt_path) != git_blob_sha(receipt_payload)
                or not set(self._receipt_required_paths(receipt_payload)).issubset(expected)
                or not any(_PATH.fullmatch(path) for path in expected)):
            raise PublishConflict("UTC slot manifest omits mandatory receipt/snapshots")
        event_keys = tuple(path for path in expected if _EVENT_KEY_PATH.fullmatch(path))
        other = tuple(path for path in expected if path not in event_keys)
        actual = self._existing_paths(root_tree, other)
        actual.update(self._existing_event_paths(root_tree, event_keys))
        mismatches = [path for path, sha in expected.items() if actual.get(path) != sha]
        if mismatches:
            raise PublishConflict(
                f"UTC slot artifact missing/changed ({len(mismatches)}): {mismatches[0]}"
            )

    @staticmethod
    def _receipt_required_paths(payload: bytes) -> tuple[str, ...]:
        try:
            paths = json.loads(payload[len(SLOT_PREFIX):])["required_paths"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("receipt lacks mandatory path manifest") from exc
        if (not isinstance(paths, list) or len(paths) != len(set(paths))
                or any(not isinstance(path, str) or not (
                    _PATH.fullmatch(path) or _RWA_STATE_PATH.fullmatch(path)
                ) for path in paths)):
            raise ValueError("invalid receipt mandatory paths")
        return tuple(paths)

    @staticmethod
    def _validate_receipt(path: str, payload: bytes) -> None:
        if not isinstance(path, str) or not _SLOT_PATH.fullmatch(path):
            raise ValueError(f"invalid UTC slot receipt path: {path!r}")
        if not isinstance(payload, bytes) or not payload.startswith(SLOT_PREFIX) or len(payload) > 16384:
            raise ValueError(f"invalid UTC slot receipt payload: {path}")
        GitHubArchivePublisher._receipt_required_paths(payload)

    @staticmethod
    def _validate_coverage(path: str, payload: bytes) -> None:
        if not _COVERAGE_PATH.fullmatch(path) or not payload or len(payload) > 16384:
            raise ValueError(f"invalid coverage manifest path/size: {path}")
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("coverage must be a JSON object")
            slot = data["snapshot_slot_at"]
            entries = data["unlisted"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid coverage JSON: {path}") from exc
        stamp = path.removeprefix("deribit/coverage/").removesuffix(".json")
        year, month, day, hhmm = stamp.split("/")
        expected_slot = f"{year}-{month}-{day}T{hhmm[:2]}:{hhmm[2:]}:00+00:00"
        if (type(data.get("schema_version")) is not int
                or data["schema_version"] != 1
                or data.get("provider") != "deribit"
                or slot != expected_slot
                or not isinstance(data.get("observed_at"), str)
                or not isinstance(entries, list) or not entries):
            raise ValueError(f"invalid coverage manifest schema: {path}")
        seen = set()
        for row in entries:
            if (not isinstance(row, dict)
                    or row.get("dataset") not in {"option_chain", "futures_curve"}
                    or not isinstance(row.get("underlying"), str)
                    or not re.fullmatch(r"[A-Z0-9]+", row["underlying"])
                    or type(row.get("listed_count")) is not int
                    or row["listed_count"] != 0):
                raise ValueError(f"invalid unlisted capability record: {path}")
            identity = (row["dataset"], row["underlying"])
            if identity in seen:
                raise ValueError(f"duplicate unlisted capability record: {path}")
            seen.add(identity)

    @staticmethod
    def _validate_rwa_state(path: str, payload: bytes) -> None:
        if (not _RWA_STATE_PATH.fullmatch(path) or len(payload) > _RWA_STATE_MAX_BYTES
                or not payload):
            raise ValueError("invalid RWA checkpoint path or size")
        state = parse_state(json.loads(payload))
        stamp = state["snapshot_slot_at"]
        expected = path.removeprefix("deribit/rwa_state/").removesuffix(".json")
        from datetime import datetime
        if datetime.fromisoformat(stamp).strftime("%Y/%m/%d/%H00") != expected:
            raise ValueError("RWA checkpoint slot does not match path")

    def publish(self, parts: Mapping[str, bytes], *, branch: str = "market-data",
                slot_receipt: tuple[str, bytes] | None = None,
                expected_tip: str | None | object = _EXPECTED_TIP_UNSET) -> PublishResult:
        if branch != "market-data":
            raise ValueError("only market-data orphan branch is supported")
        if not parts:
            raise ValueError("cannot publish empty batch")
        if slot_receipt is not None:
            receipt_path, receipt_payload = slot_receipt
            self._validate_receipt(receipt_path, receipt_payload)
            suffix = receipt_path.removeprefix("deribit/slots/").removesuffix(".receipt")
            if any(not path.endswith(suffix + ".parquet")
                   for path in parts if path.endswith(".parquet")):
                raise ValueError("Parquet files must belong to the receipt UTC slot")
            if any(not path.endswith(suffix + ".json")
                   for path in parts if _COVERAGE_PATH.fullmatch(path)):
                raise ValueError("coverage manifest must belong to the receipt UTC slot")
            if any(not path.endswith(suffix + ".json")
                   for path in parts if _RWA_STATE_PATH.fullmatch(path)):
                raise ValueError("RWA checkpoint must belong to the receipt UTC slot")
            if receipt_path in parts or any(_MANIFEST_PATH.fullmatch(p) for p in parts):
                raise ValueError("receipt/manifest path must not appear in raw parts")
            if not set(self._receipt_required_paths(receipt_payload)).issubset(parts):
                raise ValueError("batch missing mandatory snapshot Parquet paths")
            parts = {**parts, receipt_path: receipt_payload}
            manifest_path, manifest_payload = slot_manifest(parts, receipt_path)
            parts = {**parts, manifest_path: manifest_payload}
        for path, payload in parts.items():
            if not isinstance(path, str) or not (_PATH.fullmatch(path) or _EVENT_KEY_PATH.fullmatch(path)
                                                  or _SLOT_PATH.fullmatch(path)
                                                  or _COVERAGE_PATH.fullmatch(path)
                                                  or _RWA_STATE_PATH.fullmatch(path)
                                                  or _MANIFEST_PATH.fullmatch(path)):
                raise ValueError(f"invalid archive path: {path!r}")
            if not isinstance(payload, bytes):
                raise ValueError(f"invalid archive payload: {path}")
            if _EVENT_KEY_PATH.fullmatch(path):
                if not payload.startswith(_EVENT_PREFIX) or len(payload) > 512:
                    raise ValueError(f"invalid event key payload: {path}")
            elif _SLOT_PATH.fullmatch(path):
                self._validate_receipt(path, payload)
            elif _COVERAGE_PATH.fullmatch(path):
                self._validate_coverage(path, payload)
            elif _RWA_STATE_PATH.fullmatch(path):
                self._validate_rwa_state(path, payload)
            elif _MANIFEST_PATH.fullmatch(path):
                if not payload.startswith(_MANIFEST_PREFIX):
                    raise ValueError(f"invalid artifact manifest: {path}")
            elif not payload.startswith(b"PAR1") or not payload.endswith(b"PAR1"):
                raise ValueError(f"not a Parquet payload: {path}")
        if not any(path.endswith(".parquet") for path in parts):
            raise ValueError("event markers require a Parquet part in the same batch")

        names = tuple(sorted(parts))
        ref = self._api("GET", "/git/ref/heads/market-data", missing_ok=True)
        if (expected_tip is not _EXPECTED_TIP_UNSET
                and (ref["object"]["sha"] if ref is not None else None) != expected_tip):
            raise PublishConflict("RWA checkpoint archive tip changed since observation")
        if ref is None:
            # The branch must be created as a zero-parent root, not from main.
            tree_sha = self._build_tree(parts, base_tree=None)
            commit = self._api("POST", "/git/commits", payload={
                "message": "archive: initial Deribit market-data batch",
                "tree": tree_sha, "parents": [],
            })
            try:
                self._api("POST", "/git/refs", payload={
                    "ref": "refs/heads/market-data", "sha": commit["sha"],
                })
            except PublishConflict:
                raise PublishConflict("archive branch was concurrently initialized") from None
            return PublishResult(commit["sha"], True, names)

        tip = ref["object"]["sha"]
        commit = self._api("GET", f"/git/commits/{tip}")
        base_tree = commit["tree"]["sha"]
        # Walk only the directory prefixes of requested parts. Recursive trees
        # eventually truncate in a long-lived archive and must not be used.
        paths = self._existing_paths(base_tree, names)
        if slot_receipt is not None and receipt_path in paths:
            if paths[receipt_path] != git_blob_sha(receipt_payload):
                raise PublishConflict("UTC slot was already completed with a different configuration")
            self._verify_published_slot(base_tree, receipt_path, receipt_payload)
            # Receipt is an atomic commit marker. The same config and paths
            # represent an already published slot even if a later capture
            # produces different Parquet bytes and timestamps.
            return PublishResult(tip, False, names)
        matches = [path for path in names if paths.get(path) == git_blob_sha(parts[path])]
        if len(matches) == len(names):
            return PublishResult(tip, False, names)
        if any(path in paths for path in names):
            raise PublishConflict("slot contains partial or conflicting immutable archive paths")

        tree_sha = self._build_tree(parts, base_tree=base_tree)
        next_commit = self._api("POST", "/git/commits", payload={
            "message": "archive: Deribit UTC batch " + names[0].split("/")[-1],
            "tree": tree_sha, "parents": [tip],
        })
        # force=false rejects concurrent branch movement; caller must retry
        # by re-reading and validating the *whole* batch.
        self._api("PATCH", "/git/refs/heads/market-data", payload={
            "sha": next_commit["sha"], "force": False,
        })
        return PublishResult(next_commit["sha"], True, names)

    def _existing_paths(self, root_tree: str, paths: tuple[str, ...]) -> dict[str, str]:
        """Resolve requested paths using non-recursive Git trees only.

        Shared directory prefixes are fetched once. A missing directory means
        its descendants cannot exist; a blob in place of a directory is a
        conflict rather than permission to replace existing archive content.
        """
        cache: dict[str, dict[str, dict]] = {}

        def entries(sha: str) -> dict[str, dict]:
            if sha not in cache:
                response = self._api("GET", f"/git/trees/{sha}")
                if response.get("truncated"):
                    raise RuntimeError("GitHub tree truncated; refusing unsafe publish")
                cache[sha] = {entry["path"]: entry for entry in response["tree"]}
            return cache[sha]

        found: dict[str, str] = {}
        for path in paths:
            segments = path.split("/")
            tree_sha = root_tree
            for segment in segments[:-1]:
                entry = entries(tree_sha).get(segment)
                if entry is None:
                    break
                if entry["type"] != "tree":
                    raise PublishConflict(f"non-directory in archive path: {path}")
                tree_sha = entry["sha"]
            else:
                leaf = entries(tree_sha).get(segments[-1])
                if leaf is not None:
                    if leaf["type"] != "blob":
                        raise PublishConflict(f"non-file archive target: {path}")
                    found[path] = leaf["sha"]
        return found

    def _build_tree(self, parts: Mapping[str, bytes], *, base_tree: str | None) -> str:
        """Build an immutable candidate tree without moving a branch ref.

        Small trusted UTF-8 receipt/event/coverage markers are inlined via the
        Git Tree API, avoiding O(event-count) POST /git/blobs rate-limits on
        first historical delivery-price backfill (~10k marker objects). Native
        Parquet binary blobs use the Git Blob API and verified Git SHA-1.
        Tree batches only create unreachable candidates until the final
        commit/ref update succeeds; failure cannot partially publish a slot.
        """
        tree_sha = base_tree
        entries = []
        for path in sorted(parts):
            entry = {"path": path, "mode": "100644", "type": "blob"}
            if (_EVENT_KEY_PATH.fullmatch(path) or _SLOT_PATH.fullmatch(path)
                    or _COVERAGE_PATH.fullmatch(path) or _RWA_STATE_PATH.fullmatch(path)
                    or _MANIFEST_PATH.fullmatch(path)):
                try:
                    entry["content"] = parts[path].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"invalid UTF-8 marker: {path}") from exc
            else:
                blob = self._api("POST", "/git/blobs", payload={
                    "content": base64.b64encode(parts[path]).decode("ascii"),
                    "encoding": "base64",
                })
                if blob["sha"] != git_blob_sha(parts[path]):
                    raise RuntimeError(f"GitHub blob checksum mismatch: {path}")
                entry["sha"] = blob["sha"]
            entries.append(entry)
            if len(entries) == TREE_BATCH_SIZE:
                tree_sha = self._publish_tree_chunk(entries, tree_sha)
                entries = []
        if entries:
            tree_sha = self._publish_tree_chunk(entries, tree_sha)
        if not tree_sha:
            raise ValueError("cannot build empty Git tree")
        return tree_sha

    def _publish_tree_chunk(self, entries: list[dict], base: str | None) -> str:
        body = {"tree": entries}
        if base is not None:
            body["base_tree"] = base
        result = self._api("POST", "/git/trees", payload=body)
        if not isinstance(result, dict) or not result.get("sha"):
            raise RuntimeError("GitHub failed to return candidate tree SHA")
        return result["sha"]
