"""Deterministic no-network tests for the Deribit REST identity/coverage adapter."""
from __future__ import annotations

import pytest

from nasset.deribit_client import DeribitClient, DeribitError


def fixture_transport(method, params):
    if method == "get_instruments":
        kind = params["kind"]
        if kind == "option":
            return [
                dict(instrument_name="BTC-OPT-A", kind=kind, base_currency="BTC",
                     settlement_currency="BTC", price_index="btc_usd"),
                dict(instrument_name="BTC-OPT-B", kind=kind, base_currency="BTC",
                     settlement_currency="USDC", price_index="btc_usdc"),
                dict(instrument_name="ETH-OPT", kind=kind, base_currency="ETH",
                     settlement_currency="ETH", price_index="eth_usd"),
                dict(instrument_name="PAXG-OPT", kind=kind, base_currency="PAXG",
                     settlement_currency="USDC", price_index="paxg_usdc"),
            ]
        if kind == "future":
            return [
                dict(instrument_name="BTC-FUT", kind=kind, base_currency="BTC",
                     settlement_currency="BTC", price_index="btc_usd"),
                dict(instrument_name="PAXG-FUT", kind=kind, base_currency="PAXG",
                     settlement_currency="USDC", price_index="paxg_usdc"),
            ]
    if method == "get_book_summary_by_currency":
        names = {"option": {"BTC": ["BTC-OPT-A"], "USDC": ["BTC-OPT-B", "PAXG-OPT"],
                            "ETH": ["ETH-OPT"]},
                 "future": {"BTC": ["BTC-FUT"], "USDC": ["PAXG-FUT"]}}
        return [dict(instrument_name=n, mark_price=1.25) for n in
                names[params["kind"]][params["currency"]]]
    if method == "get_index_price":
        return {"index_price": 100.0, "estimated_delivery_price": 99.9}
    raise AssertionError((method, params))


def test_multi_settlement_and_index_variants():
    calls = []
    def transport(method, params):
        calls.append((method, params))
        return fixture_transport(method, params)
    snapshot = DeribitClient(transport=transport).snapshot(("BTC", "ETH", "PAXG"))
    assert {x["instrument_name"] for x in snapshot.options} == {
        "BTC-OPT-A", "BTC-OPT-B", "ETH-OPT", "PAXG-OPT"}
    assert {x["instrument_name"] for x in snapshot.futures} == {"BTC-FUT", "PAXG-FUT"}
    assert {x["index_name"] for x in snapshot.indices} == {
        "btc_usd", "btc_usdc", "eth_usd", "paxg_usdc"}
    assert len([c for c in calls if c[0] == "get_book_summary_by_currency"]) == 5
    assert all(x["settlement_currency"] for x in snapshot.options)



def test_independent_quote_scopes_preserve_paxg_future_and_index():
    """An index/future PAXG asset never broadens the BTC/ETH option chain."""
    calls = []

    def no_paxg_option_quote(method, params):
        calls.append((method, dict(params)))
        rows = fixture_transport(method, params)
        if method == "get_book_summary_by_currency" and params["kind"] == "option":
            return [row for row in rows if row["instrument_name"] != "PAXG-OPT"]
        return rows

    snapshot = DeribitClient(transport=no_paxg_option_quote).snapshot(
        ("BTC", "ETH", "PAXG"),
        option_underlyings=("BTC", "ETH"),
        futures_underlyings=("BTC", "ETH", "PAXG"),
    )
    assert {r["instrument_name"] for r in snapshot.options} == {
        "BTC-OPT-A", "BTC-OPT-B", "ETH-OPT"
    }
    assert {r["instrument_name"] for r in snapshot.futures} == {
        "BTC-FUT", "PAXG-FUT"
    }
    assert "paxg_usdc" in {r["index_name"] for r in snapshot.indices}
    assert "PAXG-OPT" in {r["instrument_name"] for r in snapshot.instruments}
    assert all(r["base_currency"] != "PAXG" for r in snapshot.options)
    assert any(method == "get_book_summary_by_currency"
               and params["kind"] == "future" and params["currency"] == "USDC"
               for method, params in calls)


def test_scoped_option_quotes_still_fail_closed_for_btc_missing_summary():
    def incomplete(method, params):
        rows = fixture_transport(method, params)
        if method == "get_book_summary_by_currency" and params["kind"] == "option":
            return [r for r in rows if r["instrument_name"] != "BTC-OPT-B"]
        return rows

    with pytest.raises(DeribitError, match="missing book summaries"):
        DeribitClient(transport=incomplete).snapshot(
            ("BTC", "ETH", "PAXG"),
            option_underlyings=("BTC", "ETH"),
            futures_underlyings=("BTC", "ETH", "PAXG"),
        )


def test_quote_scope_cannot_reference_unrequested_asset():
    with pytest.raises(ValueError, match="quote scope"):
        DeribitClient(transport=fixture_transport).snapshot(
            ("BTC", "ETH"), option_underlyings=("BTC", "ETH", "PAXG"),
        )


def test_missing_summary_fails_whole_batch():
    def missing(method, params):
        if method == "get_book_summary_by_currency" and params["currency"] == "USDC":
            return []
        return fixture_transport(method, params)
    with pytest.raises(DeribitError, match="missing book summaries"):
        DeribitClient(transport=missing).snapshot(("BTC", "ETH", "PAXG"))


def test_no_listed_asset_fails_explicitly():
    with pytest.raises(DeribitError, match="DOGE"):
        DeribitClient(transport=fixture_transport).snapshot(("BTC", "DOGE"))


def test_missing_settlement_currency_fails_cleanly():
    def malformed(method, params):
        if method == "get_instruments" and params["kind"] == "option":
            data = fixture_transport(method, params)
            data[0].pop("settlement_currency")
            return data
        return fixture_transport(method, params)
    with pytest.raises(DeribitError, match="missing settlement"):
        DeribitClient(transport=malformed).snapshot(("BTC", "ETH", "PAXG"))


def test_exchange_null_and_index_failure():
    def null(method, params):
        return None
    with pytest.raises(DeribitError, match="null result"):
        DeribitClient(transport=null).instruments()

    def invalid_index(method, params):
        if method == "get_index_price":
            return {"index_price": None}
        return fixture_transport(method, params)
    with pytest.raises(DeribitError, match="invalid result"):
        DeribitClient(transport=invalid_index).snapshot(("BTC", "ETH", "PAXG"))


def test_conflicting_duplicate_quote_fails_closed():
    def conflict(method, params):
        rows = fixture_transport(method, params)
        if method == "get_book_summary_by_currency" and params["kind"] == "option" and params["currency"] == "USDC":
            return rows + [dict(instrument_name="PAXG-OPT", mark_price=999)]
        return rows

    with pytest.raises(DeribitError, match="conflicting book summaries"):
        DeribitClient(transport=conflict).snapshot(("BTC", "ETH", "PAXG"))


def test_options_only_skips_future_endpoints():
    calls = []

    def transport(method, params):
        calls.append((method, params))
        return fixture_transport(method, params)

    snapshot = DeribitClient(transport=transport).snapshot(
        ("BTC", "ETH", "PAXG"), include_futures=False, include_indices=False
    )
    assert len(snapshot.options) == 4
    assert not snapshot.futures and not snapshot.indices
    assert all(params.get("kind") != "future" for _, params in calls)


def test_http_retryable_status_then_success(monkeypatch):
    import requests

    class Response:
        def __init__(self, status, body):
            self.status_code, self.body = status, body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError("status")

        def json(self):
            return self.body

    class Session:
        def __init__(self):
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return (Response(429, {}) if self.calls == 1
                    else Response(200, {"result": {"index_price": 1}}))

    monkeypatch.setattr("nasset.deribit_client.time.sleep", lambda _: None)
    session = Session()
    assert DeribitClient(session=session).call("get_index_price", index_name="btc_usd") == {"index_price": 1}
    assert session.calls == 2


def test_http_exchange_error_rejected():
    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"error": {"code": 10001, "message": "invalid index"}}

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(DeribitError, match="exchange error"):
        DeribitClient(session=Session()).call("get_index_price", index_name="invalid")


@pytest.mark.parametrize("payload,reason", [
    ({"data": [[3600000, 10, 12, 9, 11]], "continuation": 123}, "truncated"),
    ({"data": [[3600000, 10, 12, 9]]}, "invalid OHLC"),
    ({"data": [[3600000, 10, 12, 9, 11], [3600000, 10, 12, 9, 11]]}, "duplicate"),
    ({"data": [[7200000, 10, 12, 9, 11]]}, "outside closed interval"),
    ({"data": []}, "no candles"),
    ({"unexpected": []}, "malformed"),
    ({"data": [[3600000, 10, float("nan"), 9, 11]]}, "invalid OHLC values"),
    ({"data": [[3600000, 10, float("inf"), 9, 11]]}, "invalid OHLC values"),
    ({"data": [[3600000, 10, 8, 9, 11]]}, "invalid OHLC values"),
    ({"data": [[3600000, 10, 12, 11, 11]]}, "invalid OHLC values"),
    ({"data": [[3600000, -1, 12, 0, 11]]}, "invalid OHLC values"),
])
def test_dvol_rejects_invalid_or_incomplete_candles(payload, reason):
    client = DeribitClient(transport=lambda method, params: payload)
    with pytest.raises(DeribitError, match=reason):
        client.volatility_candles("BTC", start_ms=3600000, end_ms=7200000)


def test_dvol_normalizes_and_sorts_native_ohlc():
    data = {"data": [[5400000, 10, 13, 9, 12], [3600000, 8, 10, 7, 9]]}
    client = DeribitClient(transport=lambda method, params: data)
    candles = client.volatility_candles("ETH", start_ms=3600000, end_ms=7200000)
    assert [x["event_timestamp"] for x in candles] == [3600000, 5400000]
    assert candles[1]["close"] == 12
    assert all(x["base_currency"] == "ETH" for x in candles)


def test_historical_volatility_preserves_native_events_and_deduplicates():
    def transport(method, params):
        assert method == "get_historical_volatility"
        assert params == {"currency": "BTC"}
        return [[1700003600000, 52.5], [1700000000000, 49.0],
                [1700003600000, 52.5]]
    client = DeribitClient(transport=transport)
    assert client.historical_volatility("BTC") == (
        {"base_currency": "BTC", "event_timestamp": 1700000000000, "volatility": 49.0},
        {"base_currency": "BTC", "event_timestamp": 1700003600000, "volatility": 52.5},
    )


@pytest.mark.parametrize("payload", [
    {"data": []}, [[123, 10], [123, 11]], [[123, None]],
    [[123, float("nan")]], [[123, -1]], [[True, 12]], [[0, 10]],
])
def test_historical_volatility_rejects_malformed_or_conflicting_data(payload):
    client = DeribitClient(transport=lambda method, params: payload)
    with pytest.raises(DeribitError):
        client.historical_volatility("ETH")


def test_historical_volatility_does_not_query_unsupported_paxg():
    calls = []
    client = DeribitClient(transport=lambda method, params: calls.append(method))
    with pytest.raises(ValueError, match="unsupported"):
        client.historical_volatility("PAXG")
    assert calls == []


def test_historical_volatility_empty_series_is_not_a_synthetic_zero():
    client = DeribitClient(transport=lambda method, params: [])
    assert client.historical_volatility("ETH") == ()


def test_delivery_prices_paginates_and_preserves_native_event_identity():
    calls = []
    events = [
        {"date": "2023-11-14", "delivery_price": 41000.5},
        {"date": "2023-11-15", "delivery_price": 42000.25},
        {"date": "2023-11-16", "delivery_price": 43000.0},
    ]

    def transport(method, params):
        assert method == "get_delivery_prices"
        assert params["index_name"] == "btc_usdc"
        calls.append(params["offset"])
        offset, count = params["offset"], params["count"]
        return {"data": events[offset:offset + count], "records_total": len(events)}

    rows = DeribitClient(transport=transport).delivery_prices("btc_usdc", page_size=2)
    assert calls == [0, 2]
    assert [(row["event_date"], row["delivery_price"]) for row in rows] == [
        (item["date"], item["delivery_price"]) for item in events
    ]
    assert all(row["index_name"] == "btc_usdc" for row in rows)


def test_delivery_prices_empty_series_is_healthy():
    client = DeribitClient(transport=lambda method, params:
                           {"data": [], "records_total": 0})
    assert client.delivery_prices("paxg_usdc") == ()


@pytest.mark.parametrize("payload, reason", [
    ({"data": [], "records_total": 1}, "incomplete page"),
    ({"data": [{"date": "2023-11-14", "delivery_price": float("nan")}],
      "records_total": 1}, "invalid event"),
    ({"data": [{"date": True, "delivery_price": 2}],
      "records_total": 1}, "invalid event"),
    ({"data": [{"date": "2023-11-14", "delivery_price": None}],
      "records_total": 1}, "invalid event"),
    ({"data": [{"date": "2023-11-14", "delivery_price": -2}],
      "records_total": 1}, "invalid event"),
    ({"data": [], "records_total": "0"}, "malformed pagination"),
    ({"data": [{"date": "2023-02-30", "delivery_price": 2}],
      "records_total": 1}, "invalid date"),
    ({"data": [{"date": "2023-11-14", "delivery_price": 2},
               {"date": "2023-11-14", "delivery_price": 3}],
      "records_total": 2}, "conflicting event"),
])
def test_delivery_prices_rejects_bad_source_pages(payload, reason):
    client = DeribitClient(transport=lambda method, params: payload)
    with pytest.raises(DeribitError, match=reason):
        client.delivery_prices("btc_usd")


def test_delivery_prices_rejects_moving_pagination_and_truncation():
    def moving(method, params):
        if params["offset"] == 0:
            return {"data": [{"date": "2023-11-14", "delivery_price": 2}],
                    "records_total": 2}
        return {"data": [{"date": "2023-11-15", "delivery_price": 3}],
                "records_total": 3}
    with pytest.raises(DeribitError, match="pagination changed"):
        DeribitClient(transport=moving).delivery_prices("btc_usd", page_size=1)

    def many(method, params):
        return {"data": [{"date": f"2023-11-{14 + params['offset']:02d}",
                          "delivery_price": 2}], "records_total": 3}
    with pytest.raises(DeribitError, match="pagination limit"):
        DeribitClient(transport=many).delivery_prices(
            "btc_usd", page_size=1, max_pages=2)


def test_delivery_prices_invalid_index_never_calls_source():
    calls = []
    client = DeribitClient(transport=lambda method, params: calls.append(method))
    with pytest.raises(ValueError, match="index_name"):
        client.delivery_prices("BTC/USD")
    assert calls == []



def test_dvol_source_query_excludes_next_hour_boundary():
    seen = []
    def transport(method, params):
        assert method == "get_volatility_index_data"
        seen.append(params)
        return {"data": [[3600000, 10.0, 11.0, 9.0, 10.5]], "continuation": None}
    rows = DeribitClient(transport=transport).volatility_candles(
        "BTC", start_ms=3600000, end_ms=7200000,
    )
    assert rows[0]["event_timestamp"] == 3600000
    assert seen[0]["start_timestamp"] == 3600000
    assert seen[0]["end_timestamp"] == 7199999


def test_delivery_prices_rejects_repeated_page_without_silent_data_loss():
    """A source ignoring offset must not be mistaken for complete pagination."""
    calls = []
    def repeating(method, params):
        calls.append(params["offset"])
        return {"data": [{"date": "2023-11-14", "delivery_price": 41000.5}],
                "records_total": 2}

    with pytest.raises(DeribitError, match="duplicate event date"):
        DeribitClient(transport=repeating).delivery_prices("btc_usd", page_size=1)
    assert calls == [0, 1]


def test_delivery_prices_rejects_duplicate_date_within_page():
    client = DeribitClient(transport=lambda method, params: {
        "data": [{"date": "2023-11-14", "delivery_price": 41000.5},
                 {"date": "2023-11-14", "delivery_price": 41000.5}],
        "records_total": 2,
    })
    with pytest.raises(DeribitError, match="duplicate event date"):
        client.delivery_prices("btc_usd", page_size=2)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0, True, "100"])
def test_index_price_rejects_nonfinite_or_non_numeric_source_values(bad):
    def transport(method, params):
        if method == "get_index_price":
            return {"index_price": bad, "estimated_delivery_price": 99.9}
        return fixture_transport(method, params)
    with pytest.raises(DeribitError, match="invalid result"):
        DeribitClient(transport=transport).snapshot(("BTC",), include_futures=False)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, True, "100"])
def test_estimated_delivery_price_rejects_invalid_source_values(bad):
    def transport(method, params):
        if method == "get_index_price":
            return {"index_price": 100.0, "estimated_delivery_price": bad}
        return fixture_transport(method, params)
    with pytest.raises(DeribitError, match="invalid estimated delivery price"):
        DeribitClient(transport=transport).snapshot(("BTC",), include_futures=False)


def test_missing_estimated_delivery_price_remains_nullable():
    def transport(method, params):
        if method == "get_index_price":
            return {"index_price": 100.0}
        return fixture_transport(method, params)
    result = DeribitClient(transport=transport).snapshot(("BTC",), include_futures=False)
    assert result.indices
    assert all("estimated_delivery_price" not in row for row in result.indices)
