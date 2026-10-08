import pandas as pd

from nasset.analytics import asset_metrics, bs_delta, option_risk_score


def test_bs_delta_has_expected_signs():
    call = bs_delta(100, 110, 30, 30, "call")
    put = bs_delta(100, 90, 30, 30, "put")
    assert 0 < call < 0.5
    assert -0.5 < put < 0


def test_asset_metrics_separates_price_and_cash():
    idx = pd.to_datetime(["2025-01-01", "2025-07-01", "2026-01-01"], utc=True)
    history = pd.DataFrame({"Close":[100.0,105.0,110.0],"Dividends":[0.0,2.0,2.0]}, index=idx)
    result = asset_metrics(history, 365)
    assert result["price_return_pct"] == 10.0
    assert result["cumulative_cash_index"] == 4.0
    assert result["total_value_index"] == 114.0
    assert result["annualized_cash_yield_pct"] > 3.9


def test_short_dte_is_penalized_at_same_inputs():
    short = option_risk_score(10, 0.1, 7, 40, 30, 1.0)
    medium = option_risk_score(10, 0.1, 30, 40, 30, 1.0)
    assert medium > short
