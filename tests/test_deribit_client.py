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
