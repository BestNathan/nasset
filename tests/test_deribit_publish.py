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
        if method == "GET" and path.startswith("/git/trees/"):
            tree_sha = path.split("/")[-1]
            # Simulate non-recursive Git tree API: each response contains only
            # direct children. Synthetic SHA is the directory prefix.
            prefix = "" if tree_sha == "tree0" else tree_sha.removeprefix("dir:").replace("|", "/")
            children = {}
            for full_path, sha in self.paths.items():
                if not full_path.startswith(prefix):
                    continue
                remaining = full_path[len(prefix):]
                first, sep, _ = remaining.partition("/")
                if sep:
                    children[first] = {"path": first, "type": "tree",
                                       "sha": "dir:" + (prefix + first + "/").replace("/", "|")}
                else:
                    children[first] = {"path": first, "type": "blob", "sha": sha}
            return Response(data={"truncated": self.truncated, "tree": list(children.values())})
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


def test_scales_without_recursive_tree_and_reuses_shared_prefixes():
    # Many unrelated historical paths must never be traversed by a new slot.
    existing = {
        f"deribit/option_chain/BTC/2024/01/{day:02d}/0000.parquet": "old"
        for day in range(1, 32)
    }
    session = FakeSession(existing=True, paths=existing)
    result = publisher(session).publish(PARTS)
    assert result.created
    tree_gets = [path for method, path, _ in session.calls
                 if method == "GET" and path.startswith("/git/trees/")]
    assert tree_gets
    assert all("?recursive" not in path for path in tree_gets)
    assert len(tree_gets) == len(set(tree_gets))
    assert len(tree_gets) < 15


def test_missing_directory_does_not_fetch_descendants():
    session = FakeSession(existing=True, paths={})
    assert publisher(session).publish(PARTS).created
    tree_gets = [path for method, path, _ in session.calls
                 if method == "GET" and path.startswith("/git/trees/")]
    assert tree_gets == ["/git/trees/tree0"]


def test_existing_complete_retry_uses_only_nonrecursive_trees():
    session = FakeSession(existing=True, paths={p: git_blob_sha(data) for p, data in PARTS.items()})
    assert not publisher(session).publish(PARTS).created
    assert all("?recursive" not in path for _, path, _ in session.calls)


EVENT_PATH = "deribit/event_keys/historical_volatility/BTC/2023/11/14/1700000000000.key"
EVENT_BLOB = b'deribit-event-v1\n{"dataset":"historical_volatility","identity":"BTC","timestamp":1700000000000,"value":50.0}'


def test_event_lookup_is_read_only_and_checks_only_requested_tree_paths():
    sha = git_blob_sha(EVENT_BLOB)
    session = FakeSession(existing=True, paths={EVENT_PATH: sha, P1: "older"})
    result = publisher(session).existing((EVENT_PATH,))
    assert result == {EVENT_PATH: sha}
    assert all(method == "GET" for method, _, _ in session.calls)
    assert not any("?recursive" in path for _, path, _ in session.calls)


def test_event_lookup_returns_empty_before_orphan_initialization():
    session = FakeSession()
    assert publisher(session).existing((EVENT_PATH,)) == {}
    assert len(session.calls) == 1


def test_event_markers_and_parquet_commit_in_one_atomic_tree():
    session = FakeSession(existing=True)
    result = publisher(session).publish({P1: PARTS[P1], EVENT_PATH: EVENT_BLOB})
    assert result.created
    tree = next(body for _, path, body in session.calls if path == "/git/trees")
    assert len(tree["tree"]) == 2
    assert set(x["path"] for x in tree["tree"]) == {P1, EVENT_PATH}
    assert len([1 for _, path, _ in session.calls if path == "/git/commits"]) == 1


def test_event_marker_without_parquet_or_wrong_magic_is_rejected():
    for parts in (
        {EVENT_PATH: EVENT_BLOB},
        {P1: PARTS[P1], EVENT_PATH: b"wrong marker"},
        {P1: PARTS[P1], "../unsafe.key": EVENT_BLOB},
    ):
        session = FakeSession()
        with pytest.raises(ValueError):
            publisher(session).publish(parts)
        assert session.calls == []


def test_event_marker_conflict_is_not_overwritten():
    session = FakeSession(existing=True, paths={EVENT_PATH: "conflicting-blob"})
    with pytest.raises(PublishConflict, match="partial or conflicting"):
        publisher(session).publish({P1: PARTS[P1], EVENT_PATH: EVENT_BLOB})
    assert all(method == "GET" for method, _, _ in session.calls)


def test_delivery_calendar_marker_is_a_supported_immutable_path():
    key = "deribit/event_keys/delivery_price/btc_usd/2023/11/14/2023-11-14.key"
    blob = (b'deribit-event-v1\n'
            b'{"dataset":"delivery_price","date":"2023-11-14",'
            b'"identity":"btc_usd","value":42000.0}')
    session = FakeSession(existing=True)
    result = publisher(session).publish({P1: PARTS[P1], key: blob})
    assert result.created
    assert len([path for method, path, _ in session.calls if path == "/git/blobs"]) == 2
    sha = git_blob_sha(blob)
    session = FakeSession(existing=True, paths={key: sha})
    assert publisher(session).existing((key,)) == {key: sha}
