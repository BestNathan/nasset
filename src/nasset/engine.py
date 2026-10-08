from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analytics import asset_metrics
from .providers import CboeProvider, DeribitProvider, YahooProvider

TARGET_DTES = [7, 14, 30, 60, 90]
TARGET_DELTAS = [5, 10, 15, 20, 25]

ASSETS: list[dict[str, Any]] = [
    {"id":"btc","name":"Bitcoin","symbol":"BTC","currency":"USD","category":"Crypto","liquidity":"high","window_days":90,"provider":"deribit","strategies":["covered_call","cash_secured_put"],"description":"BTC spot plus Deribit option-income overlays."},
    {"id":"spy","name":"S&P 500","symbol":"SPY","currency":"USD","category":"Equity","liquidity":"high","window_days":90,"provider":"yahoo","option_provider":"cboe","strategies":["covered_call","cash_secured_put"],"description":"SPY as a liquid high-quality equity proxy with option overlays."},
    {"id":"tlt","name":"US Long Treasury","symbol":"TLT","currency":"USD","category":"Government bonds","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"20+ year US Treasury ETF proxy; 3-year income and mark-to-market window."},
    {"id":"vnq","name":"US REIT","symbol":"VNQ","currency":"USD","category":"REIT","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Broad US listed real estate proxy."},
    {"id":"sreit","name":"Singapore REIT","symbol":"CLR.SI","currency":"SGD","category":"REIT","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Lion-Phillip S-REIT ETF proxy for Singapore REIT income."},
    {"id":"jreit","name":"Japan REIT","symbol":"1343.T","currency":"JPY","category":"REIT","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"NEXT FUNDS Tokyo Stock Exchange REIT Index ETF proxy."},
    {"id":"infra","name":"Global Infrastructure","symbol":"IGF","currency":"USD","category":"Infrastructure","liquidity":"income","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Global infrastructure equities as a listed real-asset proxy."},
]


def collect_snapshot() -> dict:
    deribit = DeribitProvider()
    cboe = CboeProvider()
    yahoo = YahooProvider()
    assets: list[dict] = []
    errors: list[dict] = []

    for config in ASSETS:
        try:
            if config["provider"] == "deribit":
                history = deribit.btc_history(days=config["window_days"] + 25)
                market_meta = {
                    "quote_currency": "USD",
                    "valuation_currency": "USD",
                    "local_current_price": float(history["Close"].dropna().iloc[-1]),
                    "fx_to_usd": 1.0,
                }
            else:
                history, market_meta = yahoo.history_usd(
                    config["symbol"], config["window_days"], config["currency"]
                )
            metrics = asset_metrics(history, config["window_days"])
            metrics.update(market_meta)
            strategies: list[dict] = []

            if "covered_call" in config["strategies"]:
                matrix = _option_matrix(
                    config,
                    "call",
                    metrics["realized_vol_pct"],
                    deribit,
                    cboe,
                    yahoo,
                )
                strategies.append({"id":"covered_call","name":"Covered Call","kind":"option_matrix","matrix":matrix})

            if "cash_secured_put" in config["strategies"]:
                matrix = _option_matrix(
                    config,
                    "put",
                    metrics["realized_vol_pct"],
                    deribit,
                    cboe,
                    yahoo,
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
                "category":config["category"], "liquidity":config["liquidity"], "quote_currency":config["currency"],
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
            "option_income":"Current seller-executable bid is mechanically annualized by 365/DTE. BTC uses Deribit; listed US options prefer CBOE delayed Greeks/quotes with a Yahoo fallback. Daily snapshots build the historical estimate series.",
            "best_strategy":"Option matrices rank premium yield divided by |delta|^1.2, adjusted for DTE, IV versus recent realized volatility, and liquidity.",
        },
        "assets":assets,
        "errors":errors,
    }


def _option_matrix(
    config: dict,
    option_type: str,
    rv_pct: float,
    deribit: DeribitProvider,
    cboe: CboeProvider,
    yahoo: YahooProvider,
) -> dict:
    if config["provider"] == "deribit":
        return deribit.option_matrix(
            option_type, rv_pct, TARGET_DTES, TARGET_DELTAS
        )

    if config.get("option_provider") == "cboe":
        try:
            matrix = cboe.option_matrix(
                config["symbol"],
                rv_pct,
                option_type,
                TARGET_DTES,
                TARGET_DELTAS,
            )
            if any(cell.get("available") for cell in matrix.get("cells", [])):
                return matrix
        except Exception:
            pass

    return yahoo.option_matrix(
        config["symbol"],
        rv_pct,
        option_type,
        TARGET_DTES,
        TARGET_DELTAS,
    )


def _choose_asset_best(strategies: list[dict]) -> dict | None:
    candidates: list[dict] = []
    for strategy in strategies:
        if strategy["kind"] == "option_matrix":
            cell = strategy["matrix"].get("best_risk_adjusted")
            if cell:
                candidates.append({
                    "strategy_id":strategy["id"],"strategy_name":strategy["name"],
                    "annualized_cash_yield_pct":cell["annualized_yield_pct"],"score":cell.get("score"),"raw_score":cell.get("raw_score"),
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
    scored = [x for x in candidates if x.get("raw_score") is not None]
    return max(scored, key=lambda x: float(x["raw_score"])) if scored else max(candidates, key=lambda x: float(x["annualized_cash_yield_pct"]))


def write_outputs(snapshot: dict, root: Path) -> None:
    data_dir = root / "data"
    snapshots_dir = data_dir / "snapshots"
    site_data_dir = root / "site" / "data"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    site_data_dir.mkdir(parents=True, exist_ok=True)

    _carry_forward_stale_assets(snapshot, data_dir / "latest.json")
    if len(snapshot.get("assets", [])) < 4:
        raise RuntimeError(
            f"data quality gate failed: only {len(snapshot.get('assets', []))} assets available"
        )

    date = snapshot["generated_at"][:10]
    _write_json(snapshots_dir / f"{date}.json", snapshot)
    _write_json(data_dir / "latest.json", snapshot)
    _write_json(site_data_dir / "latest.json", snapshot)
    timeline = build_timeline(snapshots_dir)
    _write_json(data_dir / "timeline.json", timeline)
    _write_json(site_data_dir / "timeline.json", timeline)


def _carry_forward_stale_assets(snapshot: dict, latest_path: Path) -> None:
    if not latest_path.exists():
        return
    try:
        previous = json.loads(latest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    current_ids = {asset["id"] for asset in snapshot.get("assets", [])}
    previous_map = {asset["id"]: asset for asset in previous.get("assets", [])}
    for error in snapshot.get("errors", []):
        asset_id = error.get("asset_id")
        if not asset_id or asset_id in current_ids or asset_id not in previous_map:
            continue
        stale = copy.deepcopy(previous_map[asset_id])
        stale["stale"] = True
        stale["stale_since"] = previous.get("generated_at")
        snapshot["assets"].append(stale)

    order = {config["id"]: idx for idx, config in enumerate(ASSETS)}
    snapshot["assets"].sort(key=lambda asset: order.get(asset["id"], 999))


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
