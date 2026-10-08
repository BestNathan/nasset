# nasset

Daily **asset → cashflow** analytics.

nasset keeps three questions separate:

1. **What happened to principal?** Buy once at the beginning of the measurement window and mark the same position today.
2. **How much cashflow did / can the asset produce?** Historical assets use actual distributions; option strategies use current seller-executable premium and mechanical annualization.
3. **Which option-income cell is attractive now?** Strategies are rendered as DTE × Delta matrices with a transparent risk-adjusted ranking.

## Cashflow risk ladder

nasset organizes assets by the **source and complexity of cashflow**, with a conceptual risk progression:

1. **Contractual Income** — cash / short rates, sovereign bonds, investment-grade credit, senior structured credit and high-yield credit.
2. **Productive Distribution** — dividend equities & banks, preferred/hybrid, listed REITs, BDC/private credit, infrastructure & utilities.
3. **Illiquid Real Assets** — direct property, farmland, timberland, royalties and private real assets.
4. **Engineered Yield** — option overlays, staking, lending/carry and basis/relative-value strategies.

This is not a universal probability-of-loss rating. Long-duration sovereign bonds can still have large mark-to-market drawdowns; the ladder instead describes increasing dependence on operating results, illiquidity and actively sold risk.

## Assets in v0.1

| Asset | Proxy / venue | Window | Cashflow model |
|---|---|---:|---|
| Bitcoin | Deribit BTC | 90D | covered call + cash-secured put matrices |
| Ethereum | Deribit ETH + Lido | 90D | staking + covered call + cash-secured put matrices |
| S&P 500 | SPY / CBOE | 90D | covered call + cash-secured put matrices |
| Gold | GLD / CBOE | 90D | covered call + cash-secured put matrices |
| US 0–3M Treasury | SGOV | 3Y | distributions |
| US long Treasury | TLT | 3Y | distributions |
| China 5Y government bond | 511010.SS | 3Y | distributions |
| US investment-grade credit | LQD | 3Y | distributions |
| AAA CLO | JAAA | 3Y | distributions |
| US high-yield credit | HYG | 3Y | distributions |
| ICBC A | 601398.SS | 3Y | dividends |
| China Construction Bank A | 601939.SS | 3Y | dividends |
| China Merchants Bank A | 600036.SS | 3Y | dividends |
| China Dividend ETF | 510880.SS | 3Y | distributions |
| US preferred stock | PFF | 3Y | distributions |
| US BDC | BIZD | 3Y | distributions |
| US REIT | VNQ | 3Y | distributions |
| Singapore REIT | CLR.SI | 3Y | distributions |
| Japan REIT | 1343.T | 3Y | distributions |
| US REIT · Data Center | DLR | 3Y | distributions |
| China REIT · Data Center | 508060.SS | available history | distributions |
| US REIT · Logistics | PLD | 3Y | distributions |
| China REIT · Logistics | 508056.SS | 3Y | distributions |
| China REIT · Water Utility | 508006.SS | 3Y | distributions |
| China REIT · Hydropower | 508026.SS | available history | distributions |
| China REIT · Toll Road | 508018.SS | 3Y | distributions |
| Global infrastructure | IGF | 3Y | distributions |
| US MLP / pipelines | AMLP | 3Y | distributions |
| Chongli property benchmark | regional second-hand housing | monthly source / daily snapshot | gross rental yield + property value |

The asset registry is in `src/nasset/engine.py`.

## Measurement model

For each underlying, start with notional **100** at the beginning of its window:

```text
units = 100 / start_price
current asset value = units × current_price
cash received = units × distributions
total value = current asset value + cash received
```

Cash is not reinvested, so principal movement and cashflow remain visible separately.

### High-liquidity assets

BTC, ETH, SPY and GLD use the latest **90 days** for principal change and realized-volatility context.

Option matrix cells include selected expiry / strike, delta, IV, seller-executable bid, mechanically annualized premium yield and liquidity. BTC and ETH use Deribit; listed US options prefer CBOE delayed option-chain Greeks and quotes. ETH also shows Lido's current stETH 7-day SMA staking APR as a separate cashflow strategy. Ranking starts from premium yield divided by **|delta|^1.2**, then adjusts for DTE, IV versus recent realized volatility, and liquidity.

Daily snapshots build the time series. This lets the dashboard evolve from a point-in-time estimate into a trailing history without changing the schema.

### Income / slower-moving assets

TLT, China bank equities, REIT and infrastructure proxies use a **3-year** window, reporting principal value, cash distributions and total-value CAGR separately. Non-USD assets are converted through historical FX into USD before the return calculation, so currency gains/losses are part of present value.

## Daily pipeline

`.github/workflows/daily.yml` runs once a day and also supports manual dispatch.

It:

1. fetches market data,
2. runs tests,
3. writes `data/snapshots/YYYY-MM-DD.json`,
4. updates `data/latest.json` and `data/timeline.json`,
5. commits generated data,
6. deploys `site/` to GitHub Pages.

The Pages app is static and has no runtime API keys.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m nasset --root .
python -m http.server 8000 -d site
```

Open `http://localhost:8000`.

## Data sources

- Deribit public API for BTC/ETH price and option-market data.
- Lido APR API for the current stETH 7-day SMA staking APR.
- CBOE delayed option-chain API for listed US option quotes, Greeks, open interest and volume.
- Yahoo Finance through `yfinance` for listed-asset prices, distributions, FX conversion and an option-chain fallback.

Provider failures are isolated per asset and recorded in the daily snapshot.

## Important

Mechanically annualized option premium is **not** a guaranteed APY. Selling options exchanges convexity / tail exposure for current cashflow. nasset is an observability and comparison tool, not personalized investment advice.


### Direct property benchmark

The initial China real-estate benchmark is **Chongli second-hand residential property**. It uses a monthly regional price series and the source's stated **1.78% gross annual rental yield**. The repository still writes a daily snapshot, but the page labels the source cadence as monthly. Vacancy, furnishing, property management, maintenance, tax and transaction costs are not yet deducted, so the rental yield must not be interpreted as net distributable cash.
