# A-share dividend topic methodology

## Boundaries and data coverage

A-share-only company screen of 42 banks, 3 telecom operators and 33 infrastructure operators. Universe is an *explicit research basket*, not a claim that the 33 infrastructure operators are every infrastructure company in China. Universe definition is versioned in `data/topics-universe.json`. H shares are deliberately excluded from same-currency rankings.

Historical cash dividends come from Eastmoney `RPT_SHAREBONUS_DET`. The source fields are:
- `REPORT_DATE`: fiscal-year ownership of the distribution.
- `EX_DIVIDEND_DATE`: actual ex-date; a proposal without ex-date is not realized cash.
- `PRETAX_BONUS_RMB`: yuan per 10 shares, converted to per-share cash by dividing by 10.
- `BONUS_IT_RATIO` / `IT_RATIO`: bonus and transfer shares per 10, when published.

`2015–2025` means the 11 annual cash-dividend observations separated by 10 compounding intervals. Actual fiscal-year 2025 distributions may be paid during calendar 2026. Implemented 2025 cash to date is **not** automatically equivalent to a final annual board recommendation.

Cash dividends from multiple events belonging to a fiscal year are summed, while exact duplicate event records are rejected. Missing records are **null**, not presumed zero. The reported 10-year CAGR requires all 11 positive fiscal-year observations and no unadjusted share bonus/transfer; otherwise the CAGR is unavailable. Observed cut counts only use actually observed adjacent fiscal years, so missing years do not create fictitious non-cuts. These are historical frequencies, **not a calibrated probability of future dividends**.

## Valuation and return

- Current A-share close, CNY, Yahoo Finance; show quote timestamp.
- FY2025 implemented dividend yield = already implemented FY2025 DPS / latest close. This is an observed indicator, not a forward yield or guaranteed payout.
- Trailing 365-day cash yield = Eastmoney dividends whose ex-date is within past 365 days / latest A-share close. This can mix fiscal periods; it must be labeled separately.
- Risk and rolling total returns use Yahoo `Adj Close`. This vendor-adjusted series approximates distributions-reinvested total return and is not interchangeable with the explicit cashflow simulation.
- Annualized volatility uses sample standard deviation of daily adjusted returns × sqrt(252).
- Maximum drawdown uses adjusted-close wealth index / rolling peak − 1.
- Daily 95% CVaR is the mean of daily returns at or below empirical 5% quantile. It is **daily**, not a 1-year expected shortfall.
- Sortino is adjusted-close average daily excess return over zero divided by downside RMS and annualized with sqrt(252).
- Rolling 1/3/5/10-year returns use overlapping 252-trading-day blocks per year. Minimum 30 windows to show a percentile. Observations overlap and are *not* independent.

## 1 million CNY cashflow simulation

Invest once on the first usable trading date on or after 2015-01-01 (if not listed, start when data begins), buy whole 100-share board lots, hold to today. Count actual historical cash by **payment/ex-date calendar year** using held shares. Share count is adjusted for detected bonus/transfer events. Dividends are not reinvested; tax, fees, subscription rights and execution slippage are not modeled. This is a scenario **not a traded backtest**; potential unmatched capital actions must be verified manually.

## Financial and quality notes

The financial panel attempts Eastmoney annual financial facts where available. Missing bank capital adequacy or NPL, non-bank FCF coverage, telecom capex, concession/toll expiry, etc. are explicitly missing rather than silently approximated. **No predictive payout-probability score is published**, because no calibrated out-of-sample model has been tested.

Pipeline per-stock errors are recorded; successful old dividend data may remain as **stale** if provider fails. Source outages will never convert missing financials into zeros. If all dividend retrievals fail, generation exits nonzero and blocks a new Pages deployment.

This project offers historical comparative analytics rather than investment advice.
