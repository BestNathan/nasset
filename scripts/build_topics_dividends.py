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
        if not report_date or not ex_date or per_ten is None or per_ten < 0:
            continue  # proposed distributions are NOT realized cash
        fy = int(report_date[:4])
        if fy < START_YEAR or fy > END_YEAR:
            continue
        key = (fy, ex_date, round(per_ten, 8))
        if key in seen:
            continue
        seen.add(key)
        bonus = number(row.get("BONUS_IT_RATIO")) or 0
        transfer = number(row.get("IT_RATIO")) or 0
        if bonus < 0 or transfer < 0 or bonus + transfer > 100:
            raise ValueError("invalid share bonus/transfer ratio")
        result.append({
            "fiscal_year": fy,
            "ex_date": ex_date,
            "per_ten_cny": round(per_ten, 8),
            "cash_per_share_cny": round(per_ten / 10, 9),
            "bonus_per_ten": bonus,
            "transfer_per_ten": transfer,
            "source": "Eastmoney/RPT_SHAREBONUS_DET",
        })
    return sorted(result, key=lambda row: (row["ex_date"], row["fiscal_year"]))


def fiscal_history(events):
    years = {}
    for e in events:
        key = str(e["fiscal_year"])
        years[key] = years.get(key, 0) + e["cash_per_share_cny"]
    return {k: round(v, 8) for k, v in sorted(years.items())}


def dividend_statistics(years, events):
    """Missing is NOT zero, and a gap does NOT become a successful dividend year."""
    points = [(int(k), float(v)) for k, v in sorted(years.items()) if v > 0]
    pairs = [(a, b) for a, b in zip(points, points[1:]) if b[0] == a[0] + 1]
    cuts = [100 * (b[1] / a[1] - 1) for a, b in pairs if b[1] < a[1]]
    all_years = [str(y) for y in range(START_YEAR, END_YEAR + 1)]
    missing = [y for y in all_years if y not in years]
    corporate_action = any(e["bonus_per_ten"] or e["transfer_per_ten"] for e in events)
    # Raw per-share growth is not comparable across bonus / transfer share changes.
    cagr = None
    if not missing and not corporate_action and all(years[y] > 0 for y in all_years):
        cagr = ((points[-1][1] / points[0][1]) ** (1 / (END_YEAR - START_YEAR)) - 1) * 100
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
        "share_adjustment_required": corporate_action,
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
    """Buy full A-share board lots once, adjust held units after stock bonuses."""
    if frame.empty:
        return None
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    close = close.loc[close.index >= pd.Timestamp("2015-01-01", tz=close.index.tz)]
    close = close[close > 0]
    if close.empty:
        return None
    start_price = float(close.iloc[0])
    shares = math.floor(notional / (start_price * 100)) * 100
    if shares <= 0:
        return None
    initial_shares = shares
    start_date = close.index[0].date()
    yearly_cash = {str(year): 0.0 for year in range(start_date.year, max(END_YEAR, date.today().year) + 1)}
    for event in sorted(events, key=lambda item: item["ex_date"]):
        ex_date = date.fromisoformat(event["ex_date"])
        if ex_date < start_date or ex_date > date.today():
            continue
        key = str(ex_date.year)
        if key not in yearly_cash:
            continue
        yearly_cash[key] += shares * event["cash_per_share_cny"]
        ratio = 1 + (event["bonus_per_ten"] + event["transfer_per_ten"]) / 10.0
        shares *= ratio
    return {
        "initial_notional_cny": int(notional),
        "invested_cny": round(initial_shares * start_price, 2),
        "start_date": start_date.isoformat(),
        "initial_price_cny": round(start_price, 4),
        "initial_shares": initial_shares,
        "current_shares_estimated": round(shares, 4),
        "cashflow_by_payment_year_cny": {y: round(value, 2) for y, value in yearly_cash.items()},
        "cumulative_cash_cny": round(sum(yearly_cash.values()), 2),
        "latest_position_value_cny": round(shares * float(close.iloc[-1]), 2),
        "assumptions": "2015 or first listed date, 100-share lots, no reinvestment, before tax/fees; share bonuses inferred only from disclosed events.",
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
        events = normalize_events(eastmoney_history(symbol))
        if not events:
            raise ValueError("no implemented fiscal-year dividend events")
        years = fiscal_history(events)
        output.update({"events": events, "years": years,
                       "statistics": dividend_statistics(years, events),
                       "status": "observed"})
    except Exception as exc:
        errors.append("dividend: " + str(exc)[:160])
        if previous and previous.get("events") and previous.get("years"):
            for key in ("events", "years", "statistics"):
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
            ttm = sum(event["cash_per_share_cny"] for event in output["events"]
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
