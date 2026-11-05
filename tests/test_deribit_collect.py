"""Offline checks for dry-run-first Deribit collector and size metrics."""
from datetime import datetime, timezone

import pytest

from nasset.deribit_client import DeribitClient
from nasset.deribit_collect import collect_once, CAPACITY_REVIEW_KIB
from nasset.deribit_publish import PublishResult, PublishConflict, git_blob_sha
from nasset.deribit_slot import slot_receipt
from test_deribit_history import config, transport


class Publisher:
    def __init__(self, *, stored=None, size=1234, fail_capacity=False, receipts=None):
        self.stored = stored or {}
        self.receipts = receipts or {}
        self.calls = []
        self.size = size
        self.fail_capacity = fail_capacity

    def existing(self, paths):
        return {p: self.stored[p] for p in paths if p in self.stored}

    def completed_slot(self, receipt):
        path, payload = receipt
        previous = self.receipts.get(path)
        if previous and previous != git_blob_sha(payload):
            raise PublishConflict("slot config changed")
        return previous is not None

    def publish(self, parts, *, branch, slot_receipt=None):
        self.calls.append((dict(parts), branch))
        assert slot_receipt is not None
        if slot_receipt:
            path, payload = slot_receipt
            self.receipts[path] = git_blob_sha(payload)
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



def test_hourly_rerun_uses_receipt_without_any_new_rest_or_git_write():
    pub = Publisher()
    config_daily = config("historical_volatility", "delivery_price")
    first = collect_once(config_daily, DeribitClient(transport=transport),
                         pub, NOW, publish=True)
    assert first["status"] == "created"
    observed = []

    def forbidden_source(method, params):
        observed.append(method)
        raise AssertionError("no REST requests allowed for completed slot")

    repeated = collect_once(
        config_daily, DeribitClient(transport=forbidden_source),
        pub, NOW.replace(minute=55), publish=True,
    )
    assert repeated["status"] == "already_published"
    assert len(pub.calls) == 1
    assert observed == []


def test_slot_receipt_rejects_changed_due_configuration_in_same_hour():
    pub = Publisher()
    collect_once(config("historical_volatility"), DeribitClient(transport=transport),
                 pub, NOW, publish=True)
    with pytest.raises(PublishConflict, match="config changed"):
        collect_once(config("delivery_price"), DeribitClient(transport=transport),
                     pub, NOW, publish=True)


def test_slot_receipt_stable_across_minute_and_dataset_order():
    same = config("historical_volatility", "delivery_price")
    other = config("delivery_price", "historical_volatility")
    assert slot_receipt(same, NOW) == slot_receipt(other, NOW.replace(minute=59))


def test_duplicate_utc_slot_skips_all_source_calls():
    pub = Publisher()
    receipt = slot_receipt(config("historical_volatility", "delivery_price"), NOW)
    pub.receipts[receipt[0]] = git_blob_sha(receipt[1])
    calls = []
    def forbidden(method, params):
        calls.append(method)
        raise AssertionError("already published slot must not query Deribit")
    result = collect_once(config("historical_volatility", "delivery_price"),
                          DeribitClient(transport=forbidden), pub, NOW, publish=True)
    assert result["status"] == "already_published"
    assert len(pub.receipts) == 1
    assert not pub.calls and not calls


def test_hourly_paxg_unlisted_coverage_is_reported_and_published_once():
    from test_deribit_pipeline import (
        _with_acknowledged_unlisted_paxg, _without_paxg_options,
    )

    pub = Publisher()
    result = collect_once(
        _with_acknowledged_unlisted_paxg("option_chain", "futures_curve", "index_price"),
        DeribitClient(transport=_without_paxg_options),
        pub, NOW, publish=True,
    )
    assert result["status"] == "created"
    assert result["unlisted"] == [
        {"dataset": "option_chain", "underlying": "PAXG", "listed_count": 0}
    ]
    assert len(result["coverage_manifests"]) == 1
    parts, branch = pub.calls[0]
    assert branch == "market-data"
    assert len(pub.calls) == 1
    assert result["coverage_manifests"][0] in parts
    assert not any("/option_chain/PAXG/" in path for path in parts)
    assert any("/futures_curve/PAXG/" in path for path in parts)
    assert result["parquet_bytes"] > 0
