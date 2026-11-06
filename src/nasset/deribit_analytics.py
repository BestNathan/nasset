"""Research-grade derived surfaces and OI-weighted gross risk sensitivities.

All option Greeks use undiscounted Black-76 forward convention, F from the
exchange's underlying_price, and mark_iv percent / 100. This models a single
long option, NOT exchange-reported Greeks. Open interest does NOT identify the
net position of dealers, customers or any account. All risk measures are
unsigned gross potential sensitivities, never "dealer net GEX" or flow signals.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import erf, exp, isfinite, log, pi, sqrt
from statistics import median

YEAR_DAYS = 365.0
TENORS = (7, 14, 30, 60, 90, 180)
SURFACE_COLUMNS = (
    ("10p", "put", -0.10),
    ("25p", "put", -0.25),
    ("atm", "atm", 0.50),
    ("25c", "call", 0.25),
    ("10c", "call", 0.10),
)
STABLES = frozenset({"USDC", "USDT", "USD"})


def _number(value, *, positive=False, zero_ok=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not isfinite(result):
        return None
    if positive and not (result > 0 or (zero_ok and result == 0)):
        return None
    return result


def _safe(value):
    return value if value is None or isfinite(value) else None


def _norm_cdf(z):
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def _norm_pdf(z):
    return exp(-z * z / 2.0) / sqrt(2.0 * pi)


def black76(forward, strike, volatility, years, kind):
    """Greeks per ONE underlying asset with constant forward, discount=1.

    delta: ∂V/∂F (call positive, put negative)
    gamma: ∂delta/∂F, USD^-1
    vega: ∂V/∂sigma, USD / vol fraction
    vanna: ∂delta/∂sigma, per vol fraction
    volga: ∂vega/∂sigma, USD / vol fraction squared
    charm_day: change in delta as one calendar day passes, holding F/IV fixed
    zomma: ∂gamma/∂sigma
    speed: ∂gamma/∂F
    """
    f, k, sigma, t = (_number(forward, positive=True), _number(strike, positive=True),
                      _number(volatility, positive=True), _number(years, positive=True))
    if None in (f, k, sigma, t) or kind not in {"call", "put"}:
        return None
    if not (0 < t <= 5 and .01 <= sigma <= 4 and 1e-5 < f / k < 1e5):
        return None
    try:
        root = sqrt(t)
        d1 = log(f / k) / (sigma * root) + 0.5 * sigma * root
        d2 = d1 - sigma * root
        density = _norm_pdf(d1)
        delta = _norm_cdf(d1) - (1 if kind == "put" else 0)
        gamma = density / (f * sigma * root)
        vega = f * density * root
        vanna = -density * d2 / sigma
        volga = vega * d1 * d2 / sigma
        dd1_dt = -(log(f / k) / (2 * sigma * t ** 1.5)) + sigma / (4 * root)
        charm_day = -density * dd1_dt / YEAR_DAYS
        zomma = gamma * ((d1 * d2 - 1) / sigma)
        speed = -gamma / f * (1 + d1 / (sigma * root))
        result = dict(delta=delta, gamma=gamma, vega=vega, vanna=vanna,
                      volga=volga, charm_day=charm_day, zomma=zomma, speed=speed)
        return result if all(isfinite(n) for n in result.values()) else None
    except (OverflowError, ValueError, ZeroDivisionError):
        return None


def _as_of(row, fallback):
    captured = row.get("captured_at")
    if isinstance(captured, str):
        try:
            parsed = datetime.fromisoformat(captured.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return fallback


def _family(row, asset):
    settlement = row.get("settlement_currency")
    if settlement == asset:
        return "inverse"
    if settlement in STABLES:
        return "linear"
    return None


def _option(row, as_of):
    asset = row.get("base_currency")
    if asset not in ("BTC", "ETH"):
        return None
    family = _family(row, asset)
    kind = row.get("option_type")
    f = _number(row.get("underlying_price"), positive=True)
    strike = _number(row.get("strike"), positive=True)
    iv_pct = _number(row.get("mark_iv"), positive=True)
    expiry = _number(row.get("expiration_timestamp"), positive=True)
    if None in (family, f, strike, iv_pct, expiry) or kind not in ("call", "put"):
        return None
    capture_time = _as_of(row, as_of)
    years = (expiry / 1000.0 - capture_time.timestamp()) / (86400 * YEAR_DAYS)
    greeks = black76(f, strike, iv_pct / 100.0, years, kind)
    if greeks is None:
        return None
    oi = _number(row.get("open_interest"), positive=True, zero_ok=True)
    if oi is None:
        oi = None
    return {
        "instrument": row.get("instrument_name"),
        "asset": asset, "family": family, "side": kind, "strike": strike,
        "expiry": datetime.fromtimestamp(expiry / 1000, tz=timezone.utc).isoformat(),
        "expiry_ms": int(expiry), "forward": f, "iv": iv_pct, "t": years,
        "days": years * YEAR_DAYS, "oi": oi, "greeks": greeks,
    }


def _interpolate(points, target, *, max_gap=0.35):
    """Only interpolate between two observed quote deltas, never extrapolate."""
    if not points:
        return None
    ordered = sorted(points)
    nearest = min(ordered, key=lambda p: abs(p[0] - target))
    if abs(nearest[0] - target) < 1e-9:
        return nearest[1]
    for (left, lv), (right, rv) in zip(ordered, ordered[1:]):
        if left < target < right and right - left <= max_gap:
            fraction = (target - left) / (right - left)
            return lv + (rv - lv) * fraction
    return None


def _expiry_slice(rows):
    calls = [(r["greeks"]["delta"], r["iv"]) for r in rows if r["side"] == "call"]
    puts = [(r["greeks"]["delta"], r["iv"]) for r in rows if r["side"] == "put"]
    forward = median(r["forward"] for r in rows)
    atms = [r for r in rows if abs(log(r["strike"] / r["forward"])) <= .065]
    nearest = min((abs(log(r["strike"] / r["forward"])) for r in rows), default=1)
    if not atms and nearest <= .10:
        atms = [r for r in rows if abs(log(r["strike"] / r["forward"])) <= nearest + 1e-7]
    atm = (sum(r["iv"] for r in atms) / len(atms)) if atms else None
    nodes = {}
    for name, side, target in SURFACE_COLUMNS:
        nodes[name] = (atm if side == "atm" else
                       _interpolate(puts if side == "put" else calls, target))
    rr = nodes["25c"] - nodes["25p"] if None not in (nodes["25c"], nodes["25p"]) else None
    fly = ((nodes["25c"] + nodes["25p"]) / 2 - atm
           if None not in (nodes["25c"], nodes["25p"], atm) else None)
    return dict(forward=forward, atm=atm, rr25=rr, bf25=fly, nodes=nodes)


def _linear_term(slices, days, field, *, max_days=65):
    valid = sorted((item["days"], item.get(field)) for item in slices
                   if item.get(field) is not None and item["days"] > 0)
    if not valid:
        return None
    if any(abs(d - days) < 1e-6 for d, _ in valid):
        return next(v for d, v in valid if abs(d - days) < 1e-6)
    for (left, lv), (right, rv) in zip(valid, valid[1:]):
        if left <= days <= right and right - left <= max_days:
            return lv + (rv - lv) * (days - left) / (right - left)
    return None


def _risk(rows, reference):
    """OI assumes underlying-asset units for BTC/ETH inverse and 1x linear.

    Gross long-all sensitivity is a market-concentration STRESS MEASURE;
    dealer long/short OI is not observable, so net hedging flow is unknowable.
    """
    groups = defaultdict(lambda: defaultdict(float))
    totals = defaultdict(float)
    scenario = {shift: 0.0 for shift in (-10, -5, -2, 2, 5, 10)}
    known = [r for r in rows if r["oi"] is not None]
    for r in known:
        f, oi, greek = r["forward"], r["oi"], r["greeks"]
        base = r["strike"]
        gamma = oi * f * f * greek["gamma"] * .01
        vega = oi * greek["vega"] * .01
        vanna = oi * f * abs(greek["vanna"]) * .01
        charm = oi * f * abs(greek["charm_day"])
        volga = oi * abs(greek["volga"]) * (.01 ** 2) / 2.0
        key = (r["expiry"], base)
        bucket = groups[key]
        bucket["gamma_1pct_usd"] += gamma
        bucket["vega_1volpt_usd"] += vega
        bucket["vanna_1volpt_delta_usd"] += vanna
        bucket["charm_1day_delta_usd"] += charm
        bucket["volga_1volpt2_usd"] += volga
        bucket["oi_units"] += oi
        bucket[r["side"] + "_oi"] += oi
        totals["oi_units"] += oi
        totals["gamma_1pct_usd"] += gamma
        totals["vega_1volpt_usd"] += vega
        totals["vanna_1volpt_delta_usd"] += vanna
        totals["charm_1day_delta_usd"] += charm
        totals["volga_1volpt2_usd"] += volga
        totals[r["side"] + "_oi"] += oi
        if r["days"] <= 7:
            totals["gamma_7d_usd"] += gamma
        for shift in scenario:
            moved = black76(f * (1 + shift / 100.0), base, r["iv"] / 100, r["t"], r["side"])
            if moved is not None:
                scenario[shift] += oi * f * abs(moved["delta"] - greek["delta"])
    at_strike = defaultdict(lambda: defaultdict(float))
    at_expiry = defaultdict(lambda: defaultdict(float))
    for (expiry, strike), r in groups.items():
        for metric, value in r.items():
            at_strike[strike][metric] += value
            at_expiry[expiry][metric] += value
    sorted_strikes = sorted(at_strike.items(), key=lambda it: it[1]["gamma_1pct_usd"], reverse=True)
    sum_gamma = totals["gamma_1pct_usd"]
    top_strike = sorted_strikes[0][0] if sorted_strikes else None
    return {
        "basis": "gross_long_oi_black76_no_dealer_sign",
        "oi_contracts_with_data": len(known),
        "reference_price_usd": reference,
        "put_call_oi_ratio": (totals["put_oi"] / totals["call_oi"]
                              if totals["call_oi"] > 0 else None),
        "metrics": {name: _safe(value) for name, value in totals.items()},
        "gamma_7d_share_pct": 100 * totals["gamma_7d_usd"] / sum_gamma if sum_gamma else None,
        "gamma_max_strike": top_strike,
        "strikes": [
            {"strike": strike, **{k: _safe(v) for k, v in data.items()}}
            for strike, data in sorted_strikes[:40]
        ],
        "expiries": [
            {"expiry": expiry, **{k: _safe(v) for k, v in data.items()}}
            for expiry, data in sorted(at_expiry.items())
        ],
        "spot_shock": [
            {"move_pct": move, "gross_delta_rehedge_usd": _safe(value)}
            for move, value in sorted(scenario.items())
        ],
    }


def _asset_market(rows, asset, reference):
    grouped = defaultdict(list)
    rejected = 0
    for row in rows:
        if row.get("base_currency") != asset:
            continue
        result = _option(row, reference["as_of"])
        if result is None:
            rejected += 1
        else:
            grouped[result["family"]].append(result)
    products = {}
    for family, opts in grouped.items():
        slices = []
        by_expiry = defaultdict(list)
        for opt in opts:
            by_expiry[opt["expiry"]].append(opt)
        for expiry, contracts in sorted(by_expiry.items()):
            surface = _expiry_slice(contracts)
            slices.append({
                "expiry": expiry,
                "days": round(median(c["days"] for c in contracts), 3),
                "contracts": len(contracts),
                "open_interest_units": sum(c["oi"] or 0 for c in contracts),
                **surface,
            })
        tenors = []
        for days in TENORS:
            tenors.append({
                "target_days": days, "atm": _linear_term(slices, days, "atm"),
                "rr25": _linear_term(slices, days, "rr25"),
                "bf25": _linear_term(slices, days, "bf25"),
            })
        risk = _risk(opts, reference["price"])
        products[family] = {
            "contracts": len(opts), "rejected": rejected,
            "expiries": slices, "tenors": tenors,
            "risk": risk,
        }
    return products


def _index_history(history, asset):
    points = []
    for entry in history:
        indexes = [row for row in entry.get("indexes", ())
                   if row.get("base_currency") == asset]
        candidates = sorted(indexes, key=lambda r: (
            not r.get("index_name", "").endswith("_usd"),
            not r.get("index_name", "").endswith("_usdc"),
        ))
        if not candidates:
            continue
        px = _number(candidates[0].get("index_price"), positive=True)
        if px is not None:
            points.append((entry["slot"], px, candidates[0].get("index_name")))
    return points


def _realized_vol(points):
    """Sample standard deviation of true consecutive 1h log returns, 365d basis.

    With fewer than 12 valid hourly returns the metric is withheld; gaps
    are never assumed to be zero or blended into multi-hour returns.
    """
    returns = []
    for (time_a, price_a, source_a), (time_b, price_b, source_b) in zip(points, points[1:]):
        try:
            delta = (datetime.fromisoformat(time_b) - datetime.fromisoformat(time_a)).total_seconds()
        except (TypeError, ValueError):
            continue
        if delta == 3600 and source_a == source_b and price_a > 0 and price_b > 0:
            returns.append(log(price_b / price_a))
    if len(returns) < 12:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return sqrt(var * 24 * 365) * 100


def _futures_curve(rows, asset, index_prices, as_of):
    points = []
    for row in rows:
        if row.get("base_currency") != asset:
            continue
        name = row.get("instrument_name", "")
        mark = _number(row.get("mark_price"), positive=True)
        expiry = _number(row.get("expiration_timestamp"), positive=True)
        family = _family(row, asset)
        if not name or mark is None or family is None:
            continue
        # Inverse/linear futures quote in USD/USDC, not settlement units.
        # Prefer matching quote-currency reference index for the product.
        suffix = "_usdc" if family == "linear" else "_usd"
        idx = next((r for r in index_prices if r.get("base_currency") == asset and
                    str(r.get("index_name", "")).endswith(suffix)), None)
        reference = _number(idx.get("index_price"), positive=True) if idx else None
        days = max(0, (expiry / 1000.0 - as_of.timestamp()) / 86400) if expiry else None
        is_perpetual = "PERPETUAL" in name
        if is_perpetual:
            days = None
        annualized = ((mark / reference - 1) * 365 / days * 100
                      if reference is not None and days is not None and days >= 1 else None)
        points.append({
            "instrument": name, "family": family, "expiry": (
                datetime.fromtimestamp(expiry / 1000, tz=timezone.utc).isoformat()
                if expiry and not is_perpetual else None),
            "days": days, "mark": mark, "reference": reference,
            "index_name": idx.get("index_name") if idx else None,
            "basis_pct": (mark / reference - 1) * 100 if reference else None,
            "annualized_basis_pct": annualized,
        })
    return sorted(points, key=lambda r: (r["family"], r["days"] is None,
                                          r["days"] if r["days"] is not None else 1e6))


def derive_analytics(latest, history, latest_slot):
    """Return JSON-safe analytics for the latest verified market-data snapshot."""
    if not latest_slot:
        return {"version": 1, "assets": {}, "as_of": None}
    as_of = datetime.fromisoformat(latest_slot.replace("Z", "+00:00"))
    index_prices = latest.get("index_price") or []
    results = {}
    for asset in ("BTC", "ETH", "PAXG"):
        points = _index_history(history, asset)
        last = points[-1] if points else None
        reference = {"price": last[1] if last else None, "as_of": as_of}
        products = (_asset_market(latest.get("option_chain", ()), asset, reference)
                    if asset != "PAXG" else {})
        rv = _realized_vol(points)
        dvol = next((_number(r.get("close")) for r in reversed(latest.get("volatility_index", ()))
                     if r.get("base_currency") == asset), None)
        forward_curve = _futures_curve(latest.get("futures_curve", ()), asset, index_prices, as_of)
        # All families remain separate; no inverse/USDC risk netting.
        results[asset] = {
            "reference_price": reference["price"],
            "reference_name": last[2] if last else None,
            "rv_hourly_annualized_pct": rv,
            "dvol_pct": dvol,
            "markets": products,
            "futures": forward_curve,
        }
    return {"version": 1, "as_of": latest_slot, "assets": results,
            "model": "Black-76 forward Greeks, no discount, fixed per-contract IV",
            "position_scope": "public OI only; no market maker inventory or dealer direction"}
