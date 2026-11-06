"""Offline Git Data API contract tests: no GitHub writes or live credentials."""
import hashlib
import json

import pytest

from nasset.deribit_publish import GitHubArchivePublisher, PublishConflict, git_blob_sha, slot_manifest

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
    def __init__(self, *, existing=False, paths=None, truncated=False, fail_patch=False,
                 blobs=None):
        self.headers = {}
        self.calls = []
        self.existing = existing
        self.paths = paths or {}
        self.blobs = blobs or {}
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
            tree_sha = path.split("/")[-1].split("?")[0]
            recursive = "?recursive=1" in path
            # Simulate Git Data API with direct directory trees and bounded
            # recursive responses relative to the requested year subtree.
            prefix = "" if tree_sha == "tree0" else tree_sha.removeprefix("dir:").replace("|", "/")
            children = {}
            for full_path, sha in self.paths.items():
                if not full_path.startswith(prefix):
                    continue
                remaining = full_path[len(prefix):]
                first, sep, _ = remaining.partition("/")
                if recursive:
                    children[remaining] = {"path": remaining, "type": "blob", "sha": sha}
                elif sep:
                    children[first] = {"path": first, "type": "tree",
                                       "sha": "dir:" + (prefix + first + "/").replace("/", "|")}
                else:
                    children[first] = {"path": first, "type": "blob", "sha": sha}
            return Response(data={"truncated": self.truncated, "tree": list(children.values())})
        if method == "GET" and path.startswith("/git/blobs/"):
            import base64
            blob = self.blobs.get(path.removeprefix("/git/blobs/"))
            if blob is None:
                return Response(404)
            return Response(data={"encoding": "base64",
                                  "content": base64.b64encode(blob).decode()})
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
    annual = [path for _, path, _ in session.calls if "?recursive=1" in path]
    assert len(annual) == 1
    assert all(method == "GET" for method, _, _ in session.calls)


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
    assert len([path for method, path, _ in session.calls if path == "/git/blobs"]) == 1
    tree = next(data for _, path, data in session.calls if path == "/git/trees")
    marker_entry = next(item for item in tree["tree"] if item["path"] == key)
    assert marker_entry["content"] == blob.decode("utf-8")
    assert "sha" not in marker_entry
    sha = git_blob_sha(blob)
    session = FakeSession(existing=True, paths={key: sha})
    assert publisher(session).existing((key,)) == {key: sha}



SLOT_RECEIPT = (
    "deribit/slots/2026/11/05/0400.receipt",
    b'deribit-slot-v1\n{"datasets":["option_chain","index_price"],"slot":"2026-11-05T04:00:00Z","required_paths":["deribit/index_price/2026/11/05/0400.parquet","deribit/option_chain/BTC/2026/11/05/0400.parquet"]}',
)


def published_slot_session(raw=None, receipt=SLOT_RECEIPT, *, missing=()):
    """Emulate an existing Git slot with its versioned immutable manifest."""
    all_parts = {**(PARTS if raw is None else raw), receipt[0]: receipt[1]}
    manifest_path, manifest_payload = slot_manifest(all_parts, receipt[0])
    all_parts[manifest_path] = manifest_payload
    return FakeSession(
        existing=True,
        paths={p: git_blob_sha(data) for p, data in all_parts.items() if p not in missing},
        blobs={git_blob_sha(manifest_payload): manifest_payload},
    )


def test_slot_receipt_is_atomic_with_multiple_parquet_parts():
    session = FakeSession()
    result = publisher(session).publish(PARTS, slot_receipt=SLOT_RECEIPT)
    assert result.created
    tree = next(body for method, path, body in session.calls if path == "/git/trees")
    assert set(item["path"] for item in tree["tree"]) == {
        *PARTS, SLOT_RECEIPT[0], SLOT_RECEIPT[0].replace(".receipt", ".manifest.json"),
    }
    assert len([x for x in session.calls if x[1] == "/git/blobs"]) == 2
    assert next(x for x in tree["tree"] if x["path"] == SLOT_RECEIPT[0])["content"] == SLOT_RECEIPT[1].decode("utf-8")
    commit = next(body for _, path, body in session.calls if path == "/git/commits")
    assert commit["parents"] == []


def test_slot_receipt_does_not_require_stable_capture_timestamp_on_retry():
    session = published_slot_session()
    later = {P1: b"PAR1different-dataPAR1", P2: b"PAR1later-capturePAR1"}
    result = publisher(session).publish(later, slot_receipt=SLOT_RECEIPT)
    assert not result.created
    assert result.commit_sha == "tip"
    assert all(method == "GET" for method, _, _ in session.calls)


def test_completed_slot_is_metadata_only_and_rejects_new_config():
    previous = {SLOT_RECEIPT[0]: git_blob_sha(SLOT_RECEIPT[1])}
    session = FakeSession(existing=True, paths=previous)
    archive = publisher(session)
    with pytest.raises(PublishConflict, match="artifact manifest"):
        archive.completed_slot(SLOT_RECEIPT)
    session = published_slot_session()
    archive = publisher(session)
    assert archive.completed_slot(SLOT_RECEIPT) is True
    assert all(method == "GET" for method, _, _ in session.calls)
    with pytest.raises(PublishConflict, match="different due config"):
        archive.completed_slot((SLOT_RECEIPT[0], SLOT_RECEIPT[1].replace(b'"datasets":["option_chain","index_price"]', b'"datasets":["index_price"]')))
    missing = FakeSession()
    assert publisher(missing).completed_slot(SLOT_RECEIPT) is False


def test_receipt_with_missing_parquet_path_aborts_instead_of_claiming_success():
    session = published_slot_session(missing=(P2,))
    with pytest.raises(PublishConflict, match="artifact missing/changed"):
        publisher(session).publish(PARTS, slot_receipt=SLOT_RECEIPT)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_changed_receipt_rejects_publishing_into_existing_slot():
    session = FakeSession(existing=True, paths={
        P1: git_blob_sha(PARTS[P1]),
        P2: git_blob_sha(PARTS[P2]),
        SLOT_RECEIPT[0]: "old-due-config",
    })
    with pytest.raises(PublishConflict, match="different configuration"):
        publisher(session).publish(PARTS, slot_receipt=SLOT_RECEIPT)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_slot_receipt_rejects_mixed_hours_or_invalid_path():
    bad = {**PARTS, "deribit/index_price/2026/11/05/0500.parquet": b"PAR1otherPAR1"}
    session = FakeSession()
    with pytest.raises(ValueError, match="UTC slot"):
        publisher(session).publish(bad, slot_receipt=SLOT_RECEIPT)
    with pytest.raises(ValueError, match="receipt path"):
        publisher(session).publish(PARTS, slot_receipt=("../wrong.receipt", SLOT_RECEIPT[1]))
    assert session.calls == []


def test_receipt_ref_race_is_not_forced_or_retried_blindly():
    session = FakeSession(existing=True, fail_patch=True)
    with pytest.raises(PublishConflict):
        publisher(session).publish(PARTS, slot_receipt=SLOT_RECEIPT)
    patches = [payload for method, path, payload in session.calls
               if method == "PATCH" and path == "/git/refs/heads/market-data"]
    assert len(patches) == 1 and patches[0]["force"] is False


COVERAGE_PATH = "deribit/coverage/2026/11/05/0400.json"
COVERAGE_BODY = {
    "schema_version": 1,
    "provider": "deribit",
    "snapshot_slot_at": "2026-11-05T04:00:00+00:00",
    "observed_at": "2026-11-05T04:37:03+00:00",
    "unlisted": [{"dataset": "option_chain", "underlying": "PAXG", "listed_count": 0}],
}
COVERAGE_PAYLOAD = (json.dumps(COVERAGE_BODY, sort_keys=True) + "\n").encode()


def test_unlisted_coverage_manifest_is_committed_atomically_with_parquet():
    session = FakeSession()
    result = publisher(session).publish(
        {**PARTS, COVERAGE_PATH: COVERAGE_PAYLOAD},
        slot_receipt=SLOT_RECEIPT,
    )
    assert result.created
    tree = next(data for _, path, data in session.calls if path == "/git/trees")
    assert {entry["path"] for entry in tree["tree"]} == {
        *PARTS, COVERAGE_PATH, SLOT_RECEIPT[0],
        SLOT_RECEIPT[0].replace(".receipt", ".manifest.json"),
    }
    assert sum(path == "/git/commits" for _, path, _ in session.calls) == 1


@pytest.mark.parametrize("mutate", [
    lambda body: {**body, "snapshot_slot_at": "2026-11-05T05:00:00+00:00"},
    lambda body: {**body, "unlisted": [{"dataset": "option_chain",
                                       "underlying": "PAXG", "listed_count": 1}]},
    lambda body: {**body, "unlisted": []},
    lambda body: ["not-an-object"],
])
def test_invalid_coverage_manifest_is_rejected_before_git_mutation(mutate):
    session = FakeSession()
    data = json.dumps(mutate(COVERAGE_BODY)).encode()
    with pytest.raises(ValueError, match="coverage|unlisted"):
        publisher(session).publish({**PARTS, COVERAGE_PATH: data},
                                   slot_receipt=SLOT_RECEIPT)
    assert session.calls == []


def test_coverage_from_other_hour_rejected_by_slot_receipt():
    session = FakeSession()
    wrong_path = COVERAGE_PATH.replace("0400", "0500")
    with pytest.raises(ValueError, match="coverage manifest"):
        publisher(session).publish(
            {P1: PARTS[P1], wrong_path: COVERAGE_PAYLOAD},
            slot_receipt=SLOT_RECEIPT,
        )
    assert session.calls == []


def test_large_history_batch_uses_inline_tree_objects():
    """Event import does not emit one Git Blob API call per historical event."""
    from nasset.deribit_publish import TREE_BATCH_SIZE

    markers = {
        f"deribit/event_keys/historical_volatility/BTC/2023/11/14/{1700000000000 + i}.key":
        EVENT_BLOB for i in range(2 * TREE_BATCH_SIZE + 1)
    }
    session = FakeSession()
    result = publisher(session).publish({P1: PARTS[P1], **markers})
    assert result.created and result.commit_sha == "newcommit"
    trees = [data for method, path, data in session.calls
             if method == "POST" and path == "/git/trees"]
    assert [len(data["tree"]) for data in trees] == [TREE_BATCH_SIZE, TREE_BATCH_SIZE, 2]
    assert "base_tree" not in trees[0]
    assert [t["base_tree"] for t in trees[1:]] == ["tree1", "tree1"]
    assert all("content" in e and "sha" not in e for t in trees for e in t["tree"]
               if e["path"].endswith(".key"))
    assert sum(method == "POST" and path == "/git/blobs"
               for method, path, _ in session.calls) == 1
    assert sum(method == "POST" and path == "/git/commits"
               for method, path, _ in session.calls) == 1
    assert sum(method == "POST" and path == "/git/refs"
               for method, path, _ in session.calls) == 1


def test_candidate_tree_failure_never_publishes_archive_ref():
    from nasset.deribit_publish import TREE_BATCH_SIZE

    class RejectSecondTree(FakeSession):
        def request(self, method, url, json=None, timeout=None):
            if method == "POST" and url.endswith("/git/trees"):
                prior = sum(action == "POST" and path == "/git/trees"
                            for action, path, _ in self.calls)
                if prior == 1:
                    self.calls.append((method, "/git/trees", json))
                    return Response(409)
            return super().request(method, url, json=json, timeout=timeout)

    markers = {
        f"deribit/event_keys/historical_volatility/BTC/2023/11/14/{1700000000000 + i}.key":
        EVENT_BLOB for i in range(TREE_BATCH_SIZE + 1)
    }
    session = RejectSecondTree(existing=True)
    with pytest.raises(PublishConflict):
        publisher(session).publish({P1: PARTS[P1], **markers})
    assert not any(method == "PATCH" for method, _, _ in session.calls)
    assert not any(method == "POST" and path == "/git/commits"
                   for method, path, _ in session.calls)


def test_history_lookup_scales_by_year_not_event_count():
    # Two years, many individual native event keys. Never recurse from root.
    paths = {
        f"deribit/event_keys/historical_volatility/BTC/{year}/11/14/{1700000000000 + i}.key":
        f"sha-{year}-{i}"
        for year in (2023, 2024) for i in range(420)
    }
    session = FakeSession(existing=True, paths=paths)
    found = publisher(session).existing(tuple(sorted(paths)))
    assert found == paths
    annual = [path for method, path, _ in session.calls
              if method == "GET" and "?recursive=1" in path]
    assert len(annual) == 2
    assert all("dir:" in p for p in annual)
    assert all(not p.startswith("/git/trees/tree0?recursive") for p in annual)
    # Prefix traversal is shared for both years and does not grow with rows.
    assert len([p for method, p, _ in session.calls if method == "GET"]) < 16
    assert all(method == "GET" for method, _, _ in session.calls)


def test_history_scoped_year_truncation_fails_closed():
    session = FakeSession(existing=True, paths={EVENT_PATH: "exists"}, truncated=True)
    with pytest.raises(RuntimeError, match="truncated"):
        publisher(session).existing((EVENT_PATH,))
    assert all(method == "GET" for method, _, _ in session.calls)



def test_manifest_records_exact_futures_coverage_and_history_inventory():
    futures = "deribit/futures_curve/PAXG/2026/11/05/0400.parquet"
    extra = {**PARTS, futures: b"PAR1futurePAR1",
             COVERAGE_PATH: COVERAGE_PAYLOAD, EVENT_PATH: EVENT_BLOB}
    items = {**extra, SLOT_RECEIPT[0]: SLOT_RECEIPT[1]}
    path, manifest = slot_manifest(items, SLOT_RECEIPT[0])
    record = json.loads(manifest.removeprefix(b"deribit-manifest-v1\n"))
    assert path == "deribit/slots/2026/11/05/0400.manifest.json"
    assert [row["path"] for row in record["artifacts"]] == sorted(items)
    assert {row["path"]: row["sha"] for row in record["artifacts"]} == {
        k: git_blob_sha(v) for k, v in items.items()
    }
    session = published_slot_session(raw=extra)
    assert publisher(session).completed_slot(SLOT_RECEIPT)
    assert all(method == "GET" for method, _, _ in session.calls)


@pytest.mark.parametrize("missing", [
    "deribit/futures_curve/PAXG/2026/11/05/0400.parquet",
    COVERAGE_PATH,
    EVENT_PATH,
    P1,
])
def test_manifest_preflight_fails_closed_for_each_missing_artifact(missing):
    raw = {**PARTS,
           "deribit/futures_curve/PAXG/2026/11/05/0400.parquet": b"PAR1futurePAR1",
           COVERAGE_PATH: COVERAGE_PAYLOAD, EVENT_PATH: EVENT_BLOB}
    session = published_slot_session(raw=raw, missing=(missing,))
    with pytest.raises(PublishConflict, match="artifact missing/changed"):
        publisher(session).completed_slot(SLOT_RECEIPT)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_manifest_preflight_detects_wrong_sha_without_any_publish():
    session = published_slot_session()
    session.paths[P1] = "0" * 40
    with pytest.raises(PublishConflict, match="artifact missing/changed"):
        publisher(session).completed_slot(SLOT_RECEIPT)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_manifest_preflight_rejects_corruption_and_legacy_receipt():
    session = published_slot_session()
    manifest_path = SLOT_RECEIPT[0].replace(".receipt", ".manifest.json")
    session.blobs[session.paths[manifest_path]] = b"corrupt"
    with pytest.raises(PublishConflict, match="corrupted"):
        publisher(session).completed_slot(SLOT_RECEIPT)
    missing = FakeSession(existing=True, paths={
        SLOT_RECEIPT[0]: git_blob_sha(SLOT_RECEIPT[1]),
        **{p: git_blob_sha(data) for p, data in PARTS.items()},
    })
    with pytest.raises(PublishConflict, match="without artifact manifest"):
        publisher(missing).completed_slot(SLOT_RECEIPT)


def test_manifest_replay_checks_full_original_batch_even_if_request_is_smaller():
    futures = "deribit/futures_curve/PAXG/2026/11/05/0400.parquet"
    raw = {**PARTS, futures: b"PAR1futurePAR1", COVERAGE_PATH: COVERAGE_PAYLOAD}
    session = published_slot_session(raw=raw, missing=(COVERAGE_PATH,))
    with pytest.raises(PublishConflict, match="artifact missing/changed"):
        publisher(session).publish(PARTS, slot_receipt=SLOT_RECEIPT)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_manifest_zero_optional_artifacts_is_valid_noop():
    session = published_slot_session()
    assert publisher(session).completed_slot(SLOT_RECEIPT) is True
    assert not publisher(session).publish(PARTS, slot_receipt=SLOT_RECEIPT).created
    assert all(method == "GET" for method, _, _ in session.calls)



def test_large_slot_manifest_uses_one_atomic_commit():
    """An initial multi-thousand-event backfill never commits per event."""
    markers = {
        f"deribit/event_keys/historical_volatility/BTC/2023/11/14/{1700000000000 + i}.key":
        EVENT_BLOB for i in range(2 * 250 + 1)
    }
    session = FakeSession()
    result = publisher(session).publish({**PARTS, **markers}, slot_receipt=SLOT_RECEIPT)
    trees = [body for method, path, body in session.calls
             if method == "POST" and path == "/git/trees"]
    manifest_entries = [entry for tree in trees for entry in tree["tree"]
                        if entry["path"].endswith(".manifest.json")]
    assert result.created
    assert len(trees) >= 3
    assert len(manifest_entries) == 1
    manifest = json.loads(manifest_entries[0]["content"].split("\n", 1)[1])
    assert len(manifest["artifacts"]) == len(markers) + len(PARTS) + 1
    assert sum(method == "POST" and path == "/git/commits"
               for method, path, _ in session.calls) == 1
    assert sum(method == "POST" and path == "/git/refs"
               for method, path, _ in session.calls) == 1


def test_large_manifest_preflight_uses_bounded_historical_year_tree():
    markers = {
        f"deribit/event_keys/historical_volatility/BTC/2023/11/14/{1700000000000 + i}.key":
        EVENT_BLOB for i in range(650)
    }
    session = published_slot_session(raw={**PARTS, **markers})
    assert publisher(session).completed_slot(SLOT_RECEIPT)
    tree_reads = [path for method, path, _ in session.calls
                  if method == "GET" and path.startswith("/git/trees/")]
    assert len([path for path in tree_reads if "?recursive=1" in path]) == 1
    assert not any(path.startswith("/git/trees/tree0?recursive") for path in tree_reads)
    # Directory traversal is bounded by identity/year, not event count.
    one_key = {next(iter(markers)): EVENT_BLOB}
    baseline = published_slot_session(raw={**PARTS, **one_key})
    assert publisher(baseline).completed_slot(SLOT_RECEIPT)
    baseline_reads = [p for method, p, _ in baseline.calls
                      if method == "GET" and p.startswith("/git/trees/")]
    assert len(tree_reads) == len(baseline_reads)
    assert all(method == "GET" for method, _, _ in session.calls)
