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
- On race or retry, compare exact target paths (and event keys for event datasets); identical completed slots no-op, incomplete/conflicting data fail loudly. The publisher walks only the requested Git tree directory prefixes using non-recursive Git Data API tree reads, with prefix caching; it does not fetch the full recursive archive tree or historical Parquet blob bodies. Missing paths are safe only when the entire incoming batch is new; partial or conflicting paths fail. No `force-push`, automated squash or daily rewrites.
- `GITHUB_TOKEN` scoped to `contents:write`; use Actions concurrency to avoid overlapping publishes.
- Monitor actual compressed bytes, source coverage, index freshness, native event timestamps, missing slots, Git objects and annual commit count. The orphan branch remains part of the same GitHub repository object database.

## DVOL closed-hour collection (implementation in PR #7)

- The hourly DVOL adapter requests Deribit's `get_volatility_index_data` for the **last fully completed UTC hour** `[slot-1h, slot)`, not a partial candle for the current hour.
- Preserve each native candle timestamp and OHLC in a typed `volatility_index/BTC|ETH/YYYY/MM/DD/HH00.parquet` part; `snapshot_slot_at` identifies when the collector archived the closed candle, while `event_timestamp` identifies the market observation.
- A missing candle, malformed response, duplicate/out-of-window event or pagination continuation fails the entire due batch. PAXG remains outside the DVOL allowlist until Deribit support is verified. Historical volatility and finalized delivery-event REST adapters now feed the common batch preparation with immutable native-event markers. Live API contract verification and scheduled collection are still incomplete; production remains disabled.
- Offline fixture tests cover DVOL-only requests, the combined live batch, and failure semantics. Do not claim a live Deribit contract test or production readiness from these fixtures.

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
- **Wired into a manual, dry-run-first collector, but NOT a scheduled workflow**. All due data and event identities are validated before handing one batch to `publish`; live source verification remains mandatory.
- Current implementation resolves only target paths via non-recursive Git tree traversal and caches shared prefixes; it never requests the entire historical recursive tree. Keep monitoring per-directory tree size and Git object growth.

## Finalized delivery-price REST pagination (implementation in PR #7)

- `DeribitClient.delivery_prices(index_name)` queries the exact source index identifier through `get_delivery_prices` with explicit `offset`/`count` pagination. It requires a stable `records_total` and fails closed on missing pages, changing totals, malformed or conflicting native `date` events, and page limits. A source-reported empty series is valid.
- Deribit's native delivery `date` is `YYYY-MM-DD` **calendar-day precision**; persist it as `event_date` (string), with no invented Unix timestamp or 08:00 UTC instant. `delivery_price` is finalized settlement data, not `estimated_delivery_price` from `get_index_price`. The adapter does not infer underlying currency from an index suffix or invent events.
- These pagination assumptions require verification against real Deribit responses before production. The adapter **does not yet implement event-level incremental filtering across archived slots**, delayed-event recovery, or daily batch wiring. The default pipeline deliberately refuses a due delivery dataset until these exist.
- Offline tests cover multi-page responses, zero-event history, malformed values, changing totals, conflicting timestamps and bounded page exhaustion.

## Validation and consumption

- Test BTC/ETH/PAXG from common configuration; include options across multiple settlement currencies, the PAXG futures/option listing behavior and multiple index names per base. Verify DVOL/HV remain BTC/ETH-only unless support is explicitly confirmed.
- Test index discovery, duplicate index-name responses, source data missing, empty settlement-event polls, late-arriving events, **hourly** chain/index/futures/DVOL alignment, daily history polling, nullable fields and Parquet ZSTD read/write.
- Keep raw Parquet off `main` and Pages. DuckDB/PyArrow query a selected subset of the archive and join by exact `index_name` plus valid time.
- Keep deployment gated until a real API contract test, end-to-end Git publish fixture and artifact-size measurement are successful.

Full implementation acceptance: [issue #6](https://github.com/BestNathan/nasset/issues/6).


## Historical event-key ledger and manual collector (PR #7 implementation)

Daily `historical_volatility` (BTC/ETH) and finalized `delivery_price`
(exact Deribit index name) polls must not duplicate the same native event on
each retry or later date. Their poll slot and event timestamp are independent.

- Each observed event is identified by `dataset + native asset/index name +
  native event key` (HV: epoch-millisecond timestamp; delivery: `YYYY-MM-DD`). A deterministic, tiny `deribit-event-v1` record also
  includes the native numeric value, so a source revision to an already
  archived event is detected as a **conflict**, not silently overwritten.
- Immutable marker paths reside on **the same orphan `market-data` ref**:
  `deribit/event_keys/historical_volatility/BTC/YYYY/MM/DD/<event_ms>.key`
  and `deribit/event_keys/delivery_price/btc_usd/YYYY/MM/DD/YYYY-MM-DD.key`.
  The path date is the **native event UTC date**, not the daily poll date.
- For each historical dataset, the collector asks the Git publisher for the
  exact candidate marker paths. It checks only the Git tree SHAs and never
  downloads historical Parquet blob contents. Matches are skipped; changed
  event values abort the entire batch; unseen events are written into the
  current UTC poll-slot Parquet. Empty increment means **zero new files** and
  no commit if the run contains only event datasets.
- New marker blobs and due Parquet parts enter **the same tree and commit**.
  A concurrent ref update or collision must fail safely; retry must rebuild
  and re-evaluate the entire batch. No force updates, repairs, backfilled
  changes to immutable parts, or mutable global watermark.
- This ledger has additional Git tree entries/objects. Monitor event-key count,
  per-dataset compressed bytes, and repository size trend as well as tree costs.
  No event-key compaction or history rewrites in v1.
- If a new event is discovered by rerunning an **already published** UTC
  slot, its existing immutable Parquet part is never appended or replaced.
  A later due poll can archive the delayed event in its **new** slot.
  Conflicting same-slot retries fail rather than mutate historical files.

The unscheduled entry point is:

`python -m nasset.deribit_collect --config config/market-data.yml --repository BestNathan/nasset`

It requires `GITHUB_TOKEN` to read Git tree metadata, defaults to **dry run**,
and emits JSON with due datasets, per-dataset Parquet row/part counts,
compressed bytes, event-key count, total batch bytes, and GitHub repository
size in KiB (a cross-ref estimate, not per-branch isolation). A capacity
review is flagged at 3 GiB. Explicit `--publish` appends one batch using Git
Data API; it is **not** enabled in a scheduled workflow. Never use
`--publish` against production until real API semantics and source
availability are verified. Do not expose token values in logs.

Offline tests cover replay without writes, late arrivals, conflicting values,
combined snapshot/history batches, whole-batch failure, and atomic
marker+Parquet publication. These tests do not validate Deribit's *live* PAXG
contract listings, index discovery completeness, observed settlement response
values, `records_total` pagination behavior, real compression sizing, or Git
archive growth. Those must be verified before enabling the hourly workflow.


### Delivery price source precision (2026-11-05 contract review)

The published Deribit `get_delivery_prices` response documents native
`date: "YYYY-MM-DD"` strings, not epoch-millisecond timestamps. The adapter,
Parquet schema, stable event identity, and tests use this date-only precision.
This prevents fabricating a settlement instant. Deribit's 2026-11-05 support
notice mentions new start/end timestamp filters, but that release was
**postponed**, so incremental replay does **not** depend on those filters.
Date-only settlement and millisecond historical-volatility observations
remain distinct identity domains.
