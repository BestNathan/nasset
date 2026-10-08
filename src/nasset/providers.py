from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from .analytics import bs_delta, option_risk_score, normalize_scores, best_cell


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "nasset/0.1 (+https://github.com/BestNathan/nasset)"}
        )

    def get_json(self, url: str, params: dict | None = None, retries: int = 3) -> Any:
        error: Exception | None = None
        for attempt in range(retries):
            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"request failed: {url}") from error


class DeribitProvider:
    BASE = "https://www.deribit.com/api/v2"

    def __init__(self) -> None:
        self.http = HttpClient()

    def _call(self, method: str, **params: Any) -> Any:
        payload = self.http.get_json(f"{self.BASE}/public/{method}", params=params)
        if "error" in payload:
            raise RuntimeError(payload["error"])
        return payload["result"]

    def spot(self) -> float:
        result = self._call("get_index_price", index_name="btc_usd")
        return float(result["index_price"])

    def btc_history(self, days: int = 120) -> pd.DataFrame:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - days * 86400 * 1000
        result = self._call(
            "get_tradingview_chart_data",
            instrument_name="BTC-PERPETUAL",
            start_timestamp=start_ms,
            end_timestamp=now_ms,
            resolution="1D",
        )
        frame = pd.DataFrame(
            {"Close": result.get("close", []), "Dividends": 0.0},
            index=pd.to_datetime(result.get("ticks", []), unit="ms", utc=True),
        )
        return frame.sort_index()

    def option_matrix(
        self,
        option_type: str,
        rv_pct: float,
        target_dtes: list[int],
        target_deltas: list[int],
    ) -> dict:
        spot = self.spot()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        summaries = self._call(
            "get_book_summary_by_currency", currency="BTC", kind="option"
        )

        candidates: list[dict] = []
        for summary in summaries:
            instrument_name = str(summary.get("instrument_name") or "")
            parts = instrument_name.split("-")
            if len(parts) != 4 or parts[0] != "BTC":
                continue
            side = "call" if parts[3].upper() == "C" else "put"
            if side != option_type:
                continue
            try:
                expiry_dt = datetime.strptime(parts[1], "%d%b%y").replace(
                    hour=8, tzinfo=timezone.utc
                )
                expiry_ms = int(expiry_dt.timestamp() * 1000)
                strike = float(parts[2])
            except (ValueError, TypeError):
                continue
            dte = (expiry_ms - now_ms) / 86400000.0
            if dte <= 1:
                continue
            iv_pct = float(summary.get("mark_iv") or 0.0)
            bid = float(summary.get("bid_price") or 0.0)
            mark = float(summary.get("mark_price") or 0.0)
            premium_btc = bid if bid > 0 else mark
            if premium_btc <= 0 or iv_pct <= 0:
                continue
            delta = bs_delta(spot, strike, dte, iv_pct, option_type)
            if not math.isfinite(delta):
                continue
            oi = float(summary.get("open_interest") or 0.0)
            volume = float(summary.get("volume") or 0.0)
            liquidity = min(
                1.0,
                0.15
                + 0.55 * math.log1p(max(oi, 0.0)) / math.log1p(1000.0)
                + 0.30 * math.log1p(max(volume, 0.0)) / math.log1p(500.0),
            )
            if option_type == "call":
                annual_yield = premium_btc * 365.0 / dte * 100.0
            else:
                annual_yield = premium_btc * spot / strike * 365.0 / dte * 100.0

            candidates.append(
                {
                    "instrument": instrument_name,
                    "expiry": datetime.fromtimestamp(
                        expiry_ms / 1000, tz=timezone.utc
                    ).date().isoformat(),
                    "dte": dte,
                    "strike": strike,
                    "delta_abs": abs(delta),
                    "iv_pct": iv_pct,
                    "premium": premium_btc,
                    "premium_currency": "BTC",
                    "annualized_yield_pct": annual_yield,
                    "open_interest": oi,
                    "volume": volume,
                    "liquidity": liquidity,
                    "spot": spot,
                    "source": "Deribit public API",
                }
            )

        return _bucket_matrix(
            candidates, option_type, rv_pct, target_dtes, target_deltas
        )


class YahooProvider:
    def history(self, symbol: str, days: int) -> pd.DataFrame:
        start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days + 20)
        ticker = yf.Ticker(symbol)
        frame = ticker.history(
            start=start.date().isoformat(),
            auto_adjust=False,
            actions=True,
            repair=False,
        )
        if frame.empty:
            raise RuntimeError(f"Yahoo returned no history for {symbol}")
        if "Dividends" not in frame:
            frame["Dividends"] = 0.0
        frame.index = pd.to_datetime(frame.index, utc=True)
        return frame[["Close", "Dividends"]].sort_index()

    def history_usd(self, symbol: str, days: int, currency: str = "USD") -> tuple[pd.DataFrame, dict]:
        local = self.history(symbol, days)
        local_current = float(local["Close"].dropna().iloc[-1])
        if currency.upper() == "USD":
            return local, {
                "quote_currency": "USD",
                "valuation_currency": "USD",
                "local_current_price": local_current,
                "fx_to_usd": 1.0,
            }

        fx_symbol = f"{currency.upper()}USD=X"
        fx = self.history(fx_symbol, days)
        rates = fx["Close"].reindex(local.index, method="ffill").bfill()
        if rates.isna().any():
            raise RuntimeError(f"missing FX conversion data for {currency}/USD")

        converted = local.copy()
        converted["Close"] = converted["Close"] * rates
        converted["Dividends"] = converted["Dividends"].fillna(0.0) * rates
        return converted, {
            "quote_currency": currency.upper(),
            "valuation_currency": "USD",
            "local_current_price": local_current,
            "fx_to_usd": float(rates.iloc[-1]),
        }

    def option_matrix(
        self,
        symbol: str,
        rv_pct: float,
        option_type: str,
        target_dtes: list[int],
        target_deltas: list[int],
        rate: float = 0.04,
    ) -> dict:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d", auto_adjust=False)
        if history.empty:
            raise RuntimeError(f"Yahoo returned no spot for {symbol}")
        spot = float(history["Close"].dropna().iloc[-1])
        today = pd.Timestamp.now(tz="UTC").date()

        expirations = []
        for value in ticker.options:
            expiry = datetime.strptime(value, "%Y-%m-%d").date()
            dte = (expiry - today).days
            if dte > 0:
                expirations.append((value, dte))
        if not expirations:
            raise RuntimeError(f"Yahoo returned no option expirations for {symbol}")

        selected_expiries: dict[str, int] = {}
        for target in target_dtes:
            value, dte = min(expirations, key=lambda x: abs(x[1] - target))
            selected_expiries[value] = dte

        candidates: list[dict] = []
        for expiry_str, dte in selected_expiries.items():
            chain = ticker.option_chain(expiry_str)
            table = chain.calls if option_type == "call" else chain.puts
            for _, row in table.iterrows():
                strike = float(row.get("strike") or 0.0)
                iv_pct = float(row.get("impliedVolatility") or 0.0) * 100.0
                bid = float(row.get("bid") or 0.0)
                iv_floor = max(5.0, rv_pct * 0.60)
                iv_ceiling = max(200.0, rv_pct * 8.0)
                is_otm = (option_type == "call" and strike > spot) or (
                    option_type == "put" and strike < spot
                )
                if (
                    strike <= 0
                    or bid <= 0
                    or not is_otm
                    or iv_pct < iv_floor
                    or iv_pct > iv_ceiling
                ):
                    continue
                premium = bid
                delta = bs_delta(spot, strike, dte, iv_pct, option_type, rate=rate)
                if not math.isfinite(delta):
                    continue
                oi = float(row.get("openInterest") or 0.0)
                volume = float(row.get("volume") or 0.0)
                liquidity = min(
                    1.0,
                    0.15
                    + 0.55 * math.log1p(max(oi, 0.0)) / math.log1p(10000.0)
                    + 0.30 * math.log1p(max(volume, 0.0)) / math.log1p(5000.0),
                )
                capital = spot if option_type == "call" else strike
                annual_yield = premium / capital * 365.0 / dte * 100.0
                candidates.append(
                    {
                        "instrument": str(row.get("contractSymbol") or ""),
                        "expiry": expiry_str,
                        "dte": float(dte),
                        "strike": strike,
                        "delta_abs": abs(delta),
                        "iv_pct": iv_pct,
                        "premium": premium,
                        "premium_currency": "USD/share",
                        "annualized_yield_pct": annual_yield,
                        "open_interest": oi,
                        "volume": volume,
                        "liquidity": liquidity,
                        "spot": spot,
                        "source": "Yahoo Finance",
                    }
                )

        return _bucket_matrix(
            candidates, option_type, rv_pct, target_dtes, target_deltas
        )


def _bucket_matrix(
    candidates: list[dict],
    option_type: str,
    rv_pct: float,
    target_dtes: list[int],
    target_deltas: list[int],
) -> dict:
    cells: list[dict] = []
    for target_dte in target_dtes:
        if not candidates:
            break
        nearest_dte = min(candidates, key=lambda c: abs(c["dte"] - target_dte))["dte"]
        same_expiry = [c for c in candidates if abs(c["dte"] - nearest_dte) < 0.51]
        for target_delta in target_deltas:
            target = target_delta / 100.0
            if not same_expiry:
                cells.append(
                    {
                        "target_dte": target_dte,
                        "target_delta_pct": target_delta,
                        "available": False,
                    }
                )
                continue
            selected = min(
                same_expiry, key=lambda c: abs(c["delta_abs"] - target)
            ).copy()
            tolerance = max(0.025, target * 0.35)
            if abs(selected["delta_abs"] - target) > tolerance:
                cells.append(
                    {
                        "target_dte": target_dte,
                        "target_delta_pct": target_delta,
                        "available": False,
                        "reason": "no option close enough to target delta",
                    }
                )
                continue
            selected["target_dte"] = target_dte
            selected["target_delta_pct"] = target_delta
            selected["available"] = True
            selected["_raw_score"] = option_risk_score(
                selected["annualized_yield_pct"],
                selected["delta_abs"],
                selected["dte"],
                selected["iv_pct"],
                rv_pct,
                selected["liquidity"],
            )
            for key in (
                "dte", "strike", "delta_abs", "iv_pct", "premium",
                "annualized_yield_pct", "open_interest", "volume", "liquidity", "spot"
            ):
                selected[key] = round(float(selected[key]), 6)
            cells.append(selected)

    normalize_scores(cells)
    return {
        "option_type": option_type,
        "target_dtes": target_dtes,
        "target_deltas_pct": target_deltas,
        "cells": cells,
        "best_risk_adjusted": best_cell(cells, "raw_score"),
        "highest_cash_yield": best_cell(cells, "annualized_yield_pct"),
        "score_method": (
            "annualized premium yield divided by |delta|^1.2, adjusted for DTE, "
            "IV-vs-realized-vol edge, and liquidity"
        ),
    }
