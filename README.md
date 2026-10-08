# nasset

Daily **asset → cashflow** analytics.

nasset keeps three questions separate:

1. **What happened to principal?** Buy once at the beginning of the measurement window and mark the same position today.
2. **How much cashflow did / can the asset produce?** Historical assets use actual distributions; option strategies use current seller-executable premium and mechanical annualization.
3. **Which option-income cell is attractive now?** Strategies are rendered as DTE × Delta matrices with a transparent risk-adjusted ranking.

## Assets in v0.1

| Asset | Proxy / venue | Window | Cashflow model |
|---|---|---:|---|
| Bitcoin | Deribit BTC | 90D | covered call + cash-secured put matrices |
| S&P 500 | SPY / CBOE | 90D | covered call + cash-secured put matrices |
| US long Treasury | TLT | 3Y | distributions |
| US REIT | VNQ | 3Y | distributions |
| Singapore REIT | CLR.SI | 3Y | distributions |
| Japan REIT | 1343.T | 3Y | distributions |
| Global infrastructure | IGF | 3Y | distributions |

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

BTC and SPY use the latest **90 days** for principal change and realized-volatility context.

Option matrix cells include selected expiry / strike, delta, IV, seller-executable bid, mechanically annualized premium yield and liquidity. BTC uses Deribit; listed US options prefer CBOE delayed option-chain Greeks and quotes. Ranking starts from premium yield divided by **|delta|^1.2**, then adjusts for DTE, IV versus recent realized volatility, and liquidity.

Daily snapshots build the time series. This lets the dashboard evolve from a point-in-time estimate into a trailing history without changing the schema.

### Income / slower-moving assets

TLT, REIT and infrastructure proxies use a **3-year** window, reporting principal value, cash distributions and total-value CAGR separately. Non-USD assets are converted through historical FX into USD before the return calculation, so currency gains/losses are part of present value.

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

- Deribit public API for BTC price and option-market data.
- CBOE delayed option-chain API for listed US option quotes, Greeks, open interest and volume.
- Yahoo Finance through `yfinance` for listed-asset prices, distributions, FX conversion and an option-chain fallback.

Provider failures are isolated per asset and recorded in the daily snapshot.

## Important

Mechanically annualized option premium is **not** a guaranteed APY. Selling options exchanges convexity / tail exposure for current cashflow. nasset is an observability and comparison tool, not personalized investment advice.
