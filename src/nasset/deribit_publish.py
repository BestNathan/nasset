"""Append-only GitHub Git Data API publisher for validated Parquet parts.

No checkout or fetch of the archive branch. A single commit contains all due
datasets, and an existing slot is never overwritten or silently repaired.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

import requests

from .deribit_slot import SLOT_PREFIX

_PATH = re.compile(r"^deribit/[a-z_]+/(?:[A-Z0-9]+/)?[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.parquet$")
_EVENT_KEY_PATH = re.compile(
    r"^deribit/event_keys/(?:historical_volatility/[A-Z0-9]+|delivery_price/[a-z0-9_]+)/"
    r"[0-9]{4}/[0-9]{2}/[0-9]{2}/(?:[0-9]{10,16}|[0-9]{4}-[0-9]{2}-[0-9]{2})\.key$"
)
_EVENT_PREFIX = b"deribit-event-v1\n"
_COVERAGE_PATH = re.compile(
    r"^deribit/coverage/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.json$"
)
_SLOT_PATH = re.compile(r"^deribit/slots/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.receipt$")


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
        response = self.session.request(method, self.url + path, json=payload, timeout=30)
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code in (409, 412, 422):
            raise PublishConflict(f"GitHub {method} {path}: HTTP {response.status_code}")
        response.raise_for_status()
        return response.json()

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
        return self._existing_paths(commit["tree"]["sha"], paths)

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
        return True

    @staticmethod
    def _validate_receipt(path: str, payload: bytes) -> None:
        if not isinstance(path, str) or not _SLOT_PATH.fullmatch(path):
            raise ValueError(f"invalid UTC slot receipt path: {path!r}")
        if not isinstance(payload, bytes) or not payload.startswith(SLOT_PREFIX) or len(payload) > 16384:
            raise ValueError(f"invalid UTC slot receipt payload: {path}")

    @staticmethod
    def _validate_coverage(path: str, payload: bytes) -> None:
        if not _COVERAGE_PATH.fullmatch(path) or not payload or len(payload) > 16384:
            raise ValueError(f"invalid coverage manifest path/size: {path}")
        try:
            data = json.loads(payload)
            slot = data["snapshot_slot_at"]
            entries = data["unlisted"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid coverage JSON: {path}") from exc
        if (data.get("schema_version") != 1 or data.get("provider") != "deribit"
                or not isinstance(slot, str)
                or not slot.startswith(path.removeprefix("deribit/coverage/")
                                       .removesuffix(".json")
                                       .replace("/", "-")[:10])
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

    def publish(self, parts: Mapping[str, bytes], *, branch: str = "market-data",
                slot_receipt: tuple[str, bytes] | None = None) -> PublishResult:
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
            if receipt_path in parts:
                raise ValueError("receipt path must not appear in raw parts")
            parts = {**parts, receipt_path: receipt_payload}
        for path, payload in parts.items():
            if not isinstance(path, str) or not (_PATH.fullmatch(path) or _EVENT_KEY_PATH.fullmatch(path)
                                                  or _SLOT_PATH.fullmatch(path)
                                                  or _COVERAGE_PATH.fullmatch(path)):
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
            elif not payload.startswith(b"PAR1") or not payload.endswith(b"PAR1"):
                raise ValueError(f"not a Parquet payload: {path}")
        if not any(path.endswith(".parquet") for path in parts):
            raise ValueError("event markers require a Parquet part in the same batch")

        names = tuple(sorted(parts))
        ref = self._api("GET", "/git/ref/heads/market-data", missing_ok=True)
        if ref is None:
            # The branch must be created as a zero-parent root, not from main.
            blobs = self._blobs(parts)
            tree = self._api("POST", "/git/trees", payload={"tree": blobs})
            commit = self._api("POST", "/git/commits", payload={
                "message": "archive: initial Deribit market-data batch",
                "tree": tree["sha"], "parents": [],
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
            if not all(path in paths for path in names):
                raise PublishConflict("UTC slot receipt exists but requested archive paths are absent")
            # Receipt is an atomic commit marker. The same config and paths
            # represent an already published slot even if a later capture
            # produces different Parquet bytes and timestamps.
            return PublishResult(tip, False, names)
        matches = [path for path in names if paths.get(path) == git_blob_sha(parts[path])]
        if len(matches) == len(names):
            return PublishResult(tip, False, names)
        if any(path in paths for path in names):
            raise PublishConflict("slot contains partial or conflicting immutable archive paths")

        blobs = self._blobs(parts)
        tree = self._api("POST", "/git/trees", payload={
            "base_tree": base_tree, "tree": blobs,
        })
        next_commit = self._api("POST", "/git/commits", payload={
            "message": "archive: Deribit UTC batch " + names[0].split("/")[-1],
            "tree": tree["sha"], "parents": [tip],
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

    def _blobs(self, parts: Mapping[str, bytes]) -> list[dict]:
        result = []
        for path in sorted(parts):
            blob = self._api("POST", "/git/blobs", payload={
                "content": base64.b64encode(parts[path]).decode("ascii"),
                "encoding": "base64",
            })
            if blob["sha"] != git_blob_sha(parts[path]):
                raise RuntimeError(f"GitHub blob checksum mismatch: {path}")
            result.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        return result
