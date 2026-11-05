# nasset market-data archive: Parquet + Git (design v1)

Status: **design only, collection not yet enabled**. Implementation: [issue #6](https://github.com/BestNathan/nasset/issues/6). Config: [`config/market-data.yml`](../config/market-data.yml).

## Architecture and scope

- Code, workflow and configuration remain on `main`; archived immutable Parquet objects live on a true `market-data` **orphan branch** (no shared root history), in the same GitHub repository.
- Parquet uses native ZSTD compression level 6. Schema is explicit and versioned. Do **not** create a global numeric instrument-ID dictionary: store the original names, and use Parquet dictionary encoding as appropriate.
- Deribit collection is now a **multi-dataset provider** rather than an option-chain-only job. Each dataset has its own source API, typed schema, frequency, validation rules and partitioning.
- Common `sources.deribit.underlyings: [BTC, ETH, PAXG]` selects underlying/base assets. PAXG is enabled in the target configuration, including its listed options and futures/perpetuals where present.
- Optional dataset-level `underlyings` overrides the common list where API capability is narrower. DVOL and historical volatility are initially limited to BTC/ETH. Adding PAXG to the common scope does **not** automatically invoke unsupported volatility endpoints. Missing listed instruments must be distinguished from errors and recorded as explicit coverage/capability status.
- Collect raw reference series with native units/timestamps; do not mix them into an investment-theme hierarchy.

## Datasets and priorities

| Dataset | Initial enabled | API | Update semantics / research use |
|---|---|---|---|
| `option_chain` | yes, 1h | `public/get_instruments` + `public/get_book_summary_by_currency` | One current option quote per instrument; options prices, IV, OI, expiries, put/call surfaces |
| `index_price` | yes, 1h | `public/get_supported_index_names` or `get_index_price_names`, then `public/get_index_price` | Index observation + estimated delivery reference, time-aligned to chain |
| `delivery_price` | yes, daily poll | `public/get_delivery_prices` | Immutable actual delivery/settlement *events*, not latest-price snapshots; vital to realized expiry P&L |
| `volatility_index` | yes, 1h (BTC/ETH only) | `public/get_volatility_index_data` | Native timestamped DVOL candles; discover availability; BTC/ETH initial allowlist |
| `historical_volatility` | yes, daily incremental (BTC/ETH only) | `public/get_historical_volatility` | Realized-volatility observations with native time axis; compare IV/RV |
| `futures_curve` | yes, 1h | `public/get_book_summary_by_currency` (`kind=future`) | Native futures / perpetual prices and basis, optionally option forward context |
| `funding_rate` | no, optional | `public/get_funding_rate_history` | Native hourly funding data for actual perpetual instruments (do not query for spot/options) |

P0 and P1 are enabled in the target design, subject to API capability verification before scheduled production collection. Later high-cardinality sources (individual Greeks tickers, order books, full trades) are separate follow-up work. Do not initiate thousands of per-instrument requests within the bulk-chain collector. Historical index candles are optionally accessible via `public/get_index_chart_data`; don't conflate API-native time series with live observations.

References:
- [Deribit get_index_price](https://skmtc.dev/deribit/apis/deribit-api/docs/public/get_index_price/get)
- [Deribit get_supported_index_names](https://skmtc.dev/deribit/apis/deribit-api/docs/public/get_supported_index_names/get)
- [Deribit get_delivery_prices](https://skmtc.dev/deribit/apis/deribit-api/docs/public/get_delivery_prices/get)
- [Deribit get_volatility_index_data](https://skmtc.dev/deribit/apis/deribit-api/docs/public/get_volatility_index_data/get)
- [Deribit get_historical_volatility](https://skmtc.dev/deribit/apis/deribit-api/docs/public/get_historical_volatility/get)
- [Deribit get_funding_rate_history](https://skmtc.dev/deribit/apis/deribit-api/docs/public/get_funding_rate_history/get)

## Critical Deribit identity semantics

**Underlying asset is not settlement currency, and an index is identified by its full `index_name`**.

- For options, `public/get_instruments?currency=any&kind=option&expired=false`, filtered by `base_currency`, provides the authoritative instrument set. Query book summaries for *all relevant settlement currencies*, join on exact `instrument_name`, de-duplicate and require successful coverage.
- For indexes, discover supported names through the API and match relevant `price_index` identifiers from discovered instruments. Track **all** applicable quote-currency variants (e.g. `btc_usd` *and* `btc_usdc`, `paxg_usdc` and `paxg_btc` if actually referenced); never hardcode `BTC -> btc_usd` or generate unsupported names.
- `index_price.index_names: auto` derives the actual names from live instrument metadata and supported-name discovery. Document an explicit list override if market listings need index coverage independent of option listings.
- For `delivery_price`, obtain records by full index name and preserve the **settlement event date/time**, not merely the collection time; deduplicate overlapping paginated lookbacks by an event key including index name and native event timestamp/date.
- Fail visibly on unrecognized configured symbols, incompatible dataset capability or missing required data; an API success with an empty response is not automatically a healthy snapshot. A legitimate no-new-delivery-events result, however, is not a failure.

## Time and price semantics

- `captured_at` is the **actual API request/response observation time**. `snapshot_slot_at` is the UTC scheduled bucket, not an assertion that the value existed at the exact beginning of that slot.
- Record per-index observation times (the endpoint returns a current value, not necessarily a source-origin timestamp). For historical/settlement/candle datasets keep their native event timestamp separately.
- `index_price`, `underlying_price`, `mark_price`, `estimated_delivery_price`, and settled `delivery_price` are **not interchangeable**.
- Reference-market joins should use `index_name` plus time constraints (as-of join with a documented maximum tolerated staleness), not only the common asset symbol. Store all price units and quote currencies explicitly.
- Preserve `NULL` vs zero and original source precision; avoid invented Greeks or artificial backfilling of missed option-chain snapshots. Historical delivery/volatility series may be backfilled **only** when the API provides genuine event timestamps.
- Be explicit about public OI delay and native funding rate units. Index prices alone do not prove execution price or option strategy performance.

## Storage layout

Each dataset uses a logical UTC day partition with **immutable time-slot or event parts**; no closed Parquet file is repeatedly rewritten.

```text
market-data (orphan branch)
  deribit/
    option_chain/
      BTC/2026/11/05/0000.parquet
      BTC/2026/11/05/0100.parquet
      ETH/2026/11/05/0000.parquet
      PAXG/2026/11/05/0000.parquet
    index_price/
      2026/11/05/0000.parquet       # all relevant index names, one row each
      2026/11/05/0100.parquet
    delivery_price/
      2026/11/05/0000.parquet       # only if genuinely new delivery events found
    volatility_index/
      BTC/2026/11/05/0000.parquet   # hourly incremental DVOL candles
      ETH/2026/11/05/0000.parquet
    futures_curve/
      PAXG/2026/11/05/0000.parquet # only if supported contracts are listed
```

**Why index price is not split by BTC/ETH:** each requested index yields only one row per snapshot. Put a handful of distinct index-name rows in **one** small Parquet per slot to avoid hundreds of tiny files. It contains `index_name`, `base_currency`, `quote_currency`, `index_price`, `estimated_delivery_price`, `snapshot_slot_at`, `captured_at`, `schema_version`, `provider` and `source_method`. The option chain remains partitioned by base asset because it has many instrument rows.

For delivery events store `index_name`, native event date/time, native delivery price/unit, `first_observed_at` and source metadata; do not re-store old API history in every poll. Schema changes require explicit compatibility handling.

## Frequency and Git commit policy

- `interval_hours` is per dataset, v1 accepts divisors of 24: `1, 2, 3, 4, 6, 8, 12, 24`. Target defaults: chain, index price, DVOL and futures curve **1h**, delivery and historical-volatility increments **24h**. DVOL and historical volatility currently use BTC/ETH-only allowlists, with PAXG covered by chain/index/futures if supported.
- One workflow scheduled at `37 * * * *` UTC. A **cheap due-gate** reads config to select all enabled due datasets before installing costly dependencies. The archive runs hourly for P0/P1 live market snapshots, and daily for historical event datasets; a polling frequency must not be confused with the native event sampling interval. `workflow_dispatch` can trigger an idempotent manual run.
- Poll all configured underlyings/indices required by each due dataset, validate them, then publish all due datasets/underlyings together in **one Git commit per due UTC slot**. Commit count is determined by the union of dataset schedules, not by the number of assets. With current 1h + 24h schedule this is **24 commits/day (~8,760/year)**, not one commit per dataset or per asset.
- Parquet files are immutable and named for the scheduled slot/day; late capture retains actual `captured_at` and must not pretend to be a historical observation. Delayed historical **events** have their own timestamp and idempotent event key.
- Every due snapshot dataset must be complete before the batch commit; do not partially publish successful BTC if ETH/index retrieval fails. Delivery event datasets may correctly produce no files if no new event occurred.
- Publisher uses GitHub Git Data API blob → tree → commit → non-forced ref update. Initial `market-data` commit has zero parents. No fetching/checkout of its full blob history; shallow main-only checkout for CI.
- On race or retry, compare exact target paths (and event keys for event datasets); identical completed slots no-op, incomplete/conflicting data fail loudly. No `force-push`, automated squash or daily rewrites.
- `GITHUB_TOKEN` scoped to `contents:write`; use Actions concurrency to avoid overlapping publishes.
- Monitor actual compressed bytes, source coverage, index freshness, native event timestamps, missing slots, Git objects and annual commit count. The orphan branch remains part of the same GitHub repository object database.

## Capacity and future rolling policy (design only)

- With BTC+ETH alone, previous planning model estimated ~3.1 GB/year of Parquet data with hourly chain/index/DVOL/futures plus daily history/settlement checks. **PAXG adds unmeasured volume**; neither its current contract count nor Git pack overhead has been benchmarked. Measure on real live snapshots before setting retention thresholds.
- Hourly unified collection means ~24 Git commits/day, ~8,760/year; the number of configured assets/datasets does not multiply commits. Each UTC slot writes immutable typed Parquet parts with a single batch commit. No daily rewrite of compressed Parquet blobs.
- GitHub **strongly recommends** repositories remain under 5 GB, even though orphan branches do not affect the objects pulled by a properly scoped shallow main checkout. They still **share repository object storage**. Monitor Git object size, total Parquet bytes, object/file count, commit count and projected growth; trigger capacity review early (~3 GB) rather than treating 5 GB as unlimited headroom.
- **Rolling historical retention is explicitly deferred.** Do not squash/amend/rebase/force-push, delete snapshots or auto-GC as part of this change. The future proposal must compare (a) all-hourly retention, (b) immutable tiered data rollups and yearly sealed manifests, and (c) annual repository/archive split or external cold storage while preserving raw auditability and stable historical references.
- An **aggregated daily/weekly Parquet** cannot safely replace raw hourly files until the aggregation and sampling semantics, preservation of native timestamps, reproducible manifests/checksums, migration and recovery tests, and actual Git storage reclamation are independently validated. Compaction in the working tree alone does not free Git commit history.

## GitHub publisher implementation (first increment)

- `src/nasset/deribit_publish.py` implements a batch publisher using Git Data API only: create blobs, create tree, create commit, and advance `refs/heads/market-data`. First publish creates a zero-parent root commit; subsequent commits use the current tip as sole parent.
- Before creating any objects on an existing branch, the publisher reads the **tree metadata only** (not Parquet blobs) and compares Git blob SHA-1 values for every target file. Complete identical batches are no-ops; partial or conflicting immutable paths are rejected. Truncated tree listings are explicitly rejected rather than trusted.
- Ref update uses `force=false`. A concurrent update can reject the fast-forward; the caller must re-read the branch and retry the whole batch. The publisher itself does not silently retry an uncertain ref outcome.
- `tests/test_deribit_publish.py` uses a fake HTTP session to verify orphan initialization, atomic one-commit batches, idempotent replay, partial/conflicting slots, truncated trees, invalid payloads and ref races.
- **Not yet wired into a collector or workflow**. A future orchestration step must ensure every due dataset and configured symbol is complete *before* calling `publish`, and must reconcile event-level incremental deduplication separately from per-slot path immutability.
- Current implementation requests a recursive Git tree for path checking. This avoids blob downloads but may become inefficient or truncated as the archive grows. Before large-scale deployment, replace it with targeted path-tree traversal or a compact immutable manifest/index, with explicit correctness tests.

## Validation and consumption

- Test BTC/ETH/PAXG from common configuration; include options across multiple settlement currencies, the PAXG futures/option listing behavior and multiple index names per base. Verify DVOL/HV remain BTC/ETH-only unless support is explicitly confirmed.
- Test index discovery, duplicate index-name responses, source data missing, empty settlement-event polls, late-arriving events, **hourly** chain/index/futures/DVOL alignment, daily history polling, nullable fields and Parquet ZSTD read/write.
- Keep raw Parquet off `main` and Pages. DuckDB/PyArrow query a selected subset of the archive and join by exact `index_name` plus valid time.
- Keep deployment gated until a real API contract test, end-to-end Git publish fixture and artifact-size measurement are successful.

Full implementation acceptance: [issue #6](https://github.com/BestNathan/nasset/issues/6).
