# A-share dividend topic methodology

## Boundaries and data coverage

A-share-only company screen of 42 banks, 3 telecom operators and 33 infrastructure operators. Universe is an *explicit research basket*, not a claim that the 33 infrastructure operators are every infrastructure company in China. Universe definition is versioned in `data/topics-universe.json`. H shares are deliberately excluded from same-currency rankings.

Historical cash dividends come from Eastmoney `RPT_SHAREBONUS_DET`. The source fields are:
- `REPORT_DATE`: fiscal-year ownership of the distribution.
- `EX_DIVIDEND_DATE`: actual ex-date; a proposal without ex-date is not realized cash.
- `PRETAX_BONUS_RMB`: yuan per 10 shares, converted to per-share cash by dividing by 10.
- `BONUS_IT_RATIO` / `IT_RATIO`: bonus and transfer shares per 10, when published.

`2015–2025` means the 11 annual cash-dividend observations separated by 10 compounding intervals. Actual fiscal-year 2025 distributions may be paid during calendar 2026. Implemented 2025 cash to date is **not** automatically equivalent to a final annual board recommendation.

Cash dividends from multiple events belonging to a fiscal year are summed, while exact duplicate event records are rejected. Missing records are **null**, not presumed zero. The reported 10-year CAGR requires all 11 positive fiscal-year observations. Where bonus/transfer share actions occur, historical dividends are additionally converted to **latest-share-equivalent units** (old cash per share divided by subsequent share-delivery factors), while source/raw per-share amounts remain inspectable. If the corporate action audit is incomplete or 11-year coverage is missing, CAGR must be unavailable. Observed cut counts only use actually observed adjacent fiscal years, so missing years do not create fictitious non-cuts. These are historical frequencies, **not a calibrated probability of future dividends**.


### Holder-class dividend exception (FY2015 Yangtze Power)

`600900.SS` FY2015 distributions were not uniform across all holders. The company's
official **2016-07-13 implementation announcement** specifies RMB **4.00 per
10 shares** for ordinary pre-restructuring/public holders versus RMB **1.2946
per 10 shares** for designated asset-subscription shareholders. The upstream
event dataset only surfaced 1.2946, which inflated the naive 2015–2025 CAGR.
`data/topics-dividend-corrections.json` records the exceptional value, source
and exact upstream assertion. The research topic selects the public A-share
holder basis; the correction is not a generic override across shareholder classes.
Source: https://static.cninfo.com.cn/finalpage/2016-07-13/1202468389.PDF

## Valuation and return

- Current A-share close, CNY, Yahoo Finance; show quote timestamp.
- FY2025 implemented dividend yield = already implemented FY2025 DPS in comparable current-share units / latest close. This is an observed indicator, not a forward yield or guaranteed payout.
- Trailing 365-day cash yield = Eastmoney dividends whose ex-date is within past 365 days / latest A-share close. This can mix fiscal periods; it must be labeled separately.
- Risk and rolling total returns use Yahoo `Adj Close`. This vendor-adjusted series approximates distributions-reinvested total return and is not interchangeable with the explicit cashflow simulation.
- Annualized volatility uses sample standard deviation of daily adjusted returns × sqrt(252).
- Maximum drawdown uses adjusted-close wealth index / rolling peak − 1.
- Daily 95% CVaR is the mean of daily returns at or below empirical 5% quantile. It is **daily**, not a 1-year expected shortfall.
- Sortino is adjusted-close average daily excess return over zero divided by downside RMS and annualized with sqrt(252).
- Rolling 1/3/5/10-year returns use overlapping 252-trading-day blocks per year. Minimum 30 windows to show a percentile. Observations overlap and are *not* independent.

## 1 million CNY cashflow simulation

Invest once on the first usable trading date on or after 2015-01-01 (if not listed, start when data begins), buy whole 100-share board lots, hold to today. Yahoo historical Close is generally split-adjusted: for the initial historical trade, reconstruct the contemporary share price using subsequent disclosed bonus/transfer factors and then grow the share count as those actions occur. Count actual cash by **ex-date calendar year** using shares held at the event. The interface can switch between (a) **cash withdrawn**, not reinvested, and (b) **dividend reinvested** in 100-share lots on the first usable close on/after the ex-date, carrying residual cash. Both are simulated scenarios, before tax, fees, rights subscription and slippage; they are **not broker-executed backtests**. Because vendor OHLC splitting and corporate-action databases may differ, retained source events permit an audit.

## Financial and quality notes

The financial panel attempts Eastmoney annual financial facts where available. Missing bank capital adequacy or NPL, non-bank FCF coverage, telecom capex, concession/toll expiry, etc. are explicitly missing rather than silently approximated. **No predictive payout-probability score is published**, because no calibrated out-of-sample model has been tested.

Pipeline per-stock errors are recorded; successful old dividend data may remain as **stale** if provider fails. Source outages will never convert missing financials into zeros. If all dividend retrievals fail, generation exits nonzero and blocks a new Pages deployment.

This project offers historical comparative analytics rather than investment advice.

### Implementation-period transparency

Each normalized event preserves `REPORT_DATE`, an interim/annual/other classification,
`EX_DIVIDEND_DATE`, actual pre-tax cash per 10, transfer/bonus share actions and raw vendor plan.
Possible special dividends are marked only if the vendor plan actually contains the relevant
wording; no special-event inference is made from size alone. The UI permits switching between
raw contemporaneous DPS and split-comparable DPS, and expands a per-event audit table.

