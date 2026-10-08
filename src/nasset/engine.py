from __future__ import annotations

import copy
import json

import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analytics import asset_metrics, manual_income_metrics
from .providers import CboeProvider, DeribitProvider, LidoProvider, YahooProvider
from .taxonomy import CASHFLOW_TAXONOMY, taxonomy_index

TARGET_DTES = [7, 14, 30, 60, 90]
TARGET_DELTAS = [5, 10, 15, 20, 25]

ASSETS: list[dict[str, Any]] = [
    {"id":"sgov","name":"US 0–3M Treasury","symbol":"SGOV","currency":"USD","market":"US","unit":"share","layer_id":"contractual","subcategory_id":"cash_short_rates","category":"Cash & short rates","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"0–3 month US Treasury ETF proxy; short-duration contractual income baseline."},
    {"id":"tlt","name":"US Long Treasury","symbol":"TLT","currency":"USD","market":"US","unit":"share","layer_id":"contractual","subcategory_id":"sovereign_bonds","category":"Government bonds","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"20+ year US Treasury ETF proxy; contractual coupon income with substantial duration risk."},
    {"id":"china_gov5y","name":"China 5Y Government Bond ETF","symbol":"511010.SS","currency":"CNY","market":"China A","unit":"share","layer_id":"contractual","subcategory_id":"sovereign_bonds","category":"Government bonds","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Guotai SSE 5-year government bond ETF; domestic sovereign-duration benchmark."},
    {"id":"lqd","name":"US Investment Grade Credit","symbol":"LQD","currency":"USD","market":"US","unit":"share","layer_id":"contractual","subcategory_id":"investment_grade_credit","category":"Investment grade credit","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Broad US investment-grade corporate bond ETF proxy."},
    {"id":"jaaa","name":"AAA CLO","symbol":"JAAA","currency":"USD","market":"US","unit":"share","layer_id":"contractual","subcategory_id":"structured_senior_credit","category":"AAA CLO","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Janus Henderson AAA CLO ETF; floating-rate senior structured-credit income."},
    {"id":"hyg","name":"US High Yield Credit","symbol":"HYG","currency":"USD","market":"US","unit":"share","layer_id":"contractual","subcategory_id":"high_yield_credit","category":"High yield credit","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Broad USD high-yield corporate bond ETF proxy; higher contractual income with larger credit-cycle risk."},

    {"id":"icbc","name":"ICBC A","symbol":"601398.SS","currency":"CNY","market":"China A","unit":"share","layer_id":"productive","subcategory_id":"dividend_equity","category":"China bank","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Industrial and Commercial Bank of China A-share; dividend income plus bank-equity price risk."},
    {"id":"ccb","name":"China Construction Bank A","symbol":"601939.SS","currency":"CNY","market":"China A","unit":"share","layer_id":"productive","subcategory_id":"dividend_equity","category":"China bank","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"China Construction Bank A-share; dividend income plus bank-equity price risk."},
    {"id":"cmb","name":"China Merchants Bank A","symbol":"600036.SS","currency":"CNY","market":"China A","unit":"share","layer_id":"productive","subcategory_id":"dividend_equity","category":"China bank","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"China Merchants Bank A-share; a higher-quality retail-bank proxy with dividend income."},
    {"id":"china_dividend","name":"China Dividend ETF","symbol":"510880.SS","currency":"CNY","market":"China A","unit":"share","layer_id":"productive","subcategory_id":"dividend_equity","category":"Dividend equity","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Huatai-PineBridge SSE Dividend ETF; diversified China high-dividend equity benchmark."},
    {"id":"pff","name":"US Preferred Stock","symbol":"PFF","currency":"USD","market":"US","unit":"share","layer_id":"productive","subcategory_id":"preferred_hybrid","category":"Preferred stock","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Broad US preferred and hybrid securities ETF proxy."},
    {"id":"bizd","name":"US BDC","symbol":"BIZD","currency":"USD","market":"US","unit":"share","layer_id":"productive","subcategory_id":"private_credit","category":"Listed private credit","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"VanEck BDC Income ETF; listed business-development-company/private-credit income proxy."},
    {"id":"vnq","name":"US REIT","symbol":"VNQ","currency":"USD","market":"US","unit":"share","layer_id":"productive","subcategory_id":"reit","category":"Broad REIT","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Broad US listed real estate proxy."},
    {"id":"sreit","name":"Singapore REIT","symbol":"CLR.SI","currency":"SGD","market":"Singapore","unit":"share","layer_id":"productive","subcategory_id":"reit","category":"Broad REIT","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Lion-Phillip S-REIT ETF proxy for Singapore REIT income."},
    {"id":"jreit","name":"Japan REIT","symbol":"1343.T","currency":"JPY","market":"Japan","unit":"share","layer_id":"productive","subcategory_id":"reit","category":"Broad REIT","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"NEXT FUNDS Tokyo Stock Exchange REIT Index ETF proxy."},

    {"id":"dlr","name":"US REIT · Data Center","symbol":"DLR","currency":"USD","market":"US","unit":"share","layer_id":"productive","subcategory_id":"reit_data_center","category":"Data center REIT","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Digital Realty; listed data-center REIT backed by colocation and hyperscale infrastructure."},
    {"id":"china_reit_datacenter","name":"China REIT · Data Center","symbol":"508060.SS","currency":"CNY","market":"China REIT","unit":"share","layer_id":"productive","subcategory_id":"reit_data_center","category":"C-REIT data center","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Southern GDS Data Center REIT; China public REIT backed by data-center infrastructure."},

    {"id":"pld","name":"US REIT · Logistics","symbol":"PLD","currency":"USD","market":"US","unit":"share","layer_id":"productive","subcategory_id":"reit_logistics","category":"Logistics REIT","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Prologis; global logistics and warehouse REIT."},
    {"id":"china_reit_logistics","name":"China REIT · Logistics","symbol":"508056.SS","currency":"CNY","market":"China REIT","unit":"share","layer_id":"productive","subcategory_id":"reit_logistics","category":"C-REIT logistics","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"CICC GLP REIT; China public REIT backed by warehousing and logistics assets."},

    {"id":"china_reit_clean_energy","name":"China REIT · Clean Energy","symbol":"508016.SS","currency":"CNY","market":"China REIT","unit":"share","layer_id":"productive","subcategory_id":"reit_energy","category":"C-REIT clean energy","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"ChinaAMC Huadian Clean Energy REIT; clean-energy infrastructure cashflow proxy."},

    {"id":"china_reit_toll","name":"China REIT · Toll Road","symbol":"508018.SS","currency":"CNY","market":"China REIT","unit":"share","layer_id":"productive","subcategory_id":"reit_transport","category":"C-REIT toll road","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"ChinaAMC China Communications Construction Expressway REIT; toll-road cashflow benchmark."},
    {"id":"infra","name":"Global Infrastructure","symbol":"IGF","currency":"USD","market":"Global","unit":"share","layer_id":"productive","subcategory_id":"infrastructure","category":"Infrastructure","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"Global infrastructure equities as a listed real-asset proxy."},
    {"id":"amlp","name":"US MLP / Pipelines","symbol":"AMLP","currency":"USD","market":"US","unit":"share","layer_id":"productive","subcategory_id":"infrastructure","category":"MLP / pipelines","liquidity":"income","source_cadence":"daily","window_days":1095,"provider":"yahoo","strategies":["distributions"],"description":"US midstream MLP and pipeline income proxy."},

    {"id":"chongli_property","name":"Chongli Property Benchmark","symbol":"CHONGLI-RESI","currency":"CNY","market":"China · Hebei · Chongli","unit":"m²","layer_id":"real_assets","subcategory_id":"direct_property","category":"Direct property","liquidity":"illiquid","source_cadence":"monthly","window_days":1095,"provider":"manual_real_asset","data_file":"data/manual/chongli.json","strategies":["rental_income"],"description":"Chongli district second-hand residential benchmark; combines regional price change with a gross rental-yield benchmark."},

    {"id":"btc","name":"Bitcoin","symbol":"BTC","currency":"USD","market":"Crypto","unit":"BTC","layer_id":"engineered","subcategory_id":"option_overlay","category":"Crypto option overlay","liquidity":"high","source_cadence":"daily","window_days":90,"provider":"deribit","crypto_currency":"BTC","strategies":["covered_call","cash_secured_put"],"description":"BTC spot plus Deribit option-income overlays."},
    {"id":"eth","name":"Ethereum","symbol":"ETH","currency":"USD","market":"Crypto","unit":"ETH","layer_id":"engineered","subcategory_id":"staking","category":"ETH staking + option overlay","liquidity":"high","source_cadence":"daily","window_days":90,"provider":"deribit","crypto_currency":"ETH","strategies":["staking","covered_call","cash_secured_put"],"description":"ETH spot with Lido stETH staking APR plus Deribit option-income overlays."},
    {"id":"spy","name":"S&P 500","symbol":"SPY","currency":"USD","market":"US","unit":"share","layer_id":"engineered","subcategory_id":"option_overlay","category":"Equity option overlay","liquidity":"high","source_cadence":"daily","window_days":90,"provider":"yahoo","option_provider":"cboe","strategies":["covered_call","cash_secured_put"],"description":"SPY as a liquid equity underlying with option-income overlays."},
    {"id":"gld","name":"Gold","symbol":"GLD","currency":"USD","market":"US","unit":"share","layer_id":"engineered","subcategory_id":"option_overlay","category":"Gold option overlay","liquidity":"high","source_cadence":"daily","window_days":90,"provider":"yahoo","option_provider":"cboe","strategies":["covered_call","cash_secured_put"],"description":"SPDR Gold Shares as a liquid gold proxy with listed option-income overlays."},
]



def collect_snapshot() -> dict:
    deribit = DeribitProvider()
    cboe = CboeProvider()
    lido = LidoProvider()
    yahoo = YahooProvider()
    taxonomy = taxonomy_index()
    assets: list[dict] = []
    errors: list[dict] = []

    for config in ASSETS:
        try:
            if config["provider"] == "deribit":
                crypto_currency = config.get("crypto_currency", config["symbol"]).upper()
                history = deribit.crypto_history(
                    crypto_currency, days=config["window_days"] + 25
                )
                market_meta = {
                    "quote_currency": "USD",
                    "valuation_currency": "USD",
                    "local_current_price": float(history["Close"].dropna().iloc[-1]),
                    "fx_to_usd": 1.0,
                    "valuation_unit": config.get("unit", "unit"),
                    "source_cadence": config.get("source_cadence", "daily"),
                    "source": "Deribit public API",
                }
                metrics = asset_metrics(history, config["window_days"])
            elif config["provider"] == "manual_real_asset":
                metrics, market_meta = _manual_real_asset_metrics(config, yahoo)
            else:
                history, market_meta = yahoo.history_usd(
                    config["symbol"], config["window_days"], config["currency"]
                )
                metrics = asset_metrics(history, config["window_days"])
                market_meta.update({
                    "valuation_unit": config.get("unit", "share"),
                    "source_cadence": config.get("source_cadence", "daily"),
                    "source": "Yahoo Finance",
                })
            metrics.update(market_meta)
            strategies: list[dict] = []

            if "staking" in config["strategies"]:
                try:
                    staking_apr = lido.staking_apr_sma()
                    strategies.append({
                        "id":"staking",
                        "name":"stETH Staking",
                        "kind":"income",
                        "annualized_yield_pct":round(staking_apr, 4),
                        "measurement_window_days":7,
                        "yield_basis":"Lido stETH 7-day SMA APR",
                        "source":"Lido APR API",
                    })
                except Exception as exc:
                    errors.append({
                        "asset_id":config["id"],
                        "symbol":config["symbol"],
                        "strategy_id":"staking",
                        "error":f"{type(exc).__name__}: {exc}",
                    })

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

            if "rental_income" in config["strategies"]:
                strategies.append({
                    "id":"rental_income",
                    "name":"Gross Rental Income",
                    "kind":"income",
                    "annualized_yield_pct":metrics["annualized_cash_yield_pct"],
                    "measurement_window_days":config["window_days"],
                    "yield_basis":"gross regional rental yield",
                })

            if "distributions" in config["strategies"]:
                strategies.append({
                    "id":"distributions",
                    "name":"Hold for Distributions",
                    "kind":"income",
                    "annualized_yield_pct":metrics["annualized_cash_yield_pct"],
                    "measurement_window_days":config["window_days"],
                })

            taxonomy_meta = taxonomy[(config["layer_id"], config["subcategory_id"])]
            assets.append({
                "id":config["id"], "name":config["name"], "symbol":config["symbol"],
                "category":config["category"], "liquidity":config["liquidity"],
                "quote_currency":config["currency"], "market":config["market"],
                "unit":config.get("unit", "unit"),
                "layer_id":config["layer_id"], "subcategory_id":config["subcategory_id"],
                "risk_level":taxonomy_meta["layer"]["level"],
                "layer_name":taxonomy_meta["layer"]["name"],
                "subcategory_name":taxonomy_meta["subcategory"]["name"],
                "source_cadence":config.get("source_cadence", "daily"),
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
        "taxonomy":CASHFLOW_TAXONOMY,
        "methodology":{
            "high_liquidity_window_days":90,
            "income_asset_window_days":1095,
            "valuation":"Invest 100 at the beginning of the window, hold, do not reinvest distributions, and mark to current value.",
            "option_income":"Current seller-executable bid is mechanically annualized by 365/DTE. BTC and ETH use Deribit; listed US options prefer CBOE delayed Greeks/quotes with a Yahoo fallback. Daily snapshots build the historical estimate series.",
            "staking_income":"ETH staking uses the current Lido stETH 7-day simple-moving-average APR; it is a protocol yield estimate, not a guaranteed return.",
            "best_strategy":"Option matrices rank premium yield divided by |delta|^1.2, adjusted for DTE, IV versus recent realized volatility, and liquidity.",
            "risk_ladder":"Layers 1→4 are a conceptual progression in cashflow complexity, operating dependence, illiquidity and actively sold risk. They are not universal loss-probability ratings.",
        },
        "assets":assets,
        "errors":errors,
    }


def _manual_real_asset_metrics(config: dict, yahoo: YahooProvider) -> tuple[dict, dict]:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / config["data_file"]).read_text())

    frame = pd.DataFrame(
        {"Close": [float(row["price"]) for row in payload["history"]]},
        index=pd.to_datetime([row["date"] for row in payload["history"]], utc=True),
    ).sort_index()

    quote_currency = payload.get("quote_currency", config["currency"]).upper()
    local_current = float(frame["Close"].iloc[-1])
    fx_to_usd = 1.0
    converted = frame.copy()

    if quote_currency != "USD":
        fx = yahoo.history(f"{quote_currency}USD=X", config["window_days"] + 60)
        rates = fx["Close"].reindex(frame.index, method="ffill").bfill()
        if rates.isna().any():
            raise RuntimeError(f"missing FX conversion data for {quote_currency}/USD")
        converted["Close"] = frame["Close"] * rates
        fx_to_usd = float(rates.iloc[-1])

    metrics = manual_income_metrics(
        converted,
        float(payload["annual_cash_yield_pct"]),
        config["window_days"],
        observations_per_year=12.0,
    )
    local_metrics = manual_income_metrics(
        frame,
        float(payload["annual_cash_yield_pct"]),
        config["window_days"],
        observations_per_year=12.0,
    )
    return metrics, {
        "quote_currency": quote_currency,
        "valuation_currency": "USD",
        "local_current_price": local_current,
        "fx_to_usd": fx_to_usd,
        "local_price_return_pct": local_metrics["price_return_pct"],
        "local_annualized_price_return_pct": local_metrics["annualized_price_return_pct"],
        "valuation_unit": payload.get("unit", config.get("unit", "unit")),
        "source": payload.get("source"),
        "source_url": payload.get("source_url"),
        "source_effective_date": payload.get("source_effective_date"),
        "source_cadence": payload.get("source_cadence", config.get("source_cadence", "monthly")),
        "price_basis": payload.get("price_basis"),
        "cashflow_basis": payload.get("cashflow_basis"),
        "notes": payload.get("notes", []),
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
            config.get("crypto_currency", config["symbol"]),
            option_type,
            rv_pct,
            TARGET_DTES,
            TARGET_DELTAS,
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
