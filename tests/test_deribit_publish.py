"""Offline Git Data API contract tests: no GitHub writes or live credentials."""
import hashlib
import json

import pytest

from nasset.deribit_publish import GitHubArchivePublisher, PublishConflict, git_blob_sha

P1 = "deribit/index_price/2026/11/05/0400.parquet"
P2 = "deribit/option_chain/BTC/2026/11/05/0400.parquet"
PARTS = {P1: b"PAR1indexPAR1", P2: b"PAR1optionsPAR1"}


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, *, existing=False, paths=None, truncated=False, fail_patch=False):
        self.headers = {}
        self.calls = []
        self.existing = existing
        self.paths = paths or {}
        self.truncated = truncated
        self.fail_patch = fail_patch

    def request(self, method, url, json=None, timeout=None):
        path = url.split("/repos/example/archive")[1]
        self.calls.append((method, path, json))
        if path == "/git/ref/heads/market-data":
            return Response(data={"object": {"sha": "tip"}}) if self.existing else Response(404)
        if path == "/git/commits/tip":
            return Response(data={"tree": {"sha": "tree0"}})
        if path == "/git/trees/tree0?recursive=1":
            return Response(data={"truncated": self.truncated, "tree": [
                {"path": p, "sha": sha, "type": "blob"} for p, sha in self.paths.items()
            ]})
        if path == "/git/blobs" and method == "POST":
            import base64
            return Response(data={"sha": git_blob_sha(base64.b64decode(json["content"]))})
        if path == "/git/trees" and method == "POST":
            return Response(data={"sha": "tree1"})
        if path == "/git/commits" and method == "POST":
            return Response(data={"sha": "newcommit"})
        if path == "/git/refs" and method == "POST":
            return Response(data={"ref": "refs/heads/market-data"})
        if path == "/git/refs/heads/market-data" and method == "PATCH":
            return Response(409) if self.fail_patch else Response(data={"ref": "ok"})
        raise AssertionError((method, path))


def publisher(session):
    return GitHubArchivePublisher("example/archive", "fake-token", session=session)


def test_canonical_git_blob_sha():
    data = b"PAR1helloPAR1"
    assert git_blob_sha(data) == hashlib.sha1(
        f"blob {len(data)}".encode() + bytes([0]) + data
    ).hexdigest()


def test_initial_commit_is_orphan_and_atomic():
    session = FakeSession()
    result = publisher(session).publish(PARTS)
    assert result.created and result.commit_sha == "newcommit"
    commit = next(body for method, path, body in session.calls if path == "/git/commits")
    assert commit["parents"] == []
    assert sum(path == "/git/refs" for _, path, _ in session.calls) == 1
    assert len([1 for method, path, _ in session.calls if path == "/git/blobs"]) == 2


def test_existing_commit_preserves_parent_and_nonforced_ref():
    session = FakeSession(existing=True)
    result = publisher(session).publish(PARTS)
    assert result.created
    commit = next(body for _, path, body in session.calls if path == "/git/commits")
    assert commit["parents"] == ["tip"]
    tree = next(body for _, path, body in session.calls if path == "/git/trees")
    assert tree["base_tree"] == "tree0"
    patch = next(body for _, path, body in session.calls if path == "/git/refs/heads/market-data")
    assert patch["force"] is False


def test_idempotent_complete_retry_does_not_write():
    session = FakeSession(existing=True, paths={p: git_blob_sha(data) for p, data in PARTS.items()})
    result = publisher(session).publish(PARTS)
    assert not result.created and result.commit_sha == "tip"
    assert not any(method != "GET" for method, _, _ in session.calls)


@pytest.mark.parametrize("paths", [
    {P1: git_blob_sha(PARTS[P1])},
    {P1: "different", P2: git_blob_sha(PARTS[P2])},
])
def test_partial_or_conflicting_slot_is_not_repaired(paths):
    session = FakeSession(existing=True, paths=paths)
    with pytest.raises(PublishConflict):
        publisher(session).publish(PARTS)
    assert not any(method != "GET" for method, _, _ in session.calls)


def test_ref_race_is_reported_and_never_forced():
    session = FakeSession(existing=True, fail_patch=True)
    with pytest.raises(PublishConflict):
        publisher(session).publish(PARTS)


def test_truncated_tree_refused():
    session = FakeSession(existing=True, truncated=True)
    with pytest.raises(RuntimeError, match="truncated"):
        publisher(session).publish(PARTS)
    assert not any(method != "GET" for method, _, _ in session.calls)


@pytest.mark.parametrize("parts", [
    {}, {"../evil.parquet": b"PAR1okPAR1"},
    {P1: b"not parquet"}, {P1: b"PAR1missing footer"},
])
def test_invalid_batch_refused_before_api(parts):
    session = FakeSession()
    with pytest.raises(ValueError):
        publisher(session).publish(parts)
    assert session.calls == []
