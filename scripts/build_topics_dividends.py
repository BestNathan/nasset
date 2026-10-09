"""Verified-source dividend topic builder.

Eastmoney RPT_SHAREBONUS_DET provides fiscal report periods and actually
implemented ex-dividend events. Yahoo supplies split-adjusted total-return
price histories; these are NEVER substituted for declared cash dividends.
"""
from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "topics-universe.json"
CORRECTIONS = ROOT / "data" / "topics-dividend-corrections.json"
OUTPUT = ROOT / "site" / "data" / "topics-dividends.json"
START_YEAR, END_YEAR = 2015, 2025
NOTIONAL = 1_000_000
EASTMONEY = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0 nasset dividend research (github.com/BestNathan/nasset)"}


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def date_part(value):
    if not value:
        return None
    value = str(value)[:10]
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def eastmoney_history(symbol, session=None):
    """Paginate the actual implemented event table; reject truncated responses."""
    session = session or requests.Session()
    code = symbol.split(".")[0]
    rows = []
    page = 1
    pages_expected = None
    while True:
        response = session.get(
            EASTMONEY,
            params={
                "reportName": "RPT_SHAREBONUS_DET",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": page,
                "pageSize": 100,
                "sortColumns": "EX_DIVIDEND_DATE",
                "sortTypes": -1,
            },
            headers=HEADERS,
            timeout=22,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Eastmoney response has no result: " + str(payload.get("message", "")))
        batch = result.get("data") or []
        if not isinstance(batch, list):
            raise ValueError("invalid Eastmoney data list")
        if pages_expected is None:
            pages_expected = int(result.get("pages") or 1)
            if pages_expected > 100:
                raise ValueError("implausible pagination")
        rows.extend(batch)
        if page >= pages_expected:
            break
        page += 1
    return rows


def normalize_events(raw_rows):
    """Dedupe actual ex-dates; preserve report-year and per-10-share units."""
    result = []
    seen = set()
    for row in raw_rows:
        report_date = date_part(row.get("REPORT_DATE"))
        ex_date = date_part(row.get("EX_DIVIDEND_DATE"))
        per_ten = number(row.get("PRETAX_BONUS_RMB"))
        if not report_date or not ex_date or ex_date > date.today().isoformat() or per_ten is None or per_ten < 0:
            continue  # proposed distributions are NOT realized cash
        fy = int(report_date[:4])
        if fy < START_YEAR - 1 or fy > date.today().year:
            continue
        key = (fy, ex_date, round(per_ten, 8))
        if key in seen:
            continue
        seen.add(key)
        bonus = number(row.get("BONUS_IT_RATIO")) or 0
        transfer = number(row.get("IT_RATIO")) or 0
        if bonus < 0 or transfer < 0 or bonus + transfer > 100:
            raise ValueError("invalid share bonus/transfer ratio")
        plan = str(row.get("IMPL_PLAN_PROFILE") or "")
        result.append({
            "fiscal_year": fy,
            "ex_date": ex_date,
            "per_ten_cny": round(per_ten, 8),
            "cash_per_share_cny": round(per_ten / 10, 9),
            "bonus_per_ten": bonus,
            "transfer_per_ten": transfer,
            "plan": plan[:180],
            "possible_special_dividend": "特别" in plan or "特殊" in plan,
            "source": "Eastmoney/RPT_SHAREBONUS_DET",
        })
    return sorted(result, key=lambda row: (row["ex_date"], row["fiscal_year"]))



def apply_verified_corrections(events, symbol, corrections=None):
    """Correct documented holder-class dividends for representative retail A shares.

    Each correction asserts the upstream number and ex date; vendor drift must
    trigger an exception, not silently corrupt a long-term CAGR.
    """
    if corrections is None:
        corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))
    revised = [dict(event) for event in events]
    for rule in corrections.get(symbol, []):
        matches = [e for e in revised if e["fiscal_year"] == rule["fiscal_year"]
                   and e["ex_date"] == rule["ex_date"]]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one source event for correction: {symbol} {rule['ex_date']}")
        event = matches[0]
        before = event["per_ten_cny"]
        if abs(before - rule["upstream_per_ten_cny"]) > 0.00001:
            raise ValueError(f"Dividend source has changed; reverify {symbol} {rule['ex_date']}")
        event["vendor_per_ten_cny"] = before
        event["per_ten_cny"] = float(rule["retail_a_share_per_ten_cny"])
        event["cash_per_share_cny"] = round(event["per_ten_cny"] / 10, 9)
        event["verified_exception"] = {
            "source_url": rule["source_url"],
            "reason": rule["reason"],
            "basis": "public/retail A-share holder; not universal for all shareholder classes",
        }
    return revised


def split_adjusted_events(events):
    """Normalize pre-split cash to the latest equivalent share count.

    Bonus/transfer ratios are explicitly reported per 10 shares. For several
    events on one ex-date, apply that date's share action once.
    """
    result = [dict(e) for e in events]
    dates = sorted({e["ex_date"] for e in result}, reverse=True)
    factor = 1.0
    for ex_date in dates:
        same_day = [e for e in result if e["ex_date"] == ex_date]
        ratios = {(e["bonus_per_ten"], e["transfer_per_ten"]) for e in same_day
                  if e["bonus_per_ten"] or e["transfer_per_ten"]}
        if len(ratios) > 1:
            raise ValueError(f"conflicting corporate actions for {ex_date}")
        if ratios:
            bonus, transfer = next(iter(ratios))
            factor *= 1 + (bonus + transfer) / 10
        for event in same_day:
            event["comparable_cash_per_share_cny"] = round(
                event["cash_per_share_cny"] / factor, 9)
            event["current_share_equivalence_factor"] = round(factor, 8)
    return result


def fiscal_history(events, *, comparable=False):
    years = {}
    field = "comparable_cash_per_share_cny" if comparable else "cash_per_share_cny"
    for e in events:
        if not START_YEAR <= e["fiscal_year"] <= END_YEAR:
            continue
        key = str(e["fiscal_year"])
        years[key] = years.get(key, 0) + e[field]
    return {k: round(v, 8) for k, v in sorted(years.items())}


def dividend_statistics(years, events, *, comparable=False):
    """Compute complete-decade CAGR only from comparable annual share units."""
    points = [(int(k), float(v)) for k, v in sorted(years.items()) if v > 0]
    pairs = [(a, b) for a, b in zip(points, points[1:]) if b[0] == a[0] + 1]
    cuts = [100 * (b[1] / a[1] - 1) for a, b in pairs if b[1] < a[1]]
    all_years = [str(y) for y in range(START_YEAR, END_YEAR + 1)]
    missing = [y for y in all_years if y not in years or years[y] <= 0]
    corporate_action = any((e["bonus_per_ten"] or e["transfer_per_ten"]) and
                           e["ex_date"] >= f"{START_YEAR}-01-01" for e in events)
    cagr = None
    if not missing and (not corporate_action or comparable):
        cagr = ((years[str(END_YEAR)] / years[str(START_YEAR)]) ** (1 / (END_YEAR - START_YEAR)) - 1) * 100
    streak = 0
    for year in range(END_YEAR, START_YEAR - 1, -1):
        if years.get(str(year), 0) <= 0:
            break
        streak += 1
    return {
        "years_with_cash": len(points),
        "missing_fiscal_years": missing,
        "observed_adjacent_pairs": len(pairs),
        "observed_cuts": len(cuts),
        "worst_cut_pct": round(min(cuts), 3) if cuts else (0.0 if pairs else None),
        "consecutive_years_to_2025": streak,
        "cagr_pct": round(cagr, 3) if cagr is not None else None,
        "full_2015_2025_coverage": not missing,
        "share_adjustment_required": corporate_action and not comparable,
        "share_adjustment_applied": corporate_action and comparable,
    }


def risk_metrics(frame):
    if frame.empty or "Adj Close" not in frame:
        return None
    series = pd.to_numeric(frame["Adj Close"], errors="coerce").dropna()
    series = series[series > 0]
    if len(series) < 126:
        return None
    returns = series.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna()
    if len(returns) < 100:
        return None
    downside = np.minimum(returns.to_numpy(), 0.0)
    deviation = float(np.sqrt(np.mean(downside ** 2)))
    var_5 = returns.quantile(.05)
    tail = returns[returns <= var_5]
    result = {
        "annual_volatility_pct": round(float(returns.std(ddof=1)) * math.sqrt(252) * 100, 3),
        "max_drawdown_pct": round(float((series / series.cummax() - 1).min()) * 100, 3),
        "daily_cvar_95_pct": round(float(tail.mean()) * 100, 3) if len(tail) else None,
        "sortino_zero_rf": round(float(returns.mean()) / deviation * math.sqrt(252), 3) if deviation else None,
        "observations": len(returns),
        "adjusted_close_source": "Yahoo Finance Adj Close; approximates dividend-reinvested total return",
        "rolling_returns": {},
    }
    for years in (1, 3, 5, 10):
        window = 252 * years
        values = (series / series.shift(window) - 1).replace([np.inf, -np.inf], np.nan).dropna() * 100
        result["rolling_returns"][str(years)] = ({
            "n": int(len(values)),
            "p05_pct": round(float(values.quantile(.05)), 2),
            "median_pct": round(float(values.median()), 2),
            "p95_pct": round(float(values.quantile(.95)), 2),
        } if len(values) >= 30 else None)
    return result


def simulate_cashflow(frame, events, notional=NOTIONAL):
    """Buy once, handle stock-share changes and optional cash reinvestment.

    Yahoo historical Close is split-adjusted (but not cash-dividend-adjusted).
    We reconstruct the price of the original share before bonus/transfer
    events to avoid double-counting share splits in the initial unit count.
    """
    if frame.empty:
        return None
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    close = close.loc[close.index >= pd.Timestamp("2015-01-01", tz=close.index.tz)]
    close = close[close > 0]
    if close.empty:
        return None
    start_date = close.index[0].date()
    end_date = close.index[-1].date()
    eligible = [e for e in sorted(events, key=lambda item: item["ex_date"])
                if start_date <= date.fromisoformat(e["ex_date"]) <= end_date]
    ratios_by_date = {}
    for e in eligible:
        if e["bonus_per_ten"] or e["transfer_per_ten"]:
            ratio = 1 + (e["bonus_per_ten"] + e["transfer_per_ten"]) / 10
            if e["ex_date"] in ratios_by_date and abs(ratios_by_date[e["ex_date"]] - ratio) > 1e-8:
                raise ValueError("inconsistent split data for same ex date")
            ratios_by_date[e["ex_date"]] = ratio
    total_split_factor = math.prod(ratios_by_date.values())
    initial_adjusted_price = float(close.iloc[0])
    raw_start_price = initial_adjusted_price * total_split_factor
    shares = math.floor(notional / (raw_start_price * 100)) * 100
    if shares <= 0:
        return None
    initial_shares = shares
    reinvested_shares = float(initial_shares)
    unspent_dividend_cash = 0.0
    payment_cash = {str(year): 0.0 for year in range(start_date.year, end_date.year + 1)}
    reinvest_payment_cash = {year: 0.0 for year in payment_cash}
    gross_reinvest_distributions = 0.0
    grouped_dates = sorted({e["ex_date"] for e in eligible})
    for day in grouped_dates:
        daily_events = [e for e in eligible if e["ex_date"] == day]
        cash_per_pre_split_share = sum(e["cash_per_share_cny"] for e in daily_events)
        dividend = shares * cash_per_pre_split_share
        reinvest_dividend = reinvested_shares * cash_per_pre_split_share
        payment_cash[day[:4]] += dividend
        reinvest_payment_cash[day[:4]] += reinvest_dividend
        gross_reinvest_distributions += reinvest_dividend
        unspent_dividend_cash += reinvest_dividend
        ratio = ratios_by_date.get(day, 1.0)
        shares *= ratio
        reinvested_shares *= ratio
        later_multiplier = math.prod(value for when, value in ratios_by_date.items() if when > day)
        when = date.fromisoformat(day)
        candidate_prices = close.loc[close.index.date >= when]
        if not candidate_prices.empty and unspent_dividend_cash > 0:
            unadjusted_trade_price = float(candidate_prices.iloc[0]) * later_multiplier
            lots = math.floor(unspent_dividend_cash / (unadjusted_trade_price * 100))
            bought = lots * 100
            reinvested_shares += bought
            unspent_dividend_cash -= bought * unadjusted_trade_price
    final_price = float(close.iloc[-1])
    contributed = initial_shares * raw_start_price
    collected = sum(payment_cash.values())
    hold_value = shares * final_price
    reinvest_value = reinvested_shares * final_price + unspent_dividend_cash
    return {
        "initial_notional_cny": int(notional),
        "invested_cny": round(contributed, 2),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "initial_price_cny": round(raw_start_price, 4),
        "initial_shares": initial_shares,
        "current_shares_estimated": round(shares, 4),
        "cashflow_by_payment_year_cny": {y: round(v, 2) for y, v in payment_cash.items()},
        "cumulative_cash_cny": round(collected, 2),
        "latest_position_value_cny": round(hold_value, 2),
        "total_value_cny": round(hold_value + collected, 2),
        "total_return_pct_no_reinvest": round((hold_value + collected) / contributed * 100 - 100, 3),
        "calendar_2025_yield_on_cost_pct": round(payment_cash.get("2025", 0) / contributed * 100, 3),
        "reinvested": {
            "current_shares_estimated": round(reinvested_shares, 4),
            "leftover_cash_cny": round(unspent_dividend_cash, 2),
            "position_value_cny": round(reinvest_value, 2),
            "total_return_pct": round(reinvest_value / contributed * 100 - 100, 3),
            "dividends_reinvested_gross_cny": round(gross_reinvest_distributions, 2),
            "cashflow_by_payment_year_cny": {y: round(v, 2) for y, v in reinvest_payment_cash.items()},
        },
        "stock_action_factor": round(total_split_factor, 8),
        "assumptions": "2015 or first listed date, board lots of 100; Yahoo historical split-adjusted Close is reversed using declared bonus/transfer factors; no tax, fees, slippage or rights subscription.",
    }


def fetch_prices(symbol):
    frame = yf.Ticker(symbol).history(period="12y", auto_adjust=False, actions=True, timeout=25)
    if frame.empty:
        raise RuntimeError("Yahoo provided empty OHLC history")
    frame = frame.sort_index()
    current = float(frame["Close"].dropna().iloc[-1])
    day = frame["Close"].dropna().index[-1].date().isoformat()
    if not (current > 0):
        raise ValueError("invalid close")
    return frame, {"close_cny": round(current, 4), "as_of": day, "currency": "CNY", "source": "Yahoo Finance unadjusted close"}


def financial_indicators(symbol):
    """Optional Eastmoney data; absence must not be imputed."""
    code = symbol.split(".")[0]
    market = "SH" if symbol.endswith(".SS") else "SZ"
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "filter": f'(SECUCODE="{code}.{market}")',
        "pageNumber": 1, "pageSize": 5,
        "sortColumns": "REPORT_DATE", "sortTypes": -1,
        "source": "HSF10", "client": "PC",
    }
    try:
        response = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get",
                                params=params, headers=HEADERS, timeout=15)
        response.raise_for_status()
        rows = (response.json().get("result") or {}).get("data") or []
        row = next((r for r in rows if str(r.get("REPORT_DATE", ""))[:10].endswith("-12-31")), None)
        if not row:
            return {}
        fields = {
            "net_profit_cny": "PARENTNETPROFIT",
            "operating_cashflow_cny": "NETCASH_OPERATE_PK",
            "net_interest_margin_pct": "NET_INTEREST_MARGIN",
            "nonperforming_loan_pct": "NONPERLOAN",
            "capital_adequacy_pct": "NEWCAPITALADER",
            "roe_pct": "ROEJQ",
        }
        result = {k: number(row.get(v)) for k, v in fields.items()}
        result["fiscal_year"] = str(row["REPORT_DATE"])[:4]
        result["source"] = "Eastmoney/RPT_F10_FINANCE_MAINFINADATA"
        return result
    except Exception:
        return {}


def build_one(item, previous=None):
    symbol = item["symbol"]
    output = {"name": item["name"], "symbol": symbol, "sector": item["sector"],
              "group": item["group"], "currency": "CNY", "status": "missing",
              "sources": {"dividends": "Eastmoney/RPT_SHAREBONUS_DET",
                          "prices": "Yahoo Finance"}}
    errors = []
    events = []
    try:
        events = split_adjusted_events(apply_verified_corrections(normalize_events(eastmoney_history(symbol)), symbol))
        if not events:
            raise ValueError("no implemented fiscal-year dividend events")
        years = fiscal_history(events, comparable=True)
        raw_years = fiscal_history(events)
        output.update({"events": events, "years": years, "raw_years": raw_years,
                       "statistics": dividend_statistics(years, events, comparable=True),
                       "status": "observed"})
    except Exception as exc:
        errors.append("dividend: " + str(exc)[:160])
        if previous and previous.get("events") and previous.get("years"):
            for key in ("events", "years", "raw_years", "statistics"):
                output[key] = previous[key]
            output["status"] = "stale"
    try:
        frame, price = fetch_prices(symbol)
        output["price"] = price
        output["risk"] = risk_metrics(frame)
        if output.get("events"):
            output["simulation"] = simulate_cashflow(frame, output["events"])
            current = price["close_cny"]
            latest_year = output.get("years", {}).get(str(END_YEAR))
            output["implemented_fy_yield_pct"] = round(latest_year / current * 100, 3) if latest_year is not None else None
            cutoff = date.today() - timedelta(days=365)
            ttm = sum(event["comparable_cash_per_share_cny"] for event in output["events"]
                      if cutoff <= date.fromisoformat(event["ex_date"]) <= date.today())
            output["ttm_cash_yield_pct"] = round(ttm / current * 100, 3)
    except Exception as exc:
        errors.append("price: " + str(exc)[:160])
        if previous and previous.get("price"):
            for key in ("price", "risk", "simulation", "implemented_fy_yield_pct", "ttm_cash_yield_pct"):
                if key in previous:
                    output[key] = previous[key]
            output["price_stale"] = True
    output["financials"] = financial_indicators(symbol)
    if errors:
        output["errors"] = errors
    return output


def validate_universe(items):
    symbols = [item["symbol"] for item in items]
    if len(symbols) != len(set(symbols)):
        raise ValueError("duplicate symbol in universe")
    if len([item for item in items if item["sector"] == "banking"]) != 42:
        raise ValueError("expected 42 A-share banks")
    if len([item for item in items if item["sector"] == "telecom"]) != 3:
        raise ValueError("expected 3 A-share telecom carriers")
    for item in items:
        if not all(item.get(k) for k in ("symbol", "name", "sector", "group")):
            raise ValueError("incomplete universe item")
        if not (item["symbol"].endswith(".SS") or item["symbol"].endswith(".SZ")):
            raise ValueError("not an A-share symbol: " + item["symbol"])


def main():
    items = json.loads(REGISTRY.read_text(encoding="utf-8"))
    validate_universe(items)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    (OUTPUT.parent / "topics-universe.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    previous = {}
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass
    stocks = {}
    workers = max(1, min(6, int(os.getenv("TOPICS_WORKERS", "4"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(build_one, item, previous.get(item["symbol"])): item for item in items}
        for future in as_completed(pending):
            item = pending[future]
            try:
                stocks[item["symbol"]] = future.result()
            except Exception as exc:
                stocks[item["symbol"]] = {"symbol": item["symbol"], "name": item["name"],
                    "sector": item["sector"], "group": item["group"], "status": "error",
                    "errors": [str(exc)[:200]]}
    payload = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fiscal_years": [START_YEAR, END_YEAR],
        "dividend_basis": "Implemented taxable cash per 10 shares / 10, by REPORT_DATE fiscal year",
        "statistics_basis": "Full 2015-2025 CAGR only when all fiscal years observed and no share bonus/transfer",
        "price_basis": "Yahoo unadjusted close (CNY); risk uses adjusted close",
        "universe_size": len(items), "stocks": stocks,
        "coverage": {
            "fresh_dividends": sum(x["status"] == "observed" for x in stocks.values()),
            "stale_dividends": sum(x["status"] == "stale" for x in stocks.values()),
            "priced": sum(bool(x.get("price")) for x in stocks.values()),
        },
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if payload["coverage"]["fresh_dividends"] + payload["coverage"]["stale_dividends"] < 1:
        raise RuntimeError("All dividend sources unavailable; block deployment rather than publish empty data")


if __name__ == "__main__":
    main()
