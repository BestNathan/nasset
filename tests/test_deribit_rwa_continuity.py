"""Offline RWA lifecycle, metadata changes, rebase and Git checkpoint tests."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pyarrow.parquet as pq
import pytest

from nasset.deribit_archive import parse_config
from nasset.deribit_client import DeribitClient, DeribitError
from nasset.deribit_pipeline import prepare_live_batch
from nasset.deribit_publish import PublishConflict, git_blob_sha, slot_manifest
from nasset.deribit_rwa_lifecycle import (
    build_state, continuous_marks, parse_state, price_basis,
    state_payload, validate_actions,
)
from nasset.deribit_slot import slot_receipt
from test_deribit_publish import FakeSession, publisher

UTC = timezone.utc
START = datetime(2026, 11, 14, 4, tzinfo=UTC)
CONVERSION = datetime(2026, 11, 16, 10, tzinfo=UTC)
ACTION = {
    "id": "anth-into-ant-20261116",
    "kind": "price_rebase",
    "from_instrument": "ANTH_USDC-PERPETUAL",
    "to_instrument": "ANT_USDC-PERPETUAL",
    "effective_at": "2026-11-16T10:00:00Z",
    "price_divisor": 13.116,
    "quantity_multiplier": 13.116,
    "source_url": "https://insights.deribit.com/exchange-updates/deribit-to-migrate-anthropic-perpetual-to-per-share-pricing/",
    "target_pricing_basis": "pre_ipo_per_share_reference",
}


def inventory(symbol, **change):
    spec = {
        "base_currency": symbol, "settlement_currency": "USDC",
        "kind": "future", "instrument_type": "linear",
        "product_group": "RWA", "price_index": symbol.lower() + "_usd",
        "contract_size": 0.001, "tick_size": 0.01,
        "min_trade_amount": 0.001, "is_active": True,
        "expiration_timestamp": None,
    }
    return {**spec, **change}


def cfg():
    return parse_config({
        "version": 1,
        "storage": {"branch": "market-data", "format": "parquet",
                    "compression": "zstd", "compression_level": 6},
        "sources": {"deribit": {"enabled": True,
            "underlyings": ["BTC", "ETH", "PAXG"], "datasets": {
                "rwa_perpetual": {
                    "enabled": True, "interval_hours": 1,
                    "categories": {"pre_ipo": ["ANTH", "ANT"],
                                   "equity": ["NVDA"]},
                    "corporate_actions": [ACTION],
                }}}},
    })


def test_lifecycle_first_observation_missing_delisting_relisting_and_spec():
    actions = validate_actions([ACTION])
    current = {
        "ANTH_USDC-PERPETUAL": inventory("ANTH"),
        "NVDA_USDC-PERPETUAL": inventory("NVDA"),
    }
    s0 = build_state(START, START + timedelta(minutes=30), current, None, actions)
    assert {x["type"] for x in s0["events"]} == {"first_observed"}
    assert len(s0["contracts"]) == 2
    assert s0["previous_slot_at"] is None

    # One missing catalog does NOT establish a true exchange delisting.
    s1 = build_state(START + timedelta(hours=1),
                     START + timedelta(hours=1, minutes=30),
                     {"NVDA_USDC-PERPETUAL": inventory("NVDA", tick_size=0.02)},
                     s0, actions)
    assert {x["type"] for x in s1["events"]} == {
        "absence_observed", "contract_spec_changed",
    }
    diff = next(x["changes"] for x in s1["events"]
                if x["type"] == "contract_spec_changed")
    assert diff["tick_size"] == {"before": 0.01, "after": 0.02}
    assert s1["contracts"]["ANTH_USDC-PERPETUAL"]["status"] == "missing_unconfirmed"
    s2 = build_state(START + timedelta(hours=2),
                     START + timedelta(hours=2, minutes=30),
                     {"NVDA_USDC-PERPETUAL": inventory("NVDA", tick_size=0.02)},
                     s1, actions)
    assert "delisting_inferred" in {x["type"] for x in s2["events"]}
    assert s2["contracts"]["ANTH_USDC-PERPETUAL"]["missing_observations"] == 2
    s3 = build_state(START + timedelta(hours=3),
                     START + timedelta(hours=3, minutes=30),
                     current, s2, actions)
    assert "relisted_observed" in {x["type"] for x in s3["events"]}
    assert s3["contracts"]["ANTH_USDC-PERPETUAL"]["first_seen_at"] == (
        s0["contracts"]["ANTH_USDC-PERPETUAL"]["first_seen_at"])
    assert s3["contracts"]["ANTH_USDC-PERPETUAL"]["missing_observations"] == 0
    assert parse_state(json.loads(state_payload(s3))) == s3


def test_explicit_inactive_reactivated_without_renaming():
    t0 = build_state(START, START, {"NVDA_USDC-PERPETUAL": inventory("NVDA")},
                     None, ())
    t1 = build_state(START + timedelta(hours=1), START + timedelta(hours=1),
                     {"NVDA_USDC-PERPETUAL": inventory("NVDA", is_active=False)},
                     t0, ())
    assert t1["contracts"]["NVDA_USDC-PERPETUAL"]["status"] == "inactive"
    assert t1["events"][0]["type"] == "inactive_observed"
    t2 = build_state(START + timedelta(hours=2), START + timedelta(hours=2),
                     {"NVDA_USDC-PERPETUAL": inventory("NVDA")},
                     t1, ())
    assert t2["events"][0]["type"] == "reactivated"


def test_reference_index_change_emits_explicit_unverified_event():
    previous = build_state(
        START, START, {"ANT_USDC-PERPETUAL": inventory("ANT")},
        None, (),
    )
    after = build_state(
        START + timedelta(hours=1), START + timedelta(hours=1),
        {"ANT_USDC-PERPETUAL": inventory("ANT", price_index="ant_live_usd")},
        previous, (),
    )
    events = [e for e in after["events"]
              if e["type"] == "pricing_reference_changed_observed"]
    assert len(events) == 1
    assert events[0]["previous_index"] == "ant_usd"
    assert events[0]["new_index"] == "ant_live_usd"
    assert events[0]["conversion_status"] == "unverified_reference_transition"
    assert after["confirmed_actions"] == []


def test_empty_catalog_after_active_snapshot_is_fail_closed():
    t0 = build_state(START, START, {"NVDA_USDC-PERPETUAL": inventory("NVDA")}, None, ())
    with pytest.raises(DeribitError, match="unexpectedly empty"):
        build_state(START + timedelta(hours=1), START + timedelta(hours=1),
                    {}, t0, ())


def test_rebase_requires_both_source_history_and_actual_replacement():
    actions = validate_actions([ACTION])
    src = {"ANTH_USDC-PERPETUAL": inventory("ANTH")}
    s0 = build_state(START, START, src, None, actions)
    # Announced action must not adjust anything until actual successor appears.
    s1 = build_state(CONVERSION + timedelta(hours=1), CONVERSION + timedelta(hours=1),
                     src, s0, actions)
    assert s1["confirmed_actions"] == []
    assert not any(e["type"] == "price_rebase_observed" for e in s1["events"])
    both = {**src, "ANT_USDC-PERPETUAL": inventory("ANT")}
    s2 = build_state(CONVERSION + timedelta(hours=2), CONVERSION + timedelta(hours=2),
                     both, s1, actions)
    assert s2["confirmed_actions"] == []

    only_new = {"ANT_USDC-PERPETUAL": inventory("ANT")}
    s3 = build_state(CONVERSION + timedelta(hours=3), CONVERSION + timedelta(hours=3),
                     only_new, s2, actions)
    assert [x["id"] for x in s3["confirmed_actions"]] == [ACTION["id"]]
    rebase = next(e for e in s3["events"] if e["type"] == "price_rebase_observed")
    assert rebase["announced_effective_at"] == ACTION["effective_at"]
    assert rebase["price_divisor"] == 13.116
    assert rebase["observed_at"] != rebase["announced_effective_at"]
    assert price_basis("ANT_USDC-PERPETUAL", "pre_ipo", actions,
                       {ACTION["id"]}) == "pre_ipo_per_share_reference"
    s4 = build_state(CONVERSION + timedelta(hours=4), CONVERSION + timedelta(hours=4),
                     only_new, s3, actions)
    assert not any(e["type"] == "price_rebase_observed" for e in s4["events"])
    assert s4["confirmed_actions"] == s3["confirmed_actions"]


def test_confirmed_conversion_action_payload_is_immutable():
    actions = validate_actions([ACTION])
    before = build_state(START, START,
                         {"ANTH_USDC-PERPETUAL": inventory("ANTH")},
                         None, actions)
    after = build_state(
        CONVERSION + timedelta(hours=1), CONVERSION + timedelta(hours=1),
        {"ANT_USDC-PERPETUAL": inventory("ANT")}, before, actions,
    )
    fixed = after["confirmed_actions"][0]["action"]
    assert fixed["price_divisor"] == 13.116
    assert fixed["source_url"].startswith("https://insights.deribit.com/")
    assert json.loads(state_payload(after))["confirmed_actions"][0]["action"] == ACTION
    altered = validate_actions([{**ACTION, "price_divisor": 2.0,
                                  "quantity_multiplier": 2.0}])
    with pytest.raises(DeribitError, match="changed or removed"):
        build_state(
            CONVERSION + timedelta(hours=2), CONVERSION + timedelta(hours=2),
            {"ANT_USDC-PERPETUAL": inventory("ANT")}, after, altered,
        )
    with pytest.raises(ValueError, match="source-pinned"):
        continuous_marks(
            [{"instrument_name": "ANTH_USDC-PERPETUAL", "mark_price": 1000.0}],
            altered, after["confirmed_actions"],
        )


def test_no_fake_rebase_from_unseen_legacy_or_price_jump():
    actions = validate_actions([ACTION])
    new = {"ANT_USDC-PERPETUAL": inventory("ANT")}
    state = build_state(CONVERSION + timedelta(hours=1),
                        CONVERSION + timedelta(hours=1), new, None, actions)
    assert state["confirmed_actions"] == []
    # Native mark jumps are deliberately not inputs to build_state.
    result = continuous_marks([
        {"instrument_name": "ANTH_USDC-PERPETUAL", "mark_price": 1300.0},
        {"instrument_name": "ANT_USDC-PERPETUAL", "mark_price": 100.0},
    ], actions, state["confirmed_actions"])
    assert result[0]["canonical_instrument"] == "ANTH_USDC-PERPETUAL"
    assert result[0]["adjusted_mark_price"] == 1300.0
    assert result[1]["canonical_instrument"] == "ANT_USDC-PERPETUAL"


def test_confirmed_continuous_marks_preserve_native_and_economic_notional():
    actions = validate_actions([ACTION])
    points = [
        {"instrument_name": "ANTH_USDC-PERPETUAL", "mark_price": 1374.86},
        {"instrument_name": "ANT_USDC-PERPETUAL", "mark_price": 104.82},
        {"instrument_name": "NVDA_USDC-PERPETUAL", "mark_price": 180.0},
    ]
    rows = continuous_marks(points, actions, [{"id": ACTION["id"],
                                                 "observed_at": CONVERSION.isoformat(),
                                                 "action": ACTION}])
    old, new, other = rows
    assert old["lineage_id"] == new["lineage_id"] == "ANTH_USDC-PERPETUAL"
    assert old["canonical_instrument"] == new["canonical_instrument"] == "ANT_USDC-PERPETUAL"
    assert old["raw_mark_price"] == 1374.86
    assert old["adjusted_mark_price"] == pytest.approx(1374.86 / 13.116)
    assert new["adjusted_mark_price"] == 104.82
    assert old["price_adjustment_divisor"] == 13.116
    assert other["adjusted_mark_price"] == 180
    assert 1 * 1374.86 == pytest.approx(13.116 * old["adjusted_mark_price"])


@pytest.mark.parametrize("bad", [
    {**ACTION, "quantity_multiplier": 10},
    {**ACTION, "price_divisor": 0},
    {**ACTION, "price_divisor": -2},
    {**ACTION, "source_url": "http://example.com"},
    {**ACTION, "from_instrument": "ANT_USDC-PERPETUAL",
     "to_instrument": "ANT_USDC-PERPETUAL"},
    {**ACTION, "effective_at": "2026-11-16T10:00:00"},
])
def test_invalid_corporate_actions_rejected_before_any_ingestion(bad):
    with pytest.raises(ValueError):
        validate_actions([bad])


def test_rwa_atomic_slot_receipt_requires_checkpoint_and_recovers_from_git():
    from test_deribit_rwa import transport
    now = START.replace(minute=37)
    batch = prepare_live_batch(cfg(), DeribitClient(transport=transport), now)
    parquet_path = next(p for p in batch.parts if p.endswith(".parquet"))
    state_path = next(p for p in batch.parts if "/rwa_state/" in p)
    assert pq.read_table(BytesIO(batch.parts[parquet_path])).num_rows > 0
    state = json.loads(batch.parts[state_path])
    assert state["previous_slot_at"] is None
    receipt = slot_receipt(cfg(), now)
    required = json.loads(receipt[1].split(b"\n", 1)[1])["required_paths"]
    assert set(required) == {parquet_path, state_path}
    session = FakeSession(existing=True)
    with pytest.raises(ValueError, match="mandatory"):
        publisher(session).publish({parquet_path: batch.parts[parquet_path]},
                                   slot_receipt=receipt)
    assert session.calls == []
    result = publisher(session).publish(batch.parts, slot_receipt=receipt)
    assert result.created
    tree = next(body for method, path, body in session.calls
                if method == "POST" and path == "/git/trees")
    assert state_path in {x["path"] for x in tree["tree"]}


def test_latest_checkpoint_is_verified_and_precedes_slot():
    older = build_state(START, START, {"ANTH_USDC-PERPETUAL": inventory("ANTH")},
                        None, ())
    newer = build_state(START + timedelta(hours=1), START + timedelta(hours=1),
                        {"ANTH_USDC-PERPETUAL": inventory("ANTH")},
                        older, ())
    one, two = state_payload(older), state_payload(newer)
    path1 = "deribit/rwa_state/2026/11/14/0400.json"
    path2 = "deribit/rwa_state/2026/11/14/0500.json"
    sha1, sha2 = git_blob_sha(one), git_blob_sha(two)
    session = FakeSession(existing=True, paths={path1: sha1, path2: sha2},
                          blobs={sha1: one, sha2: two})
    saved, tip = publisher(session).latest_rwa_state(START + timedelta(hours=1))
    assert saved["snapshot_slot_at"] == older["snapshot_slot_at"]
    assert tip == "tip"
    saved, tip = publisher(session).latest_rwa_state(START + timedelta(hours=2))
    assert saved["snapshot_slot_at"] == newer["snapshot_slot_at"]
    assert all(method == "GET" for method, _, _ in session.calls)
    assert not any("/rwa_perpetual/" in path for _, path, _ in session.calls)
    bad = FakeSession(existing=True, paths={path1: sha1},
                      blobs={sha1: b"corrupt"})
    with pytest.raises(PublishConflict, match="corrupt"):
        publisher(bad).latest_rwa_state(START + timedelta(hours=2))


def test_archive_tip_race_rejects_stale_lifecycle_event_without_writes():
    from test_deribit_rwa import transport
    now = START.replace(minute=37)
    batch = prepare_live_batch(cfg(), DeribitClient(transport=transport), now)
    session = FakeSession(existing=True)
    with pytest.raises(PublishConflict, match="tip changed"):
        publisher(session).publish(batch.parts, slot_receipt=slot_receipt(cfg(), now),
                                   expected_tip="outdated")
    assert all(method == "GET" for method, _, _ in session.calls)
