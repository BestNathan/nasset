"""No-network contract tests for Deribit source capability and size probe."""
from datetime import datetime, timezone

import pytest

from nasset.deribit_client import DeribitClient, DeribitError
from nasset.deribit_probe import normalize_supported_indices, probe
from test_deribit_client import fixture_transport
from test_deribit_pipeline import config

NOW = datetime.now(timezone.utc).replace(minute=37, second=0, microsecond=0)
INDEXES = ["btc_usd", "btc_usdc", "eth_usd", "paxg_usdc"]


def transport(method, params):
    if method == "get_index_price_names":
        return INDEXES
    if method == "get_volatility_index_data":
        return {"data": [[params["start_timestamp"], 50.0, 52.0, 49.0, 51.0]],
                "continuation": None}
    if method == "get_historical_volatility":
        return [[1700000000000, 50.0]]
    if method == "get_delivery_prices":
        return {"data": [{"date": "2023-11-14", "delivery_price": 35000.0}],
                "records_total": 1}
    return fixture_transport(method, params)


def test_probe_is_read_only_and_measures_actual_parquet_fixture_bytes():
    observed = []
    def observed_transport(method, params):
        observed.append(method)
        return transport(method, params)

    output = probe(config("option_chain", "index_price", "futures_curve",
                          "volatility_index"),
                   DeribitClient(transport=observed_transport), NOW)
    assert output["publisher_invoked"] is False
    assert output["status"] == "ok"
    assert not output["source_failures"]
    assert output["underlyings"]["PAXG"]["options_listed"] == 1
    assert output["underlyings"]["PAXG"]["futures_listed"] == 1
    assert output["underlyings"]["BTC"]["settlement_currencies"] == ["BTC", "USDC"]
    assert output["referenced_index_names"] == sorted(INDEXES)
    assert output["compressed_parquet"]["option_chain"]["rows"] == 4
    assert output["compressed_parquet"]["futures_curve"]["rows"] == 2
    assert output["compressed_parquet"]["index_price"]["rows"] == 4
    assert output["compressed_parquet"]["volatility_index"]["rows"] == 2
    assert output["total_zstd_bytes"] > 0
    assert output["historical_volatility"]["BTC"]["observations"] == 1
    assert output["delivery_price_samples"]["paxg_usdc"]["validated_rows"] == 1
    assert observed.count("get_instruments") == 2
    assert "get_index_price_names" in observed
    assert "get_delivery_prices" in observed


@pytest.mark.parametrize("source", [
    ["btc_usd", "btc_usdc"], [{"index_name": "btc_usd"}, {"index_name": "btc_usd"}],
    {"btc_usd": True}, [], [None],
])
def test_invalid_or_incomplete_index_catalog_fails_closed(source):
    if isinstance(source, list) and source and all(
        isinstance(x, (str, dict)) for x in source
    ) and len(source) > 1 and source[0] == "btc_usd":
        # Valid partial catalogs still fail the probe because PAXG is missing.
        client = DeribitClient(transport=lambda method, params:
                               source if method == "get_index_price_names"
                               else transport(method, params))
        with pytest.raises(DeribitError, match="unsupported index"):
            probe(config("index_price"), client, NOW)
        return
    with pytest.raises(DeribitError, match="get_index_price_names"):
        normalize_supported_indices(source)


def test_index_catalog_extended_metadata_shape_is_supported():
    assert normalize_supported_indices(
        [{"index_name": "btc_usd"}, {"index_name": "paxg_usdc"}]
    ) == {"btc_usd", "paxg_usdc"}


def test_unlisted_paxg_options_are_a_capability_failure_not_synthetic_data():
    def no_paxg_options(method, params):
        result = transport(method, params)
        if method == "get_instruments" and params["kind"] == "option":
            return [row for row in result if row.get("base_currency") != "PAXG"]
        return result
    report = probe(config("option_chain", "index_price"), DeribitClient(
        transport=no_paxg_options,
    ), NOW)
    assert report["status"] == "blocked"
    assert any(g["underlying"] == "PAXG" and g["dataset"] == "option_chain"
               and g["severity"] == "blocking" for g in report["capability_gaps"])
    assert report["measured_scope"]["option_chain"] == ["BTC", "ETH"]
    assert report["compressed_parquet"]["option_chain"]["rows"] == 3


def test_source_settlement_date_must_not_be_interpreted_as_epoch():
    def invalid_delivery(method, params):
        if method == "get_delivery_prices":
            return {"data": [{"date": 1700000000000, "delivery_price": 10.0}],
                    "records_total": 1}
        return transport(method, params)
    report = probe(config("index_price"), DeribitClient(
        transport=invalid_delivery,
    ), NOW)
    assert report["status"] == "blocked"
    assert "delivery_price/btc_usd" in report["source_failures"]


def test_probe_checks_all_delivery_pages_not_only_first_page():
    calls = []
    def repeated_page(method, params):
        if method == "get_delivery_prices":
            calls.append((params["index_name"], params["offset"]))
            return {"data": [{"date": "2023-11-14", "delivery_price": 35000.0}],
                    "records_total": 2}
        return transport(method, params)
    report = probe(config("index_price"), DeribitClient(transport=repeated_page), NOW)
    assert report["status"] == "blocked"
    assert "duplicate event date" in report["source_failures"]["delivery_price/btc_usd"]
    assert ("btc_usd", 1) in calls
    assert report["publisher_invoked"] is False


def test_probe_reports_full_delivery_pagination_counts():
    calls = []
    def two_page_delivery(method, params):
        if method == "get_delivery_prices":
            calls.append(params["offset"])
            events = [{"date": "2023-11-14", "delivery_price": 35000.0},
                      {"date": "2023-11-15", "delivery_price": 35100.0}]
            return {"data": events[params["offset"]:params["offset"] + params["count"]],
                    "records_total": 2}
        return transport(method, params)
    report = probe(config("index_price"), DeribitClient(transport=two_page_delivery), NOW)
    assert report["status"] == "ok"
    assert report["delivery_price_samples"]["btc_usd"] == {
        "validated_rows": 2, "pagination_complete": True,
    }
    assert calls.count(0) == len(INDEXES)
