"""Offline end-to-end daily historical event ingestion and Git identity tests."""
from datetime import datetime, timezone
from io import BytesIO

import pyarrow.parquet as pq
import pytest

from nasset.deribit_archive import parse_config
from nasset.deribit_client import DeribitClient, DeribitError
from nasset.deribit_history import event_marker, unseen_events
from nasset.deribit_pipeline import prepare_live_batch
from nasset.deribit_publish import PublishConflict, git_blob_sha
from test_deribit_client import fixture_transport


def config(*names):
    datasets = {
        name: {"enabled": True, "interval_hours": 1,
               **({"index_names": "auto"} if name in {"index_price", "delivery_price"} else {}),
               **({"underlyings": ["BTC", "ETH"]} if name == "historical_volatility" else {})}
        for name in names
    }
    return parse_config({
        "version": 1,
        "storage": {"branch": "market-data", "format": "parquet",
                    "compression": "zstd", "compression_level": 6},
        "sources": {"deribit": {"enabled": True,
                                "underlyings": ["BTC", "ETH", "PAXG"],
                                "datasets": datasets}},
    })


# Live client timestamps use wall-clock UTC; fixture slots must use the same hour.
NOW = datetime.now(timezone.utc).replace(minute=37, second=0, microsecond=0)
OLD = 1700000000000
LATE = 1699900000000


def transport(method, params):
    if method == "get_historical_volatility":
        return [[OLD, 50.0]]
    if method == "get_delivery_prices":
        return {"records_total": 1, "data": [
            {"date": "2023-11-14", "delivery_price": 100.0}
        ]}
    return fixture_transport(method, params)


def test_unseen_events_replay_and_value_revision():
    row = {"base_currency": "BTC", "event_timestamp": OLD, "volatility": 51.0}
    marker, value = event_marker("historical_volatility", "BTC", row, observed_at=NOW)
    assert "/2023/11/14/" in marker
    assert marker.endswith(f"/{OLD}.key")
    assert marker.startswith("deribit/event_keys/historical_volatility/BTC/")
    events, markers = unseen_events("historical_volatility", "BTC", [row],
                                   observed_at=NOW, existing=lambda paths: {})
    assert events == [row] and markers == {marker: value}
    stored = {marker: git_blob_sha(value)}
    events, markers = unseen_events("historical_volatility", "BTC", [row],
                                   observed_at=NOW, existing=lambda paths: stored)
    assert events == [] and markers == {}
    with pytest.raises(PublishConflict, match="changed"):
        unseen_events("historical_volatility", "BTC",
                      [{**row, "volatility": 52.0}], observed_at=NOW,
                      existing=lambda paths: stored)


def test_incremental_delivery_and_hv_combine_with_snapshot_in_one_batch():
    batch = prepare_live_batch(
        config("option_chain", "index_price", "futures_curve",
               "historical_volatility", "delivery_price"),
        DeribitClient(transport=transport), NOW, existing=lambda paths: {},
    )
    # 3 option, 2 futures, 1 index, 2 HV, 1 delivery = 9 Parquet
    parquet_parts = {k: v for k, v in batch.parts.items() if k.endswith(".parquet")}
    markers = {k: v for k, v in batch.parts.items() if k.endswith(".key")}
    assert len(parquet_parts) == 9
    assert len(markers) == 6  # 2 HV + 4 index-delivery events
    for payload in parquet_parts.values():
        assert payload[:4] == payload[-4:] == b"PAR1"
    for path, blob in markers.items():
        assert path.startswith("deribit/event_keys/") and blob.startswith(b"deribit-event-v1\n")
    hv = pq.read_table(BytesIO(next(v for k, v in parquet_parts.items()
                                    if "/historical_volatility/BTC/" in k)))
    assert hv.column("event_timestamp").to_pylist() == [OLD]
    settled = pq.read_table(BytesIO(next(v for k, v in parquet_parts.items()
                                         if "/delivery_price/" in k)))
    assert settled.num_rows == 4
    assert settled.column("event_date").to_pylist() == ["2023-11-14"] * 4
    assert "event_timestamp" not in settled.column_names
    assert set(settled.column("index_name").to_pylist()) == {
        "btc_usd", "btc_usdc", "eth_usd", "paxg_usdc"
    }


def test_daily_event_only_replay_is_empty_without_a_commit():
    cfg = config("historical_volatility", "delivery_price")
    first = prepare_live_batch(cfg, DeribitClient(transport=transport), NOW,
                               existing=lambda paths: {})
    stored = {path: git_blob_sha(blob) for path, blob in first.parts.items()
              if path.endswith(".key")}
    second = prepare_live_batch(cfg, DeribitClient(transport=transport), NOW,
                                existing=lambda paths: {p: stored[p] for p in paths if p in stored})
    assert second.parts == {}
    assert second.datasets == ("historical_volatility", "delivery_price")


def test_late_source_event_is_archived_in_poll_day_without_rewriting_history():
    original = {"base_currency": "ETH", "event_timestamp": OLD, "volatility": 50.0}
    marker, blob = event_marker("historical_volatility", "ETH", original, observed_at=NOW)
    previous = {marker: git_blob_sha(blob)}

    def late_transport(method, params):
        if method == "get_historical_volatility":
            return [[LATE, 45.0], [OLD, 50.0]]
        return transport(method, params)

    batch = prepare_live_batch(config("historical_volatility"),
                               DeribitClient(transport=late_transport), NOW,
                               existing=lambda paths: {p: previous[p] for p in paths if p in previous})
    assert len([p for p in batch.parts if p.endswith(".parquet")]) == 2
    payload = next(v for k, v in batch.parts.items() if "/historical_volatility/ETH/" in k)
    table = pq.read_table(BytesIO(payload))
    assert table.column("event_timestamp").to_pylist() == [LATE]


def test_delivery_only_needs_no_book_summary_or_index_price():
    calls = []
    def one(method, params):
        calls.append(method)
        return transport(method, params)
    batch = prepare_live_batch(config("delivery_price"), DeribitClient(transport=one),
                               NOW, existing=lambda paths: {})
    assert len([p for p in batch.parts if p.endswith(".parquet")]) == 1
    assert "get_instruments" in calls and "get_delivery_prices" in calls
    assert "get_book_summary_by_currency" not in calls
    assert "get_index_price" not in calls


def test_bad_history_response_aborts_without_publishing_any_batch():
    def broken(method, params):
        if method == "get_delivery_prices":
            raise DeribitError("API unavailable")
        return transport(method, params)

    with pytest.raises(DeribitError, match="API unavailable"):
        prepare_live_batch(config("option_chain", "delivery_price"),
                           DeribitClient(transport=broken), NOW,
                           existing=lambda paths: {})


def test_history_requires_event_tree_lookup_before_any_network_request():
    calls = []
    with pytest.raises(ValueError, match="event-key lookup"):
        prepare_live_batch(config("delivery_price"), DeribitClient(
            transport=lambda method, params: calls.append(method)), NOW)
    assert calls == []


def test_future_dated_and_invalid_identity_are_rejected():
    row = {"base_currency": "ETH",
           "event_timestamp": int(NOW.timestamp() * 1000) + 3600000,
           "volatility": 40.0}
    with pytest.raises(ValueError, match="later than"):
        event_marker("historical_volatility", "ETH", row, observed_at=NOW)
    row["event_timestamp"] = OLD
    with pytest.raises(ValueError, match="identity"):
        event_marker("delivery_price", "../bad", row, observed_at=NOW)


def test_settlement_marker_preserves_native_day_and_rejects_future():
    row = {"index_name": "btc_usd", "event_date": "2023-11-14",
           "delivery_price": 42000.0}
    path, payload = event_marker("delivery_price", "btc_usd", row, observed_at=NOW)
    assert path == (
        "deribit/event_keys/delivery_price/btc_usd/2023/11/14/2023-11-14.key"
    )
    assert b'"date":"2023-11-14"' in payload
    assert b'"timestamp":' not in payload
    with pytest.raises(ValueError, match="future-dated"):
        event_marker("delivery_price", "btc_usd",
                     {**row, "event_date": "2099-01-01"}, observed_at=NOW)
