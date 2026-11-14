# Deribit RWA perpetual archive

RWA is a **separate hourly archive dataset**, not a crypto option chain and
not an equity ownership/tokenized-share feed. Source: [Deribit RWA Perpetual
contract specifications](https://support.deribit.com/hc/en-us/articles/38325634622493-RWA-Perpetual)
(updated 2026-11-03).

## Scope and discovery

- Categories: equities, equity ETFs, commodities, pre-IPO.
- Curated **subcategory** registry: `config/market-data.yml`
  `rwa_perpetual.categories`. The Deribit API's `product_group=RWA`
  identifies new RWA listings without manual sync. Newly discovered
  instruments outside the verified registry are collected with category
  `unclassified` and pricing basis `unclassified_rwa_reference`.
  Update the subcategory list after official verification. Do not infer RWA
  from `_USDC-PERPETUAL` alone, since crypto instruments share the suffix.
- For every hourly due slot, read `get_instruments(currency=any,kind=future)`;
  discover `product_group=RWA` and accept curated older metadata without
  group labels; match the **exact** `TICKER_USDC-PERPETUAL` name, base
  currency, kind and USDC settlement, excluding explicitly inactive listings.
  Only then read
  `get_book_summary_by_currency(currency=USDC,kind=future)` and join on
  full instrument name. If a listed contract lacks a quote, fail the whole
  atomic batch. Unknown crypto or fixed-expiry derivatives never enter RWA.
- One typed, immutable, native-ZSTD Parquet part per UTC hour:
  `deribit/rwa_perpetual/YYYY/MM/DD/HH00.parquet`.
  Each snapshot also atomically publishes a small immutable checkpoint:
  `deribit/rwa_state/YYYY/MM/DD/HH00.json`.
  A successful scan with **zero** qualifying listed contracts produces a
  **zero-row Parquet** rather than fictitious prices. The slot receipt requires
  **both** files, so missing lifecycle state cannot look like completed collection.
- Fields include ticker/category, exact instrument identity and index ID,
  native USDC bid/ask/mark/last, source index (if in summary), OI, volume,
  funding (when present), contract size/tick size and capture/slot timestamps.
  Missing optional native values remain null, never zero.

## Lifecycle discovery and durable state

- `deribit_rwa_lifecycle.py` compares each complete successful catalog with the
  **latest previously archived checkpoint**, not just the prior wall-clock hour.
  Historical Parquet parts stay immutable. State files are Git blobs included in
  the atomic slot manifest and pinned by Git blob SHA.
- Each checkpoint contains every observed instrument's first/last seen times,
  last native contract specification, active/inactive/uncertain/delisted-inferred
  status, missed-observation count and **events**. New instruments emit
  `first_observed` (not a claimed exchange listing time); the first absence
  emits `absence_observed`, and a second consecutive successful absent catalog
  emits `delisting_inferred`. Reactivation, relisting, and specification
  changes are separate events, with before/after metadata.
- A missing instrument can reflect temporary upstream issues, not a confirmed
  exchange delisting. A sudden entire-empty catalog after active instruments
  aborts the batch. No data or state is overwritten on Git ref conflicts.
  The publisher reads the previous state by bounded Git tree navigation and
  binds publication to its observed archive tip, preventing stale-state writes.
- An unannounced ticker rename is **not automatically merged** with another
  company; an authoritative sourced identity link is required.

## Verified corporate actions and continuous-price view

`config/market-data.yml` declares the officially announced
`ANTH_USDC-PERPETUAL -> ANT_USDC-PERPETUAL` rebase effective
**2026-11-16 10:00 UTC**, with price divisor **13.116**, position-quantity
multiplier **13.116**, and Deribit source URL. This declaration alone does not
convert raw snapshots. The hourly catalog must show the old instrument in a
prior checkpoint, and the new instrument live while the old one is absent,
at or after the announced time. Only then is `price_rebase_observed` emitted
and the rule persisted in `confirmed_actions`.

For an observed rebase with divisor `K`:
`adjusted_old_mark = native_old_mark / K`;
`adjusted_new_mark = native_new_mark`;
`adjusted_position_quantity = original_quantity * K`.
The pure `continuous_marks()` reader produces `lineage_id`,
`canonical_instrument`, `raw_mark_price`, `adjusted_mark_price`,
`price_adjustment_divisor` and confirmation status. It supports multiple
successive confirmed actions; it never rewrites original source data. Before
confirmation, two tickers remain separate time series even if an IPO is
scheduled. No exchange conversion timestamp is invented: the checkpoint uses
`observed_at` plus the separate announced effective time.

**Phase 2 is distinct:** after the IPO a 5-minute TWAP bridge may switch the
reference index to actual public equity; no price-divisor change should be
assumed. Its status must be tracked as a new priced-basis event based on
published exchange details, not on the observed mark-price jump.

## Pre-IPO pricing, unit changes and comparison limitations

`ANTH` and `OPENAI` are **pre-IPO perpetual contracts** with a Deribit
internal 30-minute EMA reference, not observable stock prices. Their raw price
must not be interpreted as shares, equity ownership, implied market
capitalization, or automatically spliced into a post-IPO per-share series.
Deribit's stock and ETF indexes use distinct in-hours/out-of-hours sources;
RWA trades are 24/7 even when stock exchanges are closed. Archive rows retain
`pricing_basis` and `contract_size`; the sourced, confirmed conversion
ledger is the only basis for adjusted series. Never silently back-adjust this
raw market data.

Read-only dry-run `python -m nasset.deribit_collect --config
config/market-data.yml` verifies the batch; actual publication still requires
the existing repository-controlled activation gate. The data is not yet
projected into the GitHub Pages dashboard. New Deribit RWA listings are auto-collected when the API marks them RWA;
the category registry should be reviewed to refine `unclassified` rows.
