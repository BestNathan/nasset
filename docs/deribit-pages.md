# Deribit quantitative derivatives research on nasset Pages

The user-facing [Derivatives Research terminal](../site/deribit.html) emphasizes
**economically meaningful derivatives analytics**, not data ingestion operations.

## Research panels

1. **Volatility Surface** (BTC and ETH, separate coin-margined/inverse and
   USDC-linear markets): expiry × Black-76 forward-Delta IV surface with
   tenors 10P / 25P / ATM / 25C / 10C; 7D–180D ATM term structure,
   25-Delta risk reversal (call minus put), 25-Delta butterfly (average wings
   minus ATM), DVOL and independent observed realized volatility (RV).
   Surface nodes are interpolated only *inside* genuinely bracketed quote
   deltas. No manufactured 30D values when expiry endpoints are absent.
2. **Convexity & Positioning**: OI-weighted *gross long-all* Gamma, Vanna,
   Volga, Charm and Vega, Gamma concentration by strike and expiry, put/call
   OI, share of <=7D Gamma, and symmetric ±2/±5/±10% underlying repricing
   scenarios. All figures are **model sensitivities, not observed positions
   or dealer-hedging flow**.
3. **Futures Basis Surface**: hourly observation × target tenor basis
   heatmap at 7/30/60/90/180 days, annualized basis curve across actual
   listed expiries and an individual contract table. Spot reference is
   matched separately for USD inverse and USDC linear products; no interpolation
   across missing expiries/quotes or crossing settlement families. PAXG
   supports its actual listed perpetual/futures reference data without
   pretending to have gold options or DVOL.
4. **Option Chain & Greeks**: the individual BTC/ETH quote chain filtered
   by option type, remaining days and instrument, with model Delta/Gamma/
   Vanna/Volga/Charm available beside the exchange mark IV and open interest.
   Each model Greek is labelled, never represented as source-native.

## Inputs and contract identity

Only [verified immutable Parquet](../scripts/build_deribit_dashboard.py)
from the separate `market-data` Git branch is used. It contains
`instrument_name`, `base_currency`, `settlement_currency`,
`option_type`, `expiration_timestamp`, `strike`, `mark_iv`,
`underlying_price`, `open_interest`, bid/ask and futures/index data.
`mark_iv` is percent; the model converts to a fraction. Captured timestamps
are read directly from the file; no option is priced against a fabricated
intraday sampling time. Expired, zero-IV, invalid or out-of-range quotes are
rejected from the derived surface.

Per Deribit specifications, BTC/ETH coin-settled inverse options and
BTC/ETH USDC-linear options use **different pricing and settlement units**,
even when strikes/expiries coincide. Group by `settlement_currency`; use
the underlying native forward `underlying_price` of each option; never
mix cross-family OI or quote prices. BTC/ETH options use a multiplier of
one underlying unit (Deribit contract specifications). If additional assets
or multiplier schemes are added, explicitly redesign unit normalization.

References:
- [Deribit inverse option specifications](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options)
- [Deribit linear USDC option specifications](https://support.deribit.com/hc/en-us/articles/31424932728093-Linear-USDC-Options)
- [Deribit volatility index](https://support.deribit.com/hc/en-us/articles/25944666401309-DVOL-volatility-index)

## Analytical formulas, scaling and limitations

Use **undiscounted Black-76** (discount factor 1) on the individual option's
Deribit forward price F, strike K, annualized sigma and remaining calendar
years T. These are a *consistent theoretical approximation*, not
Deribit's own margin/pricing engine or native Greeks:

```text
d1     = [ln(F/K) + 0.5*sigma^2*T] / [sigma*sqrt(T)]
d2     = d1 - sigma*sqrt(T)
Delta  = N(d1) for call; N(d1)-1 for put
Gamma  = phi(d1) / [F*sigma*sqrt(T)]
Vega   = F*phi(d1)*sqrt(T)
Vanna  = -phi(d1)*d2 / sigma                      (d Delta / d sigma)
Volga  = Vega*d1*d2 / sigma                       (d Vega / d sigma)
Charm  = -phi(d1)*d(d1)/dT / 365                 (calendar-day Delta decay)
Zomma  = Gamma*(d1*d2-1)/sigma
Speed  = -Gamma/F * (1 + d1/[sigma*sqrt(T)])
```

Vanna, Volga and Gamma are second-order derivatives. Charm is a
cross-derivative of price with respect to spot/forward and time; Zomma
and Speed are third-order. Tests check derivatives through symmetric
finite differences, across call/put, ITM/ATM/OTM and multiple expiries.

For each instrument with public OI quantity q (underlying units), the
aggregate **gross long-all** sensitivities are:

```text
Gross Gamma / +1% F     = sum(q * F^2 * Gamma * 0.01)         [USD Delta change]
Gross Vega / +1 vol pt  = sum(q * Vega * 0.01)               [USD option price]
Gross Vanna / 1 vol pt  = sum(q * F * abs(Vanna) * 0.01)      [USD Delta absolute change]
Gross Charm / 1 day     = sum(q * F * abs(Charm_day))         [USD Delta absolute drift]
Gross Volga / 1 vol pt² = sum(q * abs(Volga) * 0.01² / 2)   [USD second-order term]
Spot scenario +/-x%     = sum(q * F * abs(Delta(F*(1+x))-Delta(F))) [USD absolute delta repricing]
```

These metrics are **not net dealer Gamma Exposure (GEX)**.
Public OI has no long-vs-short owner information and can be delayed by the
exchange. Consequently nasset does not assign dealers long calls / short
puts, does not invent "Gamma Flip", does not label a Gamma strike as support
or resistance and does not claim actual dealer hedging volume or direction.
The "Gamma Wall" is only the strike containing maximal **gross sensitivity**
for the chosen asset and separately selected settlement family. A source
of signed dealer/customer positioning would require a separate data
contract and explicit uncertainty/coverage.

The 25D surface uses Black-76 **forward delta** computed from each
observed IV. For each expiry, sort real put/call IV quotes by model delta,
interpolate strictly between available bracketing deltas (maximum gap
0.35). No extrapolation. The ATM point is the mean mark IV of options with
strike close to the expiry forward, not a guaranteed standardized 50D
quote; it is clearly labelled as near-forward in the UI. Tenor interpolation
is also bounded by neighboring valid expiries. No global curve smoothing
or "arbitrage-free" claim without calibration/validation.

The realized-vol estimate uses *only* consecutive hourly **index_name**
observations, not arbitrary gaps or mixed USD/USDC references; requires
12 consecutive-return samples and uses sample variance ×24×365 annualization.
It is a short-window realized volatility, not a 30-day historical RV.
Do not interpret 30D ATM-IV minus this estimate as a maturity-matched
variance risk premium.

Futures basis = (future_mark / matched_index - 1) × 100%, and annualized
basis = raw_basis × 365 / days_remaining, only for remaining maturity
at least one day. Futures quotations and annualized bases use the same
USD / USDC reference family; perpetual contracts have **no synthetic
expiry-based annualized basis**. Interpolate target 7/30/60/90/180-day
values only between actual dated futures, never extrapolate. PAXG
perpetual prices remain visible when no dated-futures surface exists.

## Data and runtime

`scripts/build_deribit_dashboard.py` pins a real market-data Git commit,
validates each included UTC slot's Receipt/Manifest Git SHA inventory,
reads typed Parquet and computes `analytics` using
`src/nasset/deribit_analytics.py`. It produces a static JSON projection
consumed by `site/deribit.js`, which renders responsive SVGs and heatmaps.
No live API credentials or historical data fabrication exist in the browser.
The normal Pages deployment and successful hourly archive `workflow_run`
refresh the research view automatically.

Failure to read or calculate required upstream data aborts the projection
and leaves the old Pages deployment intact. On missing expiry/strike quote
nodes, derived metrics are JSON null and the UI displays a dash.

## Review checklist

- Inspect real Pages JSON with BTC and ETH inverse/USDC books and PAXG
  futures after deployment.
- Numerically test model Greeks with finite-difference perturbations and
  invalid / near-expiry inputs.
- Verify no mark IV is misconstrued as an executable price or all OI
  as actual dealer net positioning.
- Confirm instrument-family isolation and matched USD/USDC futures
  reference data.
- Verify missing IV surface nodes and basis gaps never interpolate
  outside supported ranges.
