from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

TRADING_DAYS = 365.0


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(
    spot: float,
    strike: float,
    dte: float,
    iv_pct: float,
    option_type: str,
    rate: float = 0.0,
) -> float:
    """Black-Scholes spot delta proxy."""
    if spot <= 0 or strike <= 0 or dte <= 0 or iv_pct <= 0:
        return float("nan")
    t = dte / TRADING_DAYS
    sigma = iv_pct / 100.0
    d1 = (
        math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t
    ) / (sigma * math.sqrt(t))
    call_delta = normal_cdf(d1)
    if option_type.lower() == "call":
        return call_delta
    if option_type.lower() == "put":
        return call_delta - 1.0
    raise ValueError(f"unsupported option type: {option_type}")


def bs_price(
    spot: float,
    strike: float,
    dte: float,
    iv_pct: float,
    option_type: str,
    rate: float = 0.0,
) -> float:
    if spot <= 0 or strike <= 0 or dte <= 0 or iv_pct <= 0:
        return float("nan")
    t = dte / TRADING_DAYS
    sigma = iv_pct / 100.0
    root_t = math.sqrt(t)
    d1 = (
        math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t
    ) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    discount = math.exp(-rate * t)
    if option_type.lower() == "call":
        return spot * normal_cdf(d1) - strike * discount * normal_cdf(d2)
    if option_type.lower() == "put":
        return strike * discount * normal_cdf(-d2) - spot * normal_cdf(-d1)
    raise ValueError(f"unsupported option type: {option_type}")


def implied_vol_pct_from_price(
    price: float,
    spot: float,
    strike: float,
    dte: float,
    option_type: str,
    rate: float = 0.0,
) -> float:
    """Invert Black-Scholes with bisection.

    Intended as a robust quote-normalization layer for sources whose published
    IV field is missing or inconsistent. Returns NaN if no sensible solution
    exists in the 1%-300% volatility range.
    """
    if price <= 0 or spot <= 0 or strike <= 0 or dte <= 0:
        return float("nan")

    t = dte / TRADING_DAYS
    discount = math.exp(-rate * t)
    if option_type.lower() == "call":
        intrinsic = max(0.0, spot - strike * discount)
        upper_bound = spot
    elif option_type.lower() == "put":
        intrinsic = max(0.0, strike * discount - spot)
        upper_bound = strike * discount
    else:
        raise ValueError(f"unsupported option type: {option_type}")

    if price <= intrinsic or price >= upper_bound:
        return float("nan")

    low, high = 1.0, 300.0
    low_price = bs_price(spot, strike, dte, low, option_type, rate)
    high_price = bs_price(spot, strike, dte, high, option_type, rate)
    if not (low_price <= price <= high_price):
        return float("nan")

    for _ in range(80):
        mid = (low + high) / 2.0
        value = bs_price(spot, strike, dte, mid, option_type, rate)
        if abs(value - price) < 1e-6:
            return mid
        if value < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def realized_vol_pct(prices: Iterable[float]) -> float:
    arr = np.asarray(list(prices), dtype=float)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size < 3:
        return float("nan")
    log_returns = np.diff(np.log(arr))
    if log_returns.size < 2:
        return float("nan")
    return float(np.std(log_returns, ddof=1) * math.sqrt(365.0) * 100.0)


def _safe_cagr(end_multiple: float, days: float) -> float:
    if end_multiple <= 0 or days <= 0:
        return float("nan")
    return (end_multiple ** (365.0 / days) - 1.0) * 100.0


def asset_metrics(history: pd.DataFrame, window_days: int) -> dict:
    """Separate principal mark-to-market from cash distributions."""
    if history.empty or "Close" not in history:
        raise ValueError("price history is empty")

    frame = history.copy()
    frame = frame.loc[frame["Close"].notna() & (frame["Close"] > 0)]
    if frame.empty:
        raise ValueError("no valid close prices")

    end_ts = frame.index[-1]
    cutoff = end_ts - pd.Timedelta(days=window_days)
    clipped = frame.loc[frame.index >= cutoff]
    if len(clipped) < 2:
        clipped = frame

    start_ts = clipped.index[0]
    end_ts = clipped.index[-1]
    start_price = float(clipped["Close"].iloc[0])
    end_price = float(clipped["Close"].iloc[-1])
    days = max((end_ts - start_ts).total_seconds() / 86400.0, 1.0)

    dividends = 0.0
    if "Dividends" in clipped:
        dividends = float(clipped["Dividends"].fillna(0.0).sum())

    initial_value = 100.0
    units = initial_value / start_price
    current_asset_value = units * end_price
    cumulative_cash = units * dividends
    total_value = current_asset_value + cumulative_cash

    return {
        "start_date": start_ts.date().isoformat(),
        "end_date": end_ts.date().isoformat(),
        "window_days_actual": round(days, 2),
        "start_price": round(start_price, 8),
        "current_price": round(end_price, 8),
        "initial_value_index": 100.0,
        "current_asset_value_index": round(current_asset_value, 4),
        "cumulative_cash_index": round(cumulative_cash, 4),
        "total_value_index": round(total_value, 4),
        "price_return_pct": round((current_asset_value / initial_value - 1.0) * 100.0, 4),
        "annualized_price_return_pct": round(
            _safe_cagr(current_asset_value / initial_value, days), 4
        ),
        "annualized_cash_yield_pct": round(
            cumulative_cash / initial_value * 365.0 / days * 100.0, 4
        ),
        "total_return_pct": round((total_value / initial_value - 1.0) * 100.0, 4),
        "annualized_total_return_pct": round(
            _safe_cagr(total_value / initial_value, days), 4
        ),
        "realized_vol_pct": round(realized_vol_pct(clipped["Close"].tolist()), 4),
    }


def manual_income_metrics(
    history: pd.DataFrame,
    annual_cash_yield_pct: float,
    window_days: int,
    observations_per_year: float = 12.0,
) -> dict:
    """Metrics for slower manual benchmarks such as direct property.

    Cashflow is modeled as a stated annual gross yield on initial capital.
    Price observations may be monthly or quarterly, so volatility annualization
    uses the supplied observation frequency rather than daily scaling.
    """
    if history.empty or "Close" not in history:
        raise ValueError("price history is empty")

    frame = history.copy()
    frame = frame.loc[frame["Close"].notna() & (frame["Close"] > 0)]
    if len(frame) < 2:
        raise ValueError("manual benchmark needs at least two price observations")

    end_ts = frame.index[-1]
    cutoff = end_ts - pd.Timedelta(days=window_days)
    clipped = frame.loc[frame.index >= cutoff]
    if len(clipped) < 2:
        clipped = frame

    start_ts = clipped.index[0]
    end_ts = clipped.index[-1]
    start_price = float(clipped["Close"].iloc[0])
    end_price = float(clipped["Close"].iloc[-1])
    days = max((end_ts - start_ts).total_seconds() / 86400.0, 1.0)

    initial_value = 100.0
    current_asset_value = initial_value * end_price / start_price
    cumulative_cash = initial_value * (annual_cash_yield_pct / 100.0) * days / 365.0
    total_value = current_asset_value + cumulative_cash

    prices = clipped["Close"].to_numpy(dtype=float)
    log_returns = np.diff(np.log(prices))
    vol = (
        float(np.std(log_returns, ddof=1) * math.sqrt(observations_per_year) * 100.0)
        if log_returns.size >= 2
        else float("nan")
    )

    return {
        "start_date": start_ts.date().isoformat(),
        "end_date": end_ts.date().isoformat(),
        "window_days_actual": round(days, 2),
        "start_price": round(start_price, 8),
        "current_price": round(end_price, 8),
        "initial_value_index": 100.0,
        "current_asset_value_index": round(current_asset_value, 4),
        "cumulative_cash_index": round(cumulative_cash, 4),
        "total_value_index": round(total_value, 4),
        "price_return_pct": round((current_asset_value / initial_value - 1.0) * 100.0, 4),
        "annualized_price_return_pct": round(
            _safe_cagr(current_asset_value / initial_value, days), 4
        ),
        "annualized_cash_yield_pct": round(float(annual_cash_yield_pct), 4),
        "total_return_pct": round((total_value / initial_value - 1.0) * 100.0, 4),
        "annualized_total_return_pct": round(
            _safe_cagr(total_value / initial_value, days), 4
        ),
        "realized_vol_pct": round(vol, 4) if math.isfinite(vol) else None,
    }


def option_risk_score(
    annualized_yield_pct: float,
    delta_abs: float,
    dte: float,
    iv_pct: float,
    rv_pct: float,
    liquidity: float,
) -> float:
    """Relative ranking score, not a forecast of return."""
    if not math.isfinite(annualized_yield_pct) or annualized_yield_pct <= 0:
        return 0.0
    delta_abs = max(delta_abs, 0.01)
    dte = max(dte, 1.0)
    liquidity = min(max(liquidity, 0.05), 1.0)
    yield_per_delta = annualized_yield_pct / ((delta_abs * 100.0) ** 1.2)

    if dte < 21:
        dte_factor = math.sqrt(dte / 21.0)
    elif dte <= 45:
        dte_factor = 1.0
    else:
        dte_factor = math.sqrt(45.0 / dte)

    if math.isfinite(iv_pct) and math.isfinite(rv_pct) and rv_pct > 0:
        vol_edge = (iv_pct - rv_pct) / rv_pct
        vol_factor = min(max(1.0 + 0.6 * vol_edge, 0.55), 1.75)
    else:
        vol_factor = 1.0

    return yield_per_delta * dte_factor * vol_factor * (0.4 + 0.6 * liquidity)


def normalize_scores(cells: list[dict]) -> None:
    values = [float(c.get("_raw_score", 0.0)) for c in cells]
    maximum = max(values, default=0.0)
    for cell in cells:
        raw = float(cell.pop("_raw_score", 0.0))
        cell["raw_score"] = round(raw, 8)
        cell["score"] = round((raw / maximum * 100.0) if maximum > 0 else 0.0, 2)


def best_cell(cells: list[dict], key: str = "score") -> dict | None:
    valid = [c for c in cells if c.get("available") and c.get(key) is not None]
    if not valid:
        return None
    return max(valid, key=lambda c: float(c.get(key, 0.0)))
