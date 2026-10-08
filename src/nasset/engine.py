from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analytics import asset_metrics
from .providers import DeribitProvider, YahooProvider

TARGET_DTES = [7, 14, 30, 45, 60]
TARGET_DELTAS = [5, 10, 15, 20, 25]

ASSETS: list[dict[str, Any]] = [
    {"id":"btc","name":"Bitcoin","symbol":"BTC","category":"Crypto","liquidity":"high","window_days":90,"provider":"deribit","strategies":["covered_call","cash_secured_put"],"description":"BTC spot plus Deribit option-income overlays."},
    {"id":"spy","name":"S&P 500","symbol":"SPY","category":"Equity","liquidity":"high","window_days":90,"provider":"yahoo","strategies":["covered_call","cash_secured_put"],"description":"SPY as a liquid high-quality equity proxy with option overlays."},
    {"id":"tlt","name":"US Long Treasury","symbol":"TLT","category":"Government bonds","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"20+ year US Treasury ETF proxy; 3-year income and mark-to-market window."},
    {"id":"vnq","name":"US REIT","symbol":"VNQ","category":"REIT","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Broad US listed real estate proxy."},
    {"id":"sreit","name":"Singapore REIT","symbol":"CLR.SI","category":"REIT","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Lion-Phillip S-REIT ETF proxy for Singapore REIT income."},
    {"id":"jreit","name":"Japan REIT","symbol":"1343.T","category":"REIT","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"NEXT FUNDS Tokyo Stock Exchange REIT Index ETF proxy."},
    {"id":"infra","name":"Global Infrastructure","symbol":"IGF","category":"Infrastructure","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Global infrastructure equities as a listed real-asset proxy."},
]


def collect_snapshot() -> dict:
    deribit = DeribitProvider()
    yahoo = YahooProvider()
    assets: list[dict] = []
    errors: list[dict] = []

    for config in ASSETS:
        try:
            history = (
                deribit.btc_history(days=config["window_days"] + 25)
                if config["provider"] == "deribit"
                else yahoo.history(config["symbol"], config["window_days"])
            )
            metrics = asset_metrics(history, config["window_days"])
            strategies: list[dict] = []

            if "covered_call" in config["strategies"]:
                matrix = (
                    deribit.option_matrix("call", metrics["realized_vol_pct"], TARGET_DTES, TARGET_DELTAS)
                    if config["provider"] == "deribit"
                    else yahoo.option_matrix(config["symbol"], metrics["realized_vol_pct"], "call", TARGET_DTES, TARGET_DELTAS)
                )
                strategies.append({"id":"covered_call","name":"Covered Call","kind":"option_matrix","matrix":matrix})

            if "cash_secured_put" in config["strategies"]:
                matrix = (
                    deribit.option_matrix("put", metrics["realized_vol_pct"], TARGET_DTES, TARGET_DELTAS)
                    if config["provider"] == "deribit"
                    else yahoo.option_matrix(config["symbol"], metrics["realized_vol_pct"], "put", TARGET_DTES, TARGET_DELTAS)
                )
                strategies.append({"id":"cash_secured_put","name":"Cash-Secured Put","kind":"option_matrix","matrix":matrix})

            if "distributions" in config["strategies"]:
                strategies.append({
                    "id":"distributions",
                    "name":"Hold for Distributions",
                    "kind":"income",
                    "annualized_yield_pct":metrics["annualized_cash_yield_pct"],
                    "measurement_window_days":config["window_days"],
                })

            assets.append({
                "id":config["id"], "name":config["name"], "symbol":config["symbol"],
                "category":config["category"], "liquidity":config["liquidity"],
                "measurement_window_days":config["window_days"], "description":config["description"],
                "valuation":metrics, "strategies":strategies,
                "best_strategy":_choose_asset_best(strategies),
            })
        except Exception as exc:
            errors.append({"asset_id":config["id"],"symbol":config["symbol"],"error":f"{type(exc).__name__}: {exc}"})

    return {
        "schema_version":1,
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "base_currency":"USD",
        "methodology":{
            "high_liquidity_window_days":90,
            "income_asset_window_days":1095,
            "valuation":"Invest 100 at the beginning of the window, hold, do not reinvest distributions, and mark to current value.",
            "option_income":"Current seller premium proxy (bid when available), mechanically annualized by 365/DTE. Delta is a Black-Scholes proxy. Daily snapshots build the historical estimate series.",
            "best_strategy":"Option matrices rank premium yield per |delta| adjusted for DTE, IV versus recent realized volatility, and liquidity.",
        },
        "assets":assets,
        "errors":errors,
    }


def _choose_asset_best(strategies: list[dict]) -> dict | None:
    candidates: list[dict] = []
    for strategy in strategies:
        if strategy["kind"] == "option_matrix":
            cell = strategy["matrix"].get("best_risk_adjusted")
            if cell:
                candidates.append({
                    "strategy_id":strategy["id"],"strategy_name":strategy["name"],
                    "annualized_cash_yield_pct":cell["annualized_yield_pct"],"score":cell.get("score"),
                    "dte":cell.get("dte"),"delta_abs":cell.get("delta_abs"),"strike":cell.get("strike"),
                    "instrument":cell.get("instrument"),
                })
        else:
            candidates.append({
                "strategy_id":strategy["id"],"strategy_name":strategy["name"],
                "annualized_cash_yield_pct":strategy["annualized_yield_pct"],"score":None,
            })
    if not candidates:
        return None
    scored = [x for x in candidates if x.get("score") is not None]
    return max(scored, key=lambda x: float(x["score"])) if scored else max(candidates, key=lambda x: float(x["annualized_cash_yield_pct"]))


def write_outputs(snapshot: dict, root: Path) -> None:
    data_dir = root / "data"
    snapshots_dir = data_dir / "snapshots"
    site_data_dir = root / "site" / "data"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    site_data_dir.mkdir(parents=True, exist_ok=True)
    date = snapshot["generated_at"][:10]
    _write_json(snapshots_dir / f"{date}.json", snapshot)
    _write_json(data_dir / "latest.json", snapshot)
    _write_json(site_data_dir / "latest.json", snapshot)
    timeline = build_timeline(snapshots_dir)
    _write_json(data_dir / "timeline.json", timeline)
    _write_json(site_data_dir / "timeline.json", timeline)


def build_timeline(snapshots_dir: Path) -> dict:
    rows: dict[str, list[dict]] = {}
    for path in sorted(snapshots_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        date = payload.get("generated_at", "")[:10]
        for asset in payload.get("assets", []):
            best = asset.get("best_strategy") or {}
            valuation = asset.get("valuation") or {}
            rows.setdefault(asset["id"], []).append({
                "date":date,
                "current_asset_value_index":valuation.get("current_asset_value_index"),
                "total_value_index":valuation.get("total_value_index"),
                "price_return_pct":valuation.get("price_return_pct"),
                "annualized_cash_yield_pct":best.get("annualized_cash_yield_pct", valuation.get("annualized_cash_yield_pct")),
                "best_strategy":best.get("strategy_name"),
            })
    return {"schema_version":1,"assets":rows}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
