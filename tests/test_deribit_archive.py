"""No-network contract tests for the archive configuration and due-gate."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nasset.deribit_archive import load_config, parse_config

CONFIG = Path(__file__).resolve().parents[1] / "config" / "market-data.yml"


def test_hourly_scope_and_daily_incremental_sources():
    cfg = load_config(CONFIG)
    assert cfg.branch == "market-data"
    assert cfg.compression_level == 6
    all_sources = {ds.name: ds for ds in cfg.datasets}
    assert set(all_sources) == {
        "option_chain", "index_price", "delivery_price", "volatility_index",
        "historical_volatility", "futures_curve",
    }
    for name in ("option_chain", "index_price", "volatility_index", "futures_curve"):
        assert all_sources[name].interval_hours == 1
    assert all_sources["option_chain"].underlyings == ("BTC", "ETH", "PAXG")
    assert all_sources["futures_curve"].underlyings == ("BTC", "ETH", "PAXG")
    assert all_sources["volatility_index"].underlyings == ("BTC", "ETH")
    assert all_sources["historical_volatility"].underlyings == ("BTC", "ETH")


def test_due_gate_and_utc_slot():
    cfg = load_config(CONFIG)
    midnight = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert len(cfg.due(midnight)) == 6
    midday = midnight + timedelta(hours=13, minutes=37)
    assert {d.name for d in cfg.due(midday)} == {
        "option_chain", "index_price", "volatility_index", "futures_curve"
    }
    assert cfg.slot(midday) == "2026/11/05/1300"
    assert cfg.slot(midday.astimezone(timezone(timedelta(hours=-8)))) == "2026/11/05/1300"
    with pytest.raises(ValueError):
        cfg.due(datetime(2026, 11, 5, 13))


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True),
])
def test_invalid_version(field, value):
    with pytest.raises(ValueError):
        parse_config({field: value})


def test_invalid_scope_and_interval():
    import yaml

    raw = yaml.safe_load(CONFIG.read_text())
    raw["sources"]["deribit"]["underlyings"] = ["BTC", "BTC"]
    with pytest.raises(ValueError, match="duplicates"):
        parse_config(raw)
    raw["sources"]["deribit"]["underlyings"] = ["BTC", "ETH", "PAXG"]
    raw["sources"]["deribit"]["datasets"]["option_chain"]["interval_hours"] = 5
    with pytest.raises(ValueError, match="divide 24"):
        parse_config(raw)
    raw["sources"]["deribit"]["datasets"]["option_chain"]["interval_hours"] = 1
    raw["sources"]["deribit"]["datasets"]["volatility_index"]["underlyings"] = ["DOGE"]
    with pytest.raises(ValueError, match="within shared"):
        parse_config(raw)


@pytest.mark.parametrize("dataset", ["volatility_index", "historical_volatility"])
def test_volatility_datasets_reject_paxg_before_network(dataset):
    doc = {
        "version": 1,
        "storage": {"branch": "market-data", "format": "parquet",
                    "compression": "zstd", "compression_level": 6},
        "sources": {"deribit": {"enabled": True,
                                "underlyings": ["BTC", "ETH", "PAXG"],
                                "datasets": {dataset: {"enabled": True,
                                                       "interval_hours": 24,
                                                       "underlyings": ["BTC", "PAXG"],
                                                       **({"resolution": "3600"} if dataset == "volatility_index" else {})}}}},
    }
    with pytest.raises(ValueError, match="BTC/ETH only"):
        parse_config(doc)
