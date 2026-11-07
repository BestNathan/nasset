# Metrics: progressive disclosure and semantic clusters

## Design decision

Every metric in nasset Pages follows **summary first, derivation on demand**.

- **Collapsed (default):** name, value, unit, compact qualification. No formulas
  competing with the important numbers. Missing data stays `—`, never 0.
- **Expanded (native `<details>`):** (1) calculation formula / measurement
  definition, (2) what it conveys, (3) interpretation limits and unavailable
  inferences, (4) source when applicable. The card expands **in place**.
  It works with touch, keyboard and assistive technologies; no hover-only UI.
- **Consistent contract:** `site/metric-guide.js` is the source for
  on-page definitions and a single `NassetMetricGuide.wrap(label, body, class)`
  renderer. Numeric KPI cards, company/asset summaries, modeled Greeks and
  option contract field legends share the same interaction.
- **No invented formula:** distinguish observed values and counts from
  calculated estimates. For example, DVOL is exchange-provided and is
  **not** recomputed with a made-up "DVOL" formula.
- **Units and assumptions matter:** always name absolute vs signed
  sensitivity, per-contract vs aggregate, percentage vs volatility point,
  observation window, applicable settlement family, source and missingness.

## Options / derivatives clustering

Product, underlying and settlement family are selectors, **not metric clusters**.
Within an options product the intellectual progression is:

| Group | Metrics | What question it answers |
| --- | --- | --- |
| 1. Volatility level | 30D ATM IV, DVOL, Hourly RV, IV−RV | What volatility is implied vs already observed? |
| 2. Smile and term structure | RR25, BF25, expiry × 10/25Δ IV, ATM tenor curve | Where are relative call/put wing premiums, and how do they change with tenor? |
| 3. First-order option sensitivity | Model Delta, Vega | How do option values initially respond to forward price or IV? |
| 4. Higher-order and time sensitivity | Gamma, Vanna, Volga, Charm | How does the sensitivity itself change with price, IV and calendar time? |
| 5. Open interest and distribution | OI, Put/Call OI, expiry concentration | Where is *public* outstanding contract volume concentrated? |
| 6. Gross aggregate stress | OI-weighted Gamma, Vanna, Vega, Volga, Charm, Gamma Wall, ≤7D Gamma, spot shocks | What is the size and concentration of model sensitivity *if all OI were long*? |

The visible top-of-page options clusters are **volatility level**, **smile/skew**,
and **open interest/convexity**. Existing Volatility Surface, Convexity &
Positioning and Option Chain & Greeks panels preserve their domain-specific
visualizations; users can expand an individual indicator rather than opening
one enormous generic glossary. The option chain has a compact row of
disclosures for Mark IV, Delta, Gamma, Vanna, Volga, Charm and OI.

Important semantic boundaries:

- **Mark IV** is a native Deribit observation; **Black-76 Greeks** are modeled.
- **Public OI** does not identify whether the holder is long or short;
  **Gross Gamma** here is not dealer net GEX, hedge-flow estimates or a
  predictor of support/resistance.
- Coin-margined/inverse and USDC-linear markets are not merged or netted.
- The benchmark `Gamma Wall` is the strike with the largest *gross modeled*
  Gamma contribution, not a "gamma flip" or a price wall.
- PAXG's currently supported Deribit product is futures/perpetuals; no
  fictitious option sensitivities are shown.

Futures use separate **reference price/listing** and
**term/basis/observation** clusters. The annualized basis is simple
mechanical annualization, not tradeable carry after costs.

## Pages-wide adoption

The same metric pattern is applied to the numeric summary and detail cards in
the **asset/cashflow** and **dividend equities** domains. Chart sections,
strategy/matrix values and tabular headers can call the same registry as their
representations are extended; avoid separate one-off tooltip schemas.

### Maintenance and acceptance

1. Each newly introduced metric needs a stable label and a corresponding
   definition with a formula/measurement rule, meaning and boundary.
   Definitions must agree with current `src/nasset` algorithms and sources.
2. Explain **the actual model**, including its simplifying assumptions, rather
   than interpreting a single numeric result as an investment recommendation.
3. Use native expand/collapse, keyboard/touch support, and escape dynamic
   labels and formulas. The collapsed dashboard remains compact on mobile.
4. Preserve production Deribit **self-contained HTML**: its shared metric
   definitions, JS and immutable JSON must be embedded by
   `scripts/stamp_pages_assets.py`, with no additional network fetch.
5. Re-test DOM rendering with real archive data and production Pages
   post-deploy verification whenever the shared metrics library changes.

Related: [Pages information architecture](pages-information-architecture.md),
[Deribit formulas and assumptions](deribit-pages.md).
