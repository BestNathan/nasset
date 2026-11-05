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
    assert all_sources["option_chain"].underlyings == ("BTC", "ETH")
    assert all_sources["index_price"].underlyings == ("BTC", "ETH", "PAXG")
    assert all_sources["delivery_price"].underlyings == ("BTC", "ETH", "PAXG")
    assert all_sources["futures_curve"].underlyings == ("BTC", "ETH", "PAXG")
    assert "allow_unlisted" not in all_sources["option_chain"].options
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


@pytest.mark.parametrize("allow", [
    ["DOGE"], ["BTC", "BTC"], "PAXG", [True], ["paxg"],
])
def test_invalid_unlisted_allowlist_rejected_before_network(allow):
    import yaml
    raw = yaml.safe_load(CONFIG.read_text())
    raw["sources"]["deribit"]["datasets"]["option_chain"]["allow_unlisted"] = allow
    with pytest.raises(ValueError, match="allow_unlisted"):
        parse_config(raw)


def test_unlisted_policy_cannot_be_attached_to_index_price():
    import yaml
    raw = yaml.safe_load(CONFIG.read_text())
    raw["sources"]["deribit"]["datasets"]["index_price"]["allow_unlisted"] = ["PAXG"]
    with pytest.raises(ValueError, match="only for listed derivative"):
        parse_config(raw)


def test_hourly_archive_workflow_is_explicitly_publish_gated():
    import yaml

    workflow = CONFIG.parents[1] / ".github" / "workflows" / "deribit-archive.yml"
    # BaseLoader retains the literal 'on' key under YAML 1.1 rather than
    # interpreting it as boolean True.
    root = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
    assert root["on"]["schedule"] == [{"cron": "37 * * * *"}]
    assert root["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] == "false"
    assert root["permissions"]["contents"] == "read"
    jobs = root["jobs"]
    assert set(jobs) == {"read-only-acceptance", "publish"}
    read_only = jobs["read-only-acceptance"]
    writer = jobs["publish"]
    assert "DERIBIT_ARCHIVE_ENABLED" in read_only["if"]
    assert "DERIBIT_ARCHIVE_ENABLED" in writer["if"]
    assert "inputs.publish == true" in writer["if"]
    assert writer["permissions"]["contents"] == "write"
    assert writer["needs"] == "read-only-acceptance"
    for name, job in jobs.items():
        checkouts = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        assert len(checkouts) == 1, name
        assert checkouts[0]["with"] == {
            "ref": "${{ github.sha }}", "fetch-depth": "1", "persist-credentials": "false",
        }
    assert "github.ref == 'refs/heads/main'" in writer["if"]
    assert read_only["outputs"]["slot"] == "${{ steps.slot.outputs.slot }}"
    assert any(step.get("id") == "slot" and "date -u" in step.get("run", "")
               for step in read_only["steps"])
    assert '--at "${{ steps.slot.outputs.slot }}"' in " ".join(
        step.get("run", "") for step in read_only["steps"]
    )
    assert '--at "${{ needs.read-only-acceptance.outputs.slot }}"' in " ".join(
        step.get("run", "") for step in writer["steps"]
    )
    assert "--publish" not in " ".join(
        step.get("run", "") for step in read_only["steps"]
    )
    assert "--publish" in " ".join(
        step.get("run", "") for step in writer["steps"]
    )
