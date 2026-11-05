"""Offline contract tests for immutable Parquet/ZSTD Deribit parts."""
from datetime import datetime, timezone
from io import BytesIO

import pyarrow.parquet as pq
import pytest

from nasset.deribit_parquet import encode_part, part_path

SLOT = datetime(2026, 11, 5, 4, tzinfo=timezone.utc)
CAPTURE = datetime(2026, 11, 5, 4, 37, tzinfo=timezone.utc)


def test_option_roundtrip_preserves_units_nulls_and_metadata():
    rows = [
        {"instrument_name": "BTC-TEST-C", "base_currency": "BTC",
         "settlement_currency": "USDC", "strike": 70000.5,
         "bid_price": None, "mark_iv": 62.75, "open_interest": 0.0},
        {"instrument_name": "BTC-TEST-P", "base_currency": "BTC",
         "settlement_currency": "BTC", "strike": 65000.0,
         "bid_price": 0.00125, "mark_iv": None},
    ]
    payload = encode_part("option_chain", rows, slot=SLOT, captured_at=CAPTURE)
    table = pq.read_table(BytesIO(payload))
    assert table.num_rows == 2
    assert table.schema.metadata[b"schema_version"] == b"1"
    assert table.schema.metadata[b"record_count"] == b"2"
    assert table.column("bid_price").to_pylist() == [None, 0.00125]
    assert table.column("open_interest").to_pylist() == [0.0, None]
    assert table.column("captured_at").to_pylist() == [CAPTURE, CAPTURE]
    assert pq.ParquetFile(BytesIO(payload)).metadata.row_group(0).column(0).compression == "ZSTD"
    assert part_path("option_chain", SLOT, underlying="BTC") == (
        "deribit/option_chain/BTC/2026/11/05/0400.parquet"
    )


def test_index_part_groups_all_index_names():
    rows = [
        {"index_name": "btc_usd", "index_price": 70123.4},
        {"index_name": "btc_usdc", "index_price": 70121.9},
        {"index_name": "paxg_usdc", "index_price": 4111.0},
    ]
    table = pq.read_table(BytesIO(encode_part("index_price", rows, slot=SLOT, captured_at=CAPTURE)))
    assert table.column("index_name").to_pylist() == ["btc_usd", "btc_usdc", "paxg_usdc"]
    assert part_path("index_price", SLOT) == "deribit/index_price/2026/11/05/0400.parquet"


def test_native_delivery_event_time_required():
    with pytest.raises(ValueError, match="event_timestamp"):
        encode_part("delivery_price", [{"index_name": "btc_usd", "delivery_price": 70000}],
                    slot=SLOT, captured_at=CAPTURE)
    payload = encode_part(
        "delivery_price",
        [{"index_name": "btc_usd", "event_timestamp": 1793865600000, "delivery_price": 70000.0}],
        slot=SLOT, captured_at=CAPTURE,
    )
    assert pq.read_table(BytesIO(payload)).column("event_timestamp").to_pylist() == [1793865600000]


@pytest.mark.parametrize("dataset", ["option_chain", "index_price", "delivery_price",
                                      "volatility_index", "historical_volatility", "futures_curve"])
def test_empty_part_rejected(dataset):
    with pytest.raises(ValueError, match="empty"):
        encode_part(dataset, [], slot=SLOT, captured_at=CAPTURE)


def test_reject_unknown_fields_and_misaligned_slots():
    with pytest.raises(ValueError, match="unknown"):
        encode_part("index_price", [{"index_name": "btc_usd", "extra": 1}],
                    slot=SLOT, captured_at=CAPTURE)
    with pytest.raises(ValueError, match="aligned"):
        part_path("option_chain", CAPTURE, underlying="BTC")
    with pytest.raises(ValueError, match="uppercase"):
        part_path("option_chain", SLOT, underlying="../BTC")
