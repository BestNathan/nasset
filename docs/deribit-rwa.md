# Deribit RWA perpetual archive

RWA is a **separate hourly archive dataset**, not a crypto option chain and
not an equity ownership/tokenized-share feed. Source: [Deribit RWA Perpetual
contract specifications](https://support.deribit.com/hc/en-us/articles/38325634622493-RWA-Perpetual)
(updated 2026-11-03).

## Scope and discovery

- Categories: equities, equity ETFs, commodities, pre-IPO.
- Curated classified universe: `config/market-data.yml` `rwa_perpetual.categories`.
  A ticker is introduced only after verifying its category against Deribit's
  official current contracts; do not infer RWA from `_USDC-PERPETUAL` alone,
  since crypto instruments share the suffix.
- For every hourly due slot, read `get_instruments(currency=any,kind=future)`;
  match the **exact** `TICKER_USDC-PERPETUAL` name, base currency, kind and
  USDC settlement, excluding explicitly inactive listings. Only then read
  `get_book_summary_by_currency(currency=USDC,kind=future)` and join on
  full instrument name. If a listed contract lacks a quote, fail the whole
  atomic batch. Unknown crypto or fixed-expiry derivatives never enter RWA.
- One typed, immutable, native-ZSTD Parquet part per UTC hour:
  `deribit/rwa_perpetual/YYYY/MM/DD/HH00.parquet`.
  A successful scan with **zero** qualifying listed contracts produces a
  **zero-row Parquet** rather than fictitious prices. The slot receipt requires
  this part, so missing data cannot look like completed collection.
- Fields include ticker/category, exact instrument identity and index ID,
  native USDC bid/ask/mark/last, source index (if in summary), OI, volume,
  funding (when present), contract size/tick size and capture/slot timestamps.
  Missing optional native values remain null, never zero.

## Pre-IPO pricing, unit changes and comparison limitations

`ANTH` and `OPENAI` are **pre-IPO perpetual contracts** with a Deribit
internal 30-minute EMA reference, not observable stock prices. Their raw price
must not be interpreted as shares, equity ownership, implied market
capitalization, or automatically spliced into a post-IPO per-share series.
Deribit's stock and ETF indexes use distinct in-hours/out-of-hours sources;
RWA trades are 24/7 even when stock exchanges are closed. Archive rows retain
`pricing_basis` and `contract_size`; any future price-basis transition or
corporate action requires an explicitly versioned conversion/event series
before normalized-return or valuation charts can be generated. Never silently
back-adjust this raw market data.

Read-only dry-run `python -m nasset.deribit_collect --config
config/market-data.yml` verifies the batch; actual publication still requires
the existing repository-controlled activation gate. The data is not yet
projected into the GitHub Pages dashboard. New Deribit RWA listings require
review and a registry update.
