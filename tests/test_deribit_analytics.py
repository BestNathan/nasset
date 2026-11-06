"""Analytic derivatives research contracts: finite differences, data isolation, risk semantics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import erf, exp, isfinite, log, pi, sqrt

import pytest

from nasset.deribit_analytics import black76, derive_analytics, _realized_vol

INSTANT = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


def model_price(f, k, sigma, years, kind):
    d1 = (log(f / k) + .5 * sigma * sigma * years) / (sigma * sqrt(years))
    d2 = d1 - sigma * sqrt(years)
    cdf = lambda x: (1 + erf(x / sqrt(2))) / 2
    if kind == "call":
        return f * cdf(d1) - k * cdf(d2)
    return k * cdf(-d2) - f * cdf(-d1)


@pytest.mark.parametrize("side", ["put", "call"])
@pytest.mark.parametrize("forward,strike,years,vol", [
    (100, 100, 30/365, .52),
    (100, 80, 7/365, .45),
    (100, 125, 90/365, .61),
    (100, 110, 365/365, .30),
])
def test_black76_greeks_agree_with_finite_differences(side, forward, strike, years, vol):
    d = black76(forward, strike, vol, years, side)
    assert d is not None and all(isfinite(v) for v in d.values())
    assert d["gamma"] > 0 and d["vega"] > 0
    eps_s = forward * .0001
    eps_v = 1e-4
    eps_t = 1/365/100
    delta_numerical = (model_price(forward+eps_s,strike,vol,years,side)
                      -model_price(forward-eps_s,strike,vol,years,side))/(2*eps_s)
    gamma_numerical = (model_price(forward+eps_s,strike,vol,years,side)
                      -2*model_price(forward,strike,vol,years,side)
                      +model_price(forward-eps_s,strike,vol,years,side))/(eps_s**2)
    vega_numerical = (model_price(forward,strike,vol+eps_v,years,side)
                     -model_price(forward,strike,vol-eps_v,years,side))/(2*eps_v)
    def delta_with(volatility, time=years):
        e = black76(forward,strike,volatility,time,side)
        return e["delta"]
    vanna_numerical = (delta_with(vol+eps_v)-delta_with(vol-eps_v))/(2*eps_v)
    volga_numerical = (black76(forward,strike,vol+eps_v,years,side)["vega"]
                       -black76(forward,strike,vol-eps_v,years,side)["vega"])/(2*eps_v)
    charm_numerical = -(delta_with(vol,years+eps_t)-delta_with(vol,years-eps_t))/(2*eps_t)/365
    assert d["delta"] == pytest.approx(delta_numerical, abs=1e-5)
    assert d["gamma"] == pytest.approx(gamma_numerical, rel=1e-5,abs=1e-7)
    assert d["vega"] == pytest.approx(vega_numerical,rel=1e-5)
    assert d["vanna"] == pytest.approx(vanna_numerical,rel=1e-5,abs=1e-7)
    assert d["volga"] == pytest.approx(volga_numerical,rel=1e-5,abs=1e-6)
    assert d["charm_day"] == pytest.approx(charm_numerical,rel=1e-5,abs=1e-8)


def test_black76_fails_closed_for_invalid_inputs():
    for v in (0, None, float("nan"),float("inf"),-1):
        assert black76(100,100,v,30/365,"call") is None
    assert black76(100,100,.5,0,"call") is None
    assert black76(100,100,.5,30/365,"wrong") is None
    assert black76(100,1e12,.5,30/365,"call") is None


def market_chain(asset="BTC", family="BTC", oi=10.0):
    rows=[]
    for days in (21,45,75):
        expiry=INSTANT+timedelta(days=days)
        for kind in ("call","put"):
            for strike in (70,80,90,95,100,105,110,120,130):
                rows.append({
                    "base_currency": asset,
                    "instrument_name": f"{asset}-{days}-{strike}-{kind}",
                    "option_type": kind, "settlement_currency": family,
                    "strike": strike, "expiration_timestamp": int(expiry.timestamp()*1000),
                    "underlying_price": 100.0,
                    "mark_iv": 45 + (8 if kind=="put" else 0) + abs(strike-100)*.15,
                    "open_interest": oi,
                    "captured_at": INSTANT.isoformat(),
                })
    return rows


def reference_data():
    history=[{
        "slot":INSTANT.isoformat(),
        "indexes":[{"base_currency":"BTC","index_name":"btc_usd","index_price":100.0},
                   {"base_currency":"BTC","index_name":"btc_usdc","index_price":101.0}],
        "dvol":[{"base_currency":"BTC","close":47.2}],
    }]
    return history


def test_surface_tenor_skew_and_risk_are_derived_and_segregated():
    inv=market_chain()
    usdc=market_chain(family="USDC",oi=3.0)
    latest={
        "option_chain": inv+usdc,
        "index_price": reference_data()[0]["indexes"],
        "volatility_index":reference_data()[0]["dvol"],
        "futures_curve":[{
            "base_currency":"BTC","instrument_name":"BTC-DEC26",
            "settlement_currency":"BTC","mark_price":103.0,
            "expiration_timestamp":int((INSTANT+timedelta(days=30)).timestamp()*1000),
        },{
            "base_currency":"BTC","instrument_name":"BTC_USDC-DEC26",
            "settlement_currency":"USDC","mark_price":103.5,
            "expiration_timestamp":int((INSTANT+timedelta(days=30)).timestamp()*1000),
        }],
    }
    a=derive_analytics(latest,reference_data(),INSTANT.isoformat())
    assert a["version"]==1
    asset=a["assets"]["BTC"]
    assert asset["dvol_pct"]==47.2
    assert asset["reference_price"]==100.0
    assert set(asset["markets"])=={"inverse","linear"}
    inverse=asset["markets"]["inverse"]
    linear=asset["markets"]["linear"]
    assert inverse["contracts"]==54
    assert linear["contracts"]==54
    assert len(inverse["expiries"])==3
    first=inverse["expiries"][0]
    assert first["nodes"]["atm"] is not None
    assert first["nodes"]["25p"] is not None
    assert first["nodes"]["25c"] is not None
    assert first["rr25"] < 0  # synthetic put skew is higher IV
    assert first["bf25"] is not None
    assert any(x["target_days"]==30 and x["atm"] is not None for x in inverse["tenors"])
    risk=inverse["risk"]
    assert risk["basis"]=="gross_long_oi_black76_no_dealer_sign"
    assert risk["metrics"]["gamma_1pct_usd"] > 0
    assert risk["metrics"]["vega_1volpt_usd"] > 0
    assert risk["metrics"]["vanna_1volpt_delta_usd"] > 0
    assert risk["metrics"]["charm_1day_delta_usd"] > 0
    assert risk["oi_contracts_with_data"]==54
    assert len(risk["spot_shock"])==6
    assert risk["gamma_max_strike"] is not None
    assert risk["metrics"]["gamma_1pct_usd"] > linear["risk"]["metrics"]["gamma_1pct_usd"]
    assert asset["futures"][0]["annualized_basis_pct"]==pytest.approx(36.5,rel=1e-6)
    assert asset["futures"][1]["annualized_basis_pct"]==pytest.approx(
        ((103.5/101)-1)*365/30*100,rel=1e-6
    )
    assert a["assets"]["PAXG"]["markets"]=={}
    assert a["position_scope"].startswith("public OI only")


def test_risk_scales_with_oi_and_never_invents_dealer_sign():
    def calculate(oi):
        latest={"option_chain":market_chain(oi=oi),"index_price":reference_data()[0]["indexes"],
                "volatility_index":[],"futures_curve":[]}
        return derive_analytics(latest,reference_data(),INSTANT.isoformat())["assets"]["BTC"]["markets"]["inverse"]["risk"]
    small,large=calculate(5),calculate(10)
    for metric in ("oi_units","gamma_1pct_usd","vega_1volpt_usd",
                   "vanna_1volpt_delta_usd","charm_1day_delta_usd","volga_1volpt2_usd"):
        assert large["metrics"][metric]==pytest.approx(2*small["metrics"][metric])
    assert large["basis"]!="observed_dealer_net_position"
    assert large["spot_shock"][0]["gross_delta_rehedge_usd"]==pytest.approx(
        2*small["spot_shock"][0]["gross_delta_rehedge_usd"]
    )


def test_insufficient_quotes_leave_surface_missing_and_bad_iv_ignored():
    chain=market_chain()
    only=[r for r in chain if r["strike"]==100]
    latest={"option_chain":only,"index_price":reference_data()[0]["indexes"],
            "volatility_index":[],"futures_curve":[]}
    market=derive_analytics(latest,reference_data(),INSTANT.isoformat())["assets"]["BTC"]["markets"]["inverse"]
    assert market["expiries"][0]["nodes"]["25p"] is None
    assert market["expiries"][0]["nodes"]["10c"] is None
    assert market["expiries"][0]["rr25"] is None
    assert market["expiries"][0]["nodes"]["atm"] is not None
    expected_valid=len(only)
    bad=dict(only[0],mark_iv=float("nan"))
    latest["option_chain"].append(bad)
    market2=derive_analytics(latest,reference_data(),INSTANT.isoformat())["assets"]["BTC"]["markets"]["inverse"]
    assert market2["contracts"]==expected_valid


def test_annualized_realized_vol_never_interpolates_missing_hours():
    pts=[((INSTANT+timedelta(hours=i)).isoformat(),100+i*.2, "btc_usd") for i in range(24)]
    rv=_realized_vol(pts)
    assert rv is not None and rv > 0
    assert _realized_vol(pts[:10]) is None
    gaps=[p for i,p in enumerate(pts) if i % 2==0]
    assert _realized_vol(gaps) is None
    switched=[(time,p,"btc_usdc" if i%2 else "btc_usd") for i,(time,p,name) in enumerate(pts)]
    assert _realized_vol(switched) is None
