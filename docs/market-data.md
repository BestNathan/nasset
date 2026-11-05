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

## Verified zero-listing capabilities and completeness

The 2026-11-05 live Deribit probe returned **zero listed PAXG options**, despite listing one PAXG future and the `paxg_usdc` index. A missing market product is **not** an API failure, but it must never be confused with an incomplete quote response.

- `option_chain.allow_unlisted: [PAXG]` makes this a narrow, explicit, validated exception. All other configured option underlyings remain strict. Adding a contract later automatically resumes ordinary option-chain output.
- For `futures_curve`, absence of a listed futures product for one base is an allowed optional product state; an absent quote for an actually listed future **always fails** the whole batch.
- On a verified zero-listing, write `deribit/coverage/YYYY/MM/DD/HH00.json` with the exact UTC slot, actual observation time, dataset, base underlying and `listed_count: 0`. Commit it atomically with the same slot's validated Parquet datasets and completion receipt. The lack of an asset-specific Parquet part is then auditable, not silently dropped or represented as a fabricated zero price.
- The read-only probe reports such explicit absences as **informational** `capability_gaps`. Zero listings for strict options or a failed/empty source response for **listed** contracts are blocking. Probe results do not authorize turning on the production publisher without further checks.
- Coverage manifests are control-plane JSON records, not raw market prices. All market time-series records remain Parquet/ZSTD.

### Measured read-only baseline (2026-11-05 16:57 UTC)

One actual live public-API probe measured 2,954 listed BTC+ETH options and no PAXG options. It encoded the resulting enabled snapshot datasets into **140,320 ZSTD-Parquet bytes**: option chain 107,250, futures 22,001, indexes 3,533, DVOL 7,536. Futures and index data included listed PAXG contracts/references. This was an in-memory **probe**, never published to `market-data`.

At a hypothetical constant 140,320 bytes for each of 8,760 hourly snapshots, base Parquet bytes would be roughly **1.23 GB/year**, excluding historical-event parts, coverage JSON, Git history and growth in contract counts. Do not extrapolate this single live sample as an SLA, and keep the more conservative storage monitoring budget.

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
- **Historical event scale:** the first settlement backfill can contain >9,000 distinct event-key objects. Event keys, slot receipts and small coverage manifests are encoded as native **inline UTF-8 Git Tree entries** (not thousands of individual Blob API POSTs), grouped into 250-entry candidate-tree batches. Parquet binaries continue to use verified Git Blob API writes. Ref movement occurs once, only after all candidate trees and the final commit have succeeded; failed intermediate calls cannot expose a partially published slot.
- For repeat historical polls, the existence-check walks only the requested event **identity and year subtrees** and uses bounded recursive Git Tree reads *at that scope*, not a recursive scan of the whole archive or one call per day/event. Refuse truncated year trees and conflicting preexisting event-key SHAs. This avoids thousands of GitHub API reads per daily backfill and preserves immutable checksums.

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

## Hourly workflow activation and safe rollout

The new `.github/workflows/deribit-archive.yml` is intentionally **disabled for automatic publication** unless the repository variable `DERIBIT_ARCHIVE_ENABLED` is explicitly set to the string `true`. Undefined/empty/false means scheduled runs skip both jobs (safe by default), even after the workflow is merged to `main`. No archive branch is initialized automatically while disabled.

- `workflow_dispatch` defaults to `publish: false` and runs a **read-only** live source probe and complete due-hour Parquet batch preparation, including existing event-key metadata lookups. Its job token has only `contents: read`; `--publish` is not passed.
- The separate `publish` job has `contents: write` and is eligible **only** when `DERIBIT_ARCHIVE_ENABLED=true` and either an hourly `schedule` event fires or a manual run explicitly supplies `publish: true`. It still depends on a successful read-only source/collector acceptance job; that guard is not a substitute for CI and real Git reference verification.
- When the operator elects to activate, validate a controlled successful append-only publication (orphan root must have zero parents, immutable Parquet/coverage/receipt all present, a duplicate same-hour run is a no-op, and no `main` history is fetched) before relying on scheduled runs. Stop/disable by setting the variable back to `false`.
- GitHub schedules execute only from the default branch. The workflow added on this PR branch is **not a live schedule** until merged; no repo variable has been set as part of this work. The read-only contract workflow is independent and remains the pre-activation test.

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


### Immutable UTC-slot completion receipt (implementation checkpoint)

A successful non-empty due-slot publish now atomically includes
`deribit/slots/YYYY/MM/DD/HH00.receipt` in the same Git tree as its Parquet
parts and historical event markers. Its deterministic payload identifies the
UTC hour and due dataset configuration, including selected underlyings,
intervals and compression level; it does **not** depend on the wall-clock
capture timestamp or current exchange quote. Before a `--publish` run
queries Deribit, it checks this receipt using only Git tree metadata. An
identical completed slot is skipped; an incompatible configuration is a
hard conflict. A concurrent ref movement is still handled by a non-forced
Git update, with receipt checking repeated in the publisher.

A receipt represents a completed **non-empty** batch, not proof that a
daily historical event-only poll returned no new events. Empty event-only
polls remain read-only, and subsequent polls can recover late arrivals.
A dry run deliberately does not use the completion shortcut, so it can
still measure a fresh source sample. This receipt is not an archive manifest
or a substitute for independent Git-object-size and source-completeness
monitoring.

The orphan archive and scheduled production collector remain disabled until
real Deribit API/PAXG contracts and measured live Parquet sizes are verified.


### Read-only public API contract verification (pre-production gate)

`src/nasset/deribit_probe.py` is an **independent source probe**; it has no
GitHub archive publisher, GitHub token, mutation code or scheduled collector.
Run locally with `python -m nasset.deribit_probe --config config/market-data.yml`
or inspect the `live-contract` job in
`.github/workflows/deribit-contract.yml`. That workflow has only
`pull_request` (Deribit-scoped paths) and `workflow_dispatch` triggers,
`contents: read` permissions, and a shallow one-commit checkout. It does not
alter the existing Pages/daily workflows.

The probe uses actual public Deribit responses to check:

- BTC/ETH/PAXG derivative catalog by `base_currency` and `kind`, separately
  tracking listed/active option/future counts and settlement-currency partitions;
  PAXG options missing from the actual source is a **blocking capability
  finding**, not a reason to fabricate a contract or quote.
- Exact referenced `price_index` names cross-checked against the source
  `get_index_price_names` catalog; never invent an `_usd` or `_usdc` fallback.
- A complete hourly option/index/futures/DVOL batch rendered in-memory as
  independently readable PyArrow/ZSTD Parquet, with actual per-dataset row
  counts and byte totals printed as JSON.
- BTC/ETH native historical-volatility observations and **complete**
  finalized-delivery pagination for each discovered index. The probe checks
  page totals, duplicate native dates, malformed values and future dates;
  it reports `validated_rows` per index and never publishes these events.
  This adds read-only API requests proportional to historical delivery depth.

A successful offline fixture suite is **not** live validation. A failed or
unreachable exchange probe must be investigated and documented before merging
or enabling any production `--publish`/hourly schedule. Even successful
live output does not by itself prove real Git Data API append/race behavior,
long-term object-store growth; those remain separate acceptance gates. Full
historical-delivery pagination is now checked by the probe but still needs a
successful live run as evidence.


#### Live contract finding and partial measurement policy (2026-11-05)

The first genuinely live, **read-only** contract run
[42057233650](https://github.com/BestNathan/nasset/actions/runs/42057233650)
reported `PAXG: no option instruments listed` when filtering the returned
`get_instruments(currency=any,kind=option)` catalog by the actual
`base_currency`. This is a source-capability observation for that run, **not**
proof PAXG options can never be offered. We must not invent a PAXG option
chain, silently relabel USDC contracts as PAXG or declare the full configured
dataset complete.

The probe now reports blocking capability gaps explicitly, and can measure
available underlying subsets in memory while leaving overall contract status
**blocked** (nonzero exit code). This gives a reproducible BTC/ETH size
baseline without misrepresenting it as BTC/ETH/PAXG completeness. Missing
futures are reported separately as product absence. It still performs no Git
Data API mutation and can never initialize the orphan archive.

Before activation, confirm actual index-name coverage, whether PAXG has
currently listed option/future instruments, how `is_active=false` catalog
entries relate to book summaries, and whether the configured product scope
must change. Changes to that scope require explicit acceptance evidence and
an updated config, not a fabricated historical record.


#### Measured public-source sample — 2026-11-05 15:47 UTC

Read-only job [42058417317](https://github.com/BestNathan/nasset/actions/runs/42058417317)
queried the **actual Deribit public API**; all measurements below were encoded
with PyArrow/ZSTD in memory and were **never published to Git**:

| Dataset | Native source rows | Parquet parts | Compressed bytes | Coverage |
| --- | ---: | ---: | ---: | --- |
| Option chain | 2,938 | 2 | 107,246 | BTC 1,574; ETH 1,364; PAXG 0 |
| Futures curve | 47 | 3 | 22,060 | BTC 23; ETH 23; PAXG 1 |
| Index price | 5 | 1 | 3,533 | btc_usd, btc_usdc, eth_usd, eth_usdc, paxg_usdc |
| Volatility index (DVOL) | 2 | 2 | 7,536 | BTC and ETH, previous completed hour |
| **Total measured** | **2,992** | **8** | **140,375** | One-hour source sample, not a full historical archive |

The source reported 370 supported index identifiers. Sample-only finalized
delivery counts: btc_usd 2,660; btc_usdc 1,702; eth_usd 2,500;
eth_usdc 1,697; paxg_usdc 758. Historical-volatility response length
was 383 observations each for BTC and ETH. That earlier run sampled two records per index; **full pagination has not yet
been verified by a successful run of the updated probe**.

The **blocking gap** is PAXG `option_chain` with zero listed option contracts
at this point in time. The probe intentionally excluded only that unavailable
asset/dataset from its *measurement*, reported the gap explicitly, and exited
nonzero. This does not permit the production collector to silently omit PAXG:
an explicit capability status and fail-closed dataset policy are still needed
before enabling scheduled collection.

Earlier read-only run [42057994689](https://github.com/BestNathan/nasset/actions/runs/42057994689)
also revealed the inclusive source DVOL end boundary. Querying the final
millisecond *before* the next UTC hour now validates the closed candle
without accidentally including the first candle of the following hour;
the later run measured both BTC/ETH DVOL parts successfully.

These numbers are one representative live observation, not a measured
yearly size distribution. They exclude full historical backfills, Git Blob
headers, tree/commit objects, index storage, changing contract counts and
any hypothetical PAXG option inventory. Never extrapolate them into a
reliable storage-retention decision without repeated slot samples.
