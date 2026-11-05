"""Append-only GitHub Git Data API publisher for validated Parquet parts.

No checkout or fetch of the archive branch. A single commit contains all due
datasets, and an existing slot is never overwritten or silently repaired.
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Mapping

import requests

_PATH = re.compile(r"^deribit/[a-z_]+/(?:[A-Z0-9]+/)?[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{4}\.parquet$")


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

    def publish(self, parts: Mapping[str, bytes], *, branch: str = "market-data") -> PublishResult:
        if branch != "market-data":
            raise ValueError("only market-data orphan branch is supported")
        if not parts:
            raise ValueError("cannot publish empty batch")
        for path, payload in parts.items():
            if not isinstance(path, str) or not _PATH.fullmatch(path):
                raise ValueError(f"invalid archive path: {path!r}")
            if not isinstance(payload, bytes) or not payload.startswith(b"PAR1") or not payload.endswith(b"PAR1"):
                raise ValueError(f"not a Parquet payload: {path}")

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
        # Read tree metadata only; never fetch historical Parquet blob contents.
        existing = self._api("GET", f"/git/trees/{base_tree}?recursive=1")
        if existing.get("truncated"):
            raise RuntimeError("GitHub recursive tree truncated; refusing unsafe publish")
        paths = {entry["path"]: entry["sha"] for entry in existing["tree"]
                 if entry["type"] == "blob"}
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
