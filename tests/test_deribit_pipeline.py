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
                  if name == "volatility_index" else {})}
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
    cfg = config("option_chain", "volatility_index")
    with pytest.raises(NotImplementedError, match="volatility_index"):
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
