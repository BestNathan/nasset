"""RWA listing/category/price tests (entirely offline)."""
from datetime import datetime, timezone
from io import BytesIO
import json

import pyarrow.parquet as pq
import pytest
import yaml

from nasset.deribit_archive import parse_config
from nasset.deribit_client import DeribitClient, DeribitError
from nasset.deribit_pipeline import prepare_live_batch
from nasset.deribit_slot import slot_receipt


CATEGORIES = {
    "equity": ["NVDA"], "equity_etf": ["SPY"],
    "commodity": ["GOLD"], "pre_ipo": ["ANTH", "OPENAI"],
}


def config():
    return parse_config({
        "version": 1,
        "storage": {"branch": "market-data", "format": "parquet",
                    "compression": "zstd", "compression_level": 6},
        "sources": {"deribit": {"enabled": True, "underlyings": ["BTC", "ETH", "PAXG"],
            "datasets": {"rwa_perpetual": {
                "enabled": True, "interval_hours": 1, "categories": CATEGORIES,
            }}}},
    })


def transport(method, params):
    if method == "get_instruments":
        assert params["kind"] == "future"
        return [
            {"instrument_name": f"{symbol}_USDC-PERPETUAL", "base_currency": symbol,
             "kind": "future", "settlement_currency": "USDC",
             "contract_size": 0.01, "tick_size": 0.01, "is_active": True,
             "price_index": f"{symbol.lower()}_usd", "product_group": "RWA"}
            for symbol in ("NVDA", "SPY", "GOLD", "ANTH", "OPENAI")
        ] + [
            {"instrument_name": "SOL_USDC-PERPETUAL", "base_currency": "SOL",
             "kind": "future", "settlement_currency": "USDC", "is_active": True,
             "product_group": "SOL"},
            {"instrument_name": "NEWCO_USDC-PERPETUAL", "base_currency": "NEWCO",
             "kind": "future", "settlement_currency": "USDC", "is_active": True,
             "product_group": "RWA"},
            {"instrument_name": "NVDA_USDC-30DEC26", "base_currency": "NVDA",
             "kind": "future", "settlement_currency": "USDC", "is_active": True},
        ]
    if method == "get_book_summary_by_currency":
        assert params == {"currency": "USDC", "kind": "future"}
        return [
            {"instrument_name": f"{symbol}_USDC-PERPETUAL",
             "mark_price": 100.0 + i, "bid_price": 99.0 + i,
             "ask_price": 101.0 + i, "volume_usd": 1234.0}
            for i, symbol in enumerate(("NVDA", "SPY", "GOLD", "ANTH", "OPENAI"))
        ] + [{"instrument_name": "SOL_USDC-PERPETUAL", "mark_price": 1.0},
             {"instrument_name": "NEWCO_USDC-PERPETUAL", "mark_price": 55.0}]
    raise AssertionError((method, params))


def test_rwa_is_separate_from_crypto_and_preserves_pre_ipo_native_prices():
    now = datetime.now(timezone.utc).replace(minute=59, second=0)
    batch = prepare_live_batch(config(), DeribitClient(transport=transport), now)
    path = next(iter(batch.parts))
    assert path.startswith("deribit/rwa_perpetual/")
    table = pq.read_table(BytesIO(batch.parts[path]))
    assert table.num_rows == 6
    rows = {r["base_currency"]: r for r in table.to_pylist()}
    assert set(rows) == {"NVDA", "SPY", "GOLD", "ANTH", "OPENAI", "NEWCO"}
    assert rows["NEWCO"]["category"] == "unclassified"
    assert rows["NEWCO"]["pricing_basis"] == "unclassified_rwa_reference"
    assert rows["NEWCO"]["product_group"] == "RWA"
    assert rows["ANTH"]["category"] == "pre_ipo"
    assert rows["ANTH"]["pricing_basis"] == "pre_ipo_internal_reference"
    assert rows["ANTH"]["mark_price"] == 103.0
    assert rows["ANTH"]["contract_size"] == 0.01
    assert rows["NVDA"]["pricing_basis"] == "rwa_market_reference"
    assert rows["NVDA"]["price_index"] == "nvda_usd"
    receipt = json.loads(slot_receipt(config(), now)[1].split(b"\n", 1)[1])
    assert path in receipt["required_paths"]


def test_zero_active_listings_are_explicit_empty_parquet_not_zero_prices():
    def empty(method, params):
        if method == "get_instruments":
            return [{"instrument_name": "BTC_USDC-PERPETUAL",
                     "base_currency": "BTC", "kind": "future",
                     "settlement_currency": "USDC"}]
        raise AssertionError(method)
    now = datetime.now(timezone.utc).replace(minute=59, second=0)
    batch = prepare_live_batch(config(), DeribitClient(transport=empty), now)
    assert len(batch.parts) == 2  # empty typed Parquet + complete lifecycle baseline
    parquet = next(payload for path, payload in batch.parts.items() if path.endswith(".parquet"))
    assert pq.read_table(BytesIO(parquet)).num_rows == 0
    lifecycle = next(payload for path, payload in batch.parts.items()
                     if "/rwa_state/" in path)
    assert json.loads(lifecycle)["contracts"] == {}


def test_listed_quote_gap_aborts_whole_batch():
    def missing(method, params):
        records = transport(method, params)
        if method == "get_book_summary_by_currency":
            return [r for r in records if r["instrument_name"] != "ANTH_USDC-PERPETUAL"]
        return records
    with pytest.raises(DeribitError, match="missing book summaries"):
        prepare_live_batch(config(), DeribitClient(transport=missing), datetime.now(timezone.utc))


@pytest.mark.parametrize("issue", ["duplicate", "invalid-mark", "wrong-settlement"])
def test_rwa_source_contract_fail_closed(issue):
    def bad(method, params):
        records = transport(method, params)
        if issue == "duplicate" and method == "get_book_summary_by_currency":
            return records + [records[0]]
        if issue == "invalid-mark" and method == "get_book_summary_by_currency":
            return [{**r, "mark_price": float("nan")}
                    if r["instrument_name"] == "ANTH_USDC-PERPETUAL" else r
                    for r in records]
        if issue == "wrong-settlement" and method == "get_instruments":
            return [{**r, "settlement_currency": "BTC"}
                    if r["instrument_name"] == "ANTH_USDC-PERPETUAL" else r
                    for r in records]
        return records
    with pytest.raises(DeribitError):
        prepare_live_batch(config(), DeribitClient(transport=bad), datetime.now(timezone.utc))


@pytest.mark.parametrize("bad_categories", [
    {"crypto": ["SOL"]},
    {"equity": ["nvda"]},
    {"equity": ["NVDA"], "pre_ipo": ["NVDA"]},
    {"equity": ["BTC"]},
    {},
])
def test_rwa_registry_rejects_invalid_or_crypto_overlap(bad_categories):
    doc = {
        "version": 1,
        "storage": {"branch": "market-data", "format": "parquet",
                    "compression": "zstd", "compression_level": 6},
        "sources": {"deribit": {"enabled": True, "underlyings": ["BTC", "ETH", "PAXG"],
            "datasets": {"rwa_perpetual": {"enabled": True,
                "interval_hours": 1, "categories": bad_categories}}}},
    }
    with pytest.raises(ValueError):
        parse_config(doc)


def test_production_registry_tracks_four_exchange_categories():
    from pathlib import Path
    production = Path(__file__).resolve().parents[1] / "config" / "market-data.yml"
    cfg = yaml.safe_load(production.read_text())
    categories = cfg["sources"]["deribit"]["datasets"]["rwa_perpetual"]["categories"]
    assert set(categories) == {"equity", "equity_etf", "commodity", "pre_ipo"}
    assert {"ANTH", "OPENAI"}.issubset(set(categories["pre_ipo"]))
    assert all(len(symbols) == len(set(symbols)) for symbols in categories.values())
