"""Offline regression tests for the high-dividend topic."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "topic_builder", ROOT / "scripts" / "build_topics_dividends.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(year, ex_date, per_ten, bonus=0, transfer=0):
    return {
        "REPORT_DATE": f"{year}-12-31 00:00:00",
        "EX_DIVIDEND_DATE": ex_date + " 00:00:00",
        "PRETAX_BONUS_RMB": per_ten,
        "BONUS_IT_RATIO": bonus, "IT_RATIO": transfer,
    }


def test_every_ten_and_fiscal_year_not_payment_year():
    events = module.normalize_events([
        record(2024, "2025-07-01", 3.0),
        record(2025, "2025-09-01", 1.0),
        record(2025, "2026-06-01", 2.0),
        record(2025, "2026-06-01", 2.0),
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 10},
    ])
    assert len(events) == 3
    assert module.fiscal_history(events) == {"2024": .3, "2025": .3}
    assert events[0]["fiscal_year"] == 2024
    assert events[0]["cash_per_share_cny"] == .3


def test_missing_data_not_interpreted_as_zero_or_growth():
    events = module.normalize_events([
        record(2015, "2016-04-01", 1.0),
        record(2025, "2026-03-01", 3.0),
    ])
    s = module.dividend_statistics(module.fiscal_history(events), events)
    assert s["cagr_pct"] is None
    assert s["observed_cuts"] == 0
    assert s["observed_adjacent_pairs"] == 0
    assert "2020" in s["missing_fiscal_years"]


def test_full_series_compound_growth():
    events = module.normalize_events([
        record(y, f"{y+1}-05-20", 10 * (1.04 ** (y-2015)))
        for y in range(2015, 2026)
    ])
    stat = module.dividend_statistics(module.fiscal_history(events), events)
    assert stat["full_2015_2025_coverage"]
    assert stat["observed_adjacent_pairs"] == 10
    assert stat["observed_cuts"] == 0
    assert stat["cagr_pct"] == pytest.approx(4.0, abs=.005)


def test_stock_bonus_disables_naive_cross_decade_cagr():
    events = module.normalize_events([
        record(y, f"{y+1}-04-01", 1.0, bonus=3 if y == 2020 else 0)
        for y in range(2015, 2026)
    ])
    stat = module.dividend_statistics(module.fiscal_history(events), events)
    assert stat["share_adjustment_required"]
    assert stat["cagr_pct"] is None


def test_payment_year_cashflow_and_board_lot():
    dates = pd.date_range("2025-01-02", periods=4, freq="D", tz="Asia/Shanghai")
    prices = pd.DataFrame({"Close": [10., 10.5, 11., 11.5]}, index=dates)
    events = module.normalize_events([
        record(2024, "2025-01-03", 5.0),
        record(2025, "2026-02-01", 1.0),
    ])
    result = module.simulate_cashflow(prices, events)
    assert result["initial_shares"] == 100_000
    assert result["cashflow_by_payment_year_cny"]["2025"] == 50_000
    assert result["cumulative_cash_cny"] >= 50_000
    assert result["initial_price_cny"] == 10


def test_risk_metrics_insufficient_samples():
    dates = pd.date_range("2025-01-01", periods=30, freq="B")
    data = pd.DataFrame({"Adj Close": range(100, 130)}, index=dates)
    assert module.risk_metrics(data) is None


def test_market_universe_has_all_banks_and_no_duplicates():
    universe=json.loads((ROOT / "data" / "topics-universe.json").read_text())
    module.validate_universe(universe)
    assert len(universe) == 78
    assert len({x["symbol"] for x in universe}) == len(universe)
    assert len([x for x in universe if x["sector"] == "banking"]) == 42
    assert len([x for x in universe if x["sector"] == "telecom"]) == 3
    assert {"兰州银行","苏农银行","无锡银行"} <= {
        x["name"] for x in universe
    }
