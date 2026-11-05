"""No-network end-to-end preparation tests: REST -> typed Parquet parts."""
from datetime import datetime, timezone
from io import BytesIO

import pyarrow.parquet as pq
import pytest

from nasset.deribit_archive import parse_config
from nasset.deribit_client import DeribitClient, DeribitError
from nasset.deribit_pipeline import prepare_live_batch
from test_deribit_client import fixture_transport


def config(*names):
    datasets = {
        name: {"enabled": True, "interval_hours": 1,
               **({"index_names": "auto"} if name == "index_price" else {}),
               **({"resolution": "3600", "underlyings": ["BTC", "ETH"]}
                  if name in {"volatility_index", "historical_volatility"} else {})}
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


def test_live_batch_projects_exchange_fields_and_partitions_assets():
    # Use a future time in the current hour so capture is not after 'now'.
    from datetime import timedelta
    now = datetime.now(timezone.utc).replace(minute=59, second=59, microsecond=999999)
    batch = prepare_live_batch(
        config("option_chain", "index_price", "futures_curve"),
        DeribitClient(transport=fixture_transport), now,
    )
    assert len(batch.parts) == 6  # 3 option assets + 2 futures assets + 1 index
    assert set(batch.datasets) == {"option_chain", "index_price", "futures_curve"}
    for path, payload in batch.parts.items():
        assert path.endswith(".parquet")
        assert payload[:4] == payload[-4:] == b"PAR1"
        table = pq.read_table(BytesIO(payload))
        assert table.num_rows > 0
        assert table.schema.metadata[b"provider"] == b"deribit"
    idx = next(v for k, v in batch.parts.items() if "/index_price/" in k)
    assert set(pq.read_table(BytesIO(idx)).column("index_name").to_pylist()) == {
        "btc_usd", "btc_usdc", "eth_usd", "paxg_usdc"
    }


def test_unimplemented_due_dataset_prevents_all_rest_calls():
    calls = []
    def transport(method, params):
        calls.append(method)
        return fixture_transport(method, params)
    cfg = config("option_chain", "historical_volatility")
    with pytest.raises(NotImplementedError, match="historical_volatility"):
        prepare_live_batch(cfg, DeribitClient(transport=transport), datetime.now(timezone.utc))
    assert not calls


def test_missing_source_rows_never_returns_partial_batch():
    def missing(method, params):
        if method == "get_book_summary_by_currency" and params.get("currency") == "USDC":
            return []
        return fixture_transport(method, params)
    now = datetime.now(timezone.utc).replace(minute=59, second=59, microsecond=999999)
    with pytest.raises(DeribitError, match="missing book summaries"):
        prepare_live_batch(config("option_chain", "index_price"),
                           DeribitClient(transport=missing), now)


def test_hourly_dvol_included_in_complete_live_batch():
    from datetime import timedelta
    now = datetime.now(timezone.utc).replace(minute=59, second=59, microsecond=999999)
    slot = now.replace(minute=0, second=0, microsecond=0)
    expected = int((slot - timedelta(hours=1)).timestamp() * 1000)
    calls = []

    def transport(method, params):
        if method == "get_volatility_index_data":
            calls.append(params)
            return {"data": [[expected, 50.0, 53.0, 48.0, 51.0]],
                    "continuation": None}
        return fixture_transport(method, params)

    batch = prepare_live_batch(
        config("option_chain", "index_price", "futures_curve", "volatility_index"),
        DeribitClient(transport=transport), now,
    )
    assert len(batch.parts) == 8
    assert {x["currency"] for x in calls} == {"BTC", "ETH"}
    assert all(x["end_timestamp"] == int(slot.timestamp() * 1000) for x in calls)
    for asset in ("BTC", "ETH"):
        payload = next(v for k, v in batch.parts.items()
                       if f"/volatility_index/{asset}/" in k)
        table = pq.read_table(BytesIO(payload))
        assert table.column("event_timestamp").to_pylist() == [expected]
        assert table.column("close").to_pylist() == [51.0]


def test_dvol_only_batch_does_not_query_instrument_catalog():
    from datetime import timedelta
    now = datetime.now(timezone.utc).replace(minute=59, second=59, microsecond=999999)
    slot = now.replace(minute=0, second=0, microsecond=0)
    expected = int((slot - timedelta(hours=1)).timestamp() * 1000)
    calls = []

    def transport(method, params):
        calls.append(method)
        assert method == "get_volatility_index_data"
        return {"data": [[expected, 10, 11, 9, 10.5]], "continuation": None}

    batch = prepare_live_batch(config("volatility_index"),
                               DeribitClient(transport=transport), now)
    assert len(batch.parts) == 2
    assert calls == ["get_volatility_index_data"] * 2


def test_dvol_missing_or_truncated_rejects_whole_batch():
    now = datetime.now(timezone.utc).replace(minute=59, second=59, microsecond=999999)

    def transport(method, params):
        if method == "get_volatility_index_data":
            return {"data": [], "continuation": None}
        return fixture_transport(method, params)

    with pytest.raises(DeribitError, match="no candles"):
        prepare_live_batch(config("option_chain", "volatility_index"),
                           DeribitClient(transport=transport), now)


def test_index_only_batch_discovers_indices_without_book_summaries():
    now = datetime.now(timezone.utc).replace(minute=59, second=59, microsecond=999999)
    calls = []

    def transport(method, params):
        calls.append(method)
        return fixture_transport(method, params)

    batch = prepare_live_batch(config("index_price"),
                               DeribitClient(transport=transport), now)
    assert len(batch.parts) == 1
    assert "get_index_price" in calls
    assert "get_book_summary_by_currency" not in calls
    table = pq.read_table(BytesIO(next(iter(batch.parts.values()))))
    assert set(table.column("index_name").to_pylist()) == {
        "btc_usd", "btc_usdc", "eth_usd", "paxg_usdc"
    }


def test_scheduler_time_is_not_a_capture_completion_deadline():
    # A live request completes after the scheduler invokes the pipeline.
    # This must not reject a valid snapshot within the same UTC hour.
    from datetime import timedelta
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    batch = prepare_live_batch(config("option_chain"),
                               DeribitClient(transport=fixture_transport), now)
    assert len(batch.parts) == 3
    assert batch.slot == now
