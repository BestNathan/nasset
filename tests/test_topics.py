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


def test_previous_fiscal_year_payment_preserved_for_cashflow_only():
    events = module.normalize_events([
        record(2014, "2015-07-01", 2.0),
        record(2015, "2016-07-01", 2.5),
    ])
    assert len(events) == 2
    assert module.fiscal_history(events) == {"2015": 0.25}


def test_future_ex_date_is_not_counted_as_implemented_cash():
    events = module.normalize_events([
        record(2025, "2099-12-01", 5.0),
        record(2025, "2026-07-01", 3.0),
    ])
    assert len(events) == 1
    assert module.fiscal_history(events) == {"2025": 0.3}


def test_verified_holder_class_correction_for_changjiang():
    vendor = module.normalize_events([record(2015, "2016-07-19", 1.2946)])
    corrected = module.apply_verified_corrections(vendor, "600900.SS")
    assert vendor[0]["cash_per_share_cny"] == .12946
    assert corrected[0]["cash_per_share_cny"] == .4
    assert corrected[0]["vendor_per_ten_cny"] == 1.2946
    assert corrected[0]["verified_exception"]["source_url"].startswith("https://")


def test_holder_class_correction_rejects_vendor_drift():
    altered = module.normalize_events([record(2015, "2016-07-19", 1.40)])
    with pytest.raises(ValueError, match="source has changed"):
        module.apply_verified_corrections(altered, "600900.SS")


def test_comparable_dividends_across_bonus_share_change():
    events = module.normalize_events([
        record(2015, "2016-05-01", 4.0),
        record(2018, "2019-05-01", 0, bonus=10),
        record(2025, "2026-05-01", 10.0),
    ])
    adjusted = module.split_adjusted_events(events)
    assert module.fiscal_history(adjusted)["2015"] == .4
    assert module.fiscal_history(adjusted, comparable=True)["2015"] == .2
    assert module.fiscal_history(adjusted, comparable=True)["2025"] == 1.0
    assert adjusted[0]["current_share_equivalence_factor"] == 2.0


def test_cash_reinvestment_uses_whole_lots():
    dates = pd.date_range("2025-01-02", periods=5, freq="D", tz="Asia/Shanghai")
    frame = pd.DataFrame({"Close": [10.] * 5}, index=dates)
    events = module.normalize_events([record(2024, "2025-01-03", 5.0)])
    s = module.simulate_cashflow(frame, events)
    assert s["cumulative_cash_cny"] == 50_000
    assert s["reinvested"]["current_shares_estimated"] == 105_000
    assert s["reinvested"]["leftover_cash_cny"] == 0.0
    assert s["reinvested"]["total_return_pct"] == pytest.approx(5.0)
    assert s["calendar_2025_yield_on_cost_pct"] == 5


def test_split_adjusted_yahoo_price_undoes_factor_before_board_lot_purchase():
    dates = pd.date_range("2025-01-02", periods=5, freq="D", tz="Asia/Shanghai")
    frame = pd.DataFrame({"Close": [5.] * 5}, index=dates)
    events = module.normalize_events([record(2024, "2025-01-03", 0, bonus=10)])
    result = module.simulate_cashflow(frame, events)
    assert result["initial_price_cny"] == 10.0
    assert result["initial_shares"] == 100_000
    assert result["current_shares_estimated"] == 200_000
    assert result["latest_position_value_cny"] == 1_000_000
    assert result["total_return_pct_no_reinvest"] == 0


def test_realized_risk_fixture_drawdown():
    dates=pd.date_range("2025-01-01", periods=130, freq="B")
    prices=[100.,120.,90.] + [90.] * 127
    result=module.risk_metrics(pd.DataFrame({"Adj Close":prices},index=dates))
    assert result is not None
    assert result["max_drawdown_pct"] == pytest.approx(-25.0)
    assert result["annual_volatility_pct"] > 0
    assert result["daily_cvar_95_pct"] < 0
    assert result["rolling_returns"]["10"] is None


def test_universe_rejects_non_a_share_symbols():
    universe=json.loads((ROOT/"data"/"topics-universe.json").read_text())
    mutated=[dict(x) for x in universe]
    mutated[0]["symbol"]="1398.HK"
    with pytest.raises(ValueError,match="not an A-share"):
        module.validate_universe(mutated)
