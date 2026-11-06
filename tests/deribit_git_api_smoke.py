"""Disposable live Git Data API integration test; never writes production market-data."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from nasset.deribit_archive import load_config
from nasset.deribit_publish import GitHubArchivePublisher, PublishConflict
from nasset.deribit_slot import SLOT_PREFIX, slot_receipt


class ScratchPublisher(GitHubArchivePublisher):
    """Keep the publisher code intact, redirecting only the archive Git ref."""

    def __init__(self, repo, token, scratch, *, session):
        if not re.fullmatch(r"deribit-smoke/[0-9]+-[0-9]+", scratch):
            raise ValueError("invalid disposable ref")
        super().__init__(repo, token, session=session)
        self.scratch = scratch

    def _api(self, method, path, *, payload=None, missing_ok=False):
        if path in ("/git/ref/heads/market-data", "/git/refs/heads/market-data"):
            path = path.replace("market-data", self.scratch)
        if method == "POST" and path == "/git/refs":
            if payload is None or payload.get("ref") != "refs/heads/market-data":
                raise ValueError("unexpected smoke ref")
            payload = {**payload, "ref": f"refs/heads/{self.scratch}"}
        return super()._api(method, path, payload=payload, missing_ok=missing_ok)


class RacingPublisher(ScratchPublisher):
    """Simulate a concurrent writer at the precise non-forced PATCH boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.competitor = None

    def _api(self, method, path, *, payload=None, missing_ok=False):
        if method == "PATCH" and path == "/git/refs/heads/market-data" and not self.competitor:
            ref = super()._api("GET", "/git/ref/heads/market-data")
            tip = ref["object"]["sha"]
            old = super()._api("GET", f"/git/commits/{tip}")
            competing = super()._api("POST", "/git/commits", payload={
                "message": "smoke: competing writer",
                "tree": old["tree"]["sha"], "parents": [tip],
            })
            self.competitor = competing["sha"]
            super()._api("PATCH", "/git/refs/heads/market-data", payload={
                "sha": self.competitor, "force": False,
            })
        return super()._api(method, path, payload=payload, missing_ok=missing_ok)


def parquet(price):
    output = BytesIO()
    pq.write_table(pa.table({
        "index_name": ["btc_usd"],
        "index_price": pa.array([price], type=pa.float64()),
    }), output, compression="zstd", compression_level=6)
    return output.getvalue()


def make_parts(config, when, price):
    receipt = slot_receipt(config, when)
    contract = json.loads(receipt[1][len(SLOT_PREFIX):])
    parts = {path: parquet(price) for path in contract["required_paths"]}
    stamp = when.strftime("%Y/%m/%d/%H00")
    parts[f"deribit/futures_curve/PAXG/{stamp}.parquet"] = parquet(price)
    parts[f"deribit/coverage/{stamp}.json"] = (
        json.dumps({
            "schema_version": 1, "provider": "deribit",
            "snapshot_slot_at": when.replace(minute=0, second=0, microsecond=0).isoformat(),
            "observed_at": when.isoformat(),
            "unlisted": [{"dataset": "futures_curve", "underlying": "TEST", "listed_count": 0}],
        }, sort_keys=True) + "\n"
    ).encode()
    parts["deribit/event_keys/historical_volatility/BTC/2023/11/14/1700000000000.key"] = (
        b'deribit-event-v1\n{"dataset":"historical_volatility",'
        b'"identity":"BTC","timestamp":1700000000000,"value":50.0}'
    )
    return receipt, parts


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    scratch = f"deribit-smoke/{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    if repo != "BestNathan/nasset" or not re.fullmatch(r"deribit-smoke/[0-9]+-[0-9]+", scratch):
        raise RuntimeError("refusing untrusted repository or ref")
    session = requests.Session()
    normal = GitHubArchivePublisher(repo, token, session=session)
    pub = ScratchPublisher(repo, token, scratch, session=session)
    before = normal._api("GET", "/git/ref/heads/market-data", missing_ok=True)
    if pub._api("GET", "/git/ref/heads/market-data", missing_ok=True) is not None:
        raise RuntimeError("disposable ref already exists")
    config = load_config("config/market-data.yml")
    at = datetime(2026, 11, 6, 2, 37, tzinfo=timezone.utc)
    try:
        receipt, parts = make_parts(config, at, 1.0)
        first = pub.publish(parts, slot_receipt=receipt)
        assert first.created
        root = pub._api("GET", f"/git/commits/{first.commit_sha}")
        assert root["parents"] == [], "orphan must have zero Git parents"
        assert pub.completed_slot(receipt)
        changed = dict(parts)
        changed[next(path for path in parts if path.endswith(".parquet"))] = parquet(999.0)
        replay = pub.publish(changed, slot_receipt=receipt)
        assert not replay.created and replay.commit_sha == first.commit_sha

        next_receipt, next_parts = make_parts(config, at + timedelta(hours=1), 2.0)
        # The previous native event key is immutable and already published;
        # the next incremental poll must not resubmit the historical marker.
        next_parts = {p: data for p, data in next_parts.items() if not p.endswith(".key")}
        racer = RacingPublisher(repo, token, scratch, session=session)
        try:
            racer.publish(next_parts, slot_receipt=next_receipt)
            raise AssertionError("concurrent nonforced ref update succeeded")
        except PublishConflict as exc:
            assert "422" in str(exc) or "409" in str(exc), str(exc)
        assert racer.competitor is not None
        tip = pub._api("GET", "/git/ref/heads/market-data")
        assert tip["object"]["sha"] == racer.competitor
        assert pub.completed_slot(receipt)
        assert not pub.completed_slot(next_receipt)
        assert normal._api("GET", "/git/ref/heads/market-data", missing_ok=True) == before
        print(json.dumps({
            "status": "passed", "scratch": scratch,
            "initial_commit": first.commit_sha, "orphan_parent_count": 0,
            "artifact_count": len(first.paths), "replay_noop": True,
            "concurrent_nonforced_update_rejected": True,
            "production_ref_unchanged": True,
        }, sort_keys=True))
    finally:
        # Cleanup must run after both passing and failing assertions.
        endpoint = f"https://api.github.com/repos/{repo}/git/refs/heads/{scratch}"
        response = session.delete(endpoint, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }, timeout=30)
        if response.status_code not in (204, 404):
            raise RuntimeError(
                f"SCRATCH REF CLEANUP FAILED: {scratch}, HTTP {response.status_code}"
            )
        if pub._api("GET", "/git/ref/heads/market-data", missing_ok=True) is not None:
            raise RuntimeError(f"SCRATCH REF STILL EXISTS: {scratch}")
        if normal._api("GET", "/git/ref/heads/market-data", missing_ok=True) != before:
            raise RuntimeError("PRODUCTION ARCHIVE REF CHANGED")
        print(json.dumps({"scratch_ref_deleted": scratch}, sort_keys=True))


if __name__ == "__main__":
    main()
