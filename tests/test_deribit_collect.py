"""Offline checks for dry-run-first Deribit collector and size metrics."""
from datetime import datetime, timezone

from nasset.deribit_client import DeribitClient
from nasset.deribit_collect import collect_once, CAPACITY_REVIEW_KIB
from nasset.deribit_publish import PublishResult, git_blob_sha
from test_deribit_history import config, transport


class Publisher:
    def __init__(self, *, stored=None, size=1234, fail_capacity=False):
        self.stored = stored or {}
        self.calls = []
        self.size = size
        self.fail_capacity = fail_capacity

    def existing(self, paths):
        return {p: self.stored[p] for p in paths if p in self.stored}

    def publish(self, parts, *, branch):
        self.calls.append((dict(parts), branch))
        return PublishResult("test-commit", True, tuple(sorted(parts)))

    def repository_size_kib(self):
        if self.fail_capacity:
            raise RuntimeError("size endpoint unavailable")
        return self.size


NOW = datetime.now(timezone.utc).replace(minute=37, second=0, microsecond=0)


def test_dry_run_reports_compressed_baselines_without_mutating_git():
    pub = Publisher()
    result = collect_once(
        config("historical_volatility", "delivery_price"),
        DeribitClient(transport=transport), pub, NOW,
    )
    assert result["status"] == "prepared"
    assert result["parquet_bytes"] > 0
    assert result["batch_bytes"] > result["parquet_bytes"]
    assert result["event_keys"] == 6
    assert result["metrics"]["historical_volatility"]["parts"] == 2
    assert result["metrics"]["historical_volatility"]["rows"] == 2
    assert result["metrics"]["delivery_price"]["rows"] == 4
    assert result["repository_size_kib"] == 1234
    assert pub.calls == []


def test_publish_batches_all_snapshots_and_markers_in_one_commit():
    pub = Publisher(size=CAPACITY_REVIEW_KIB)
    result = collect_once(config("historical_volatility", "delivery_price"),
                          DeribitClient(transport=transport), pub, NOW, publish=True)
    assert result["status"] == "created"
    assert result["commit_sha"] == "test-commit"
    assert result["capacity_review_required"]
    assert len(pub.calls) == 1
    parts, branch = pub.calls[0]
    assert branch == "market-data"
    assert sum(p.endswith(".parquet") for p in parts) == 3
    assert sum(p.endswith(".key") for p in parts) == 6


def test_empty_replay_is_noop_even_with_explicit_publish():
    original = Publisher()
    first = collect_once(config("historical_volatility", "delivery_price"),
                         DeribitClient(transport=transport), original, NOW,
                         publish=True)
    parts = original.calls[0][0]
    stored = {p: git_blob_sha(value) for p, value in parts.items() if p.endswith(".key")}
    second_pub = Publisher(stored=stored)
    second = collect_once(config("historical_volatility", "delivery_price"),
                          DeribitClient(transport=transport), second_pub, NOW,
                          publish=True)
    assert first["status"] == "created"
    assert second["status"] == "no_new_events"
    assert not second_pub.calls


def test_committed_batch_survives_unavailable_capacity_metric():
    pub = Publisher(fail_capacity=True)
    result = collect_once(config("historical_volatility"),
                          DeribitClient(transport=transport), pub, NOW, publish=True)
    assert result["status"] == "created"
    assert result["commit_sha"] == "test-commit"
    assert "repository_size_warning" in result
