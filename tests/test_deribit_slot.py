"""Offline UTC slot receipt and Git race/idempotence contracts."""
from datetime import datetime, timezone, timedelta

import pytest

from nasset.deribit_slot import slot_receipt
from nasset.deribit_publish import PublishConflict, git_blob_sha
from test_deribit_history import config
from test_deribit_publish import publisher, FakeSession, PARTS, P1, P2, published_slot_session


NOW = datetime(2026, 11, 5, 4, 37, tzinfo=timezone.utc)


def test_receipt_stable_within_hour_and_changes_next_hour():
    cfg = config("historical_volatility", "delivery_price")
    a = slot_receipt(cfg, NOW)
    b = slot_receipt(cfg, NOW.replace(minute=59))
    c = slot_receipt(cfg, NOW + timedelta(hours=1))
    assert a == b
    assert a != c
    assert a[0] == "deribit/slots/2026/11/05/0400.receipt"


def test_receipt_is_validated_before_network_access():
    session = FakeSession()
    with pytest.raises(ValueError, match="receipt"):
        publisher(session).publish(PARTS, slot_receipt=("bad", b"bad"))
    assert session.calls == []


def test_slot_receipt_published_atomically_with_parquet():
    receipt = slot_receipt(config("historical_volatility"), NOW)
    session = FakeSession(existing=True)
    result = publisher(session).publish(PARTS, slot_receipt=receipt)
    assert result.created
    tree = next(body for _, path, body in session.calls if path == "/git/trees")
    assert set(item["path"] for item in tree["tree"]) == {
        *PARTS, receipt[0], receipt[0].replace(".receipt", ".manifest.json"),
    }
    assert len([1 for method, path, _ in session.calls if path == "/git/commits"]) == 1


def test_preflight_complete_slot_is_read_only():
    receipt = slot_receipt(config("historical_volatility"), NOW)
    session = published_slot_session(receipt=receipt)
    assert publisher(session).completed_slot(receipt)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_preflight_rejects_changed_slot_contract():
    receipt = slot_receipt(config("historical_volatility"), NOW)
    session = FakeSession(existing=True, paths={receipt[0]: "other"})
    with pytest.raises(PublishConflict, match="different due config"):
        publisher(session).completed_slot(receipt)


def test_publish_retry_does_not_overwrite_changed_parquet_bytes():
    receipt = slot_receipt(config("historical_volatility"), NOW)
    session = published_slot_session(receipt=receipt)
    changed = {**PARTS, P1: b"PAR1different-capturePAR1"}
    result = publisher(session).publish(changed, slot_receipt=receipt)
    assert not result.created
    assert all(method == "GET" for method, _, _ in session.calls)


def test_receipt_present_with_missing_snapshot_is_conflict():
    receipt = slot_receipt(config("historical_volatility"), NOW)
    session = published_slot_session(receipt=receipt, missing=(P2,))
    with pytest.raises(PublishConflict, match="artifact missing/changed"):
        publisher(session).publish(PARTS, slot_receipt=receipt)
    assert all(method == "GET" for method, _, _ in session.calls)


def test_slot_receipt_rejects_mismatched_parquet_hour():
    receipt = slot_receipt(config("historical_volatility"), NOW + timedelta(hours=1))
    session = FakeSession()
    with pytest.raises(ValueError, match="UTC slot"):
        publisher(session).publish(PARTS, slot_receipt=receipt)
    assert session.calls == []
