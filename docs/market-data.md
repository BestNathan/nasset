# Git-backed market-data archive (design v1)

Status: **agreed design, collector not yet deployed**. Implementation and acceptance: [issue #6](https://github.com/BestNathan/nasset/issues/6).

## Goals and boundaries

- Keep application code, tests, configuration and scheduled collector workflows on `main`.
- Keep immutable exchange market snapshots on a **separate-root `market-data` orphan branch** in this same repository.
- Do not put raw Parquet snapshots under `main` or in the Pages deployment artifact.
- Use Apache Parquet with **internal ZSTD** compression (initial level 6). The filename is `.parquet`, not `.parquet.zst`.
- Collection scope is defined in [`config/market-data.yml`](../config/market-data.yml), starting with BTC/ETH Deribit option chains. PAXG or SOL can be enabled by adding symbols to `underlyings`, without changing code.
- Underlyings, data sources and datasets are **logical classification dimensions**, independent of an investment theme (e.g. gold or crypto).

## Classification and file paths

Use:

```text
<provider>/<dataset>/<underlying>/<YYYY>/<MM>/<UTC-slot>.parquet
```

Example, with a four-hour UTC schedule:

```text
market-data/       # branch name, not a directory
  deribit/
    option_chain/
      BTC/2026/11/2026-11-05T1200Z.parquet
      ETH/2026/11/2026-11-05T1200Z.parquet
      PAXG/2026/11/2026-11-05T1200Z.parquet   # only when enabled
```

The slot in the filename identifies an expected schedule window, **not** the moment when the API was queried. Every row carries the actual `captured_at` time, and Parquet key/value metadata records the first and last capture time. Never backdate newly collected quotes when a scheduled job was missed.

This hierarchy supports adding providers or separate datasets (e.g. `deribit/future/`, `provider/spot/`) later without restructuring the existing option-chain archive. Each dataset owns its own schema; do not normalize incompatible product fields into one giant table.

## Config semantics

`sources.deribit.datasets.option_chain.underlyings` lists **base assets** (BTC, ETH, PAXG), not settlement or quote currencies. The collector must not assume that `currency=BTC` returns every option on BTC.

Deribit derivative API's `currency` parameter filters by **settlement currency**, while many linear options settle in USDC. A safe collection algorithm:

1. Call `public/get_instruments?currency=any&kind=option&expired=false`.
2. Filter returned instruments by their `base_currency` and configured dataset type; build the `instrument_name → metadata` map.
3. Determine the required `settlement_currency` values; call `public/get_book_summary_by_currency` for each, with `kind=option`.
4. Join by **exact** `instrument_name`, de-duplicate repeated matches and partition rows by the matched instrument's base asset.
5. Reject unknown instruments, missing joins or suspiciously empty results; a configured symbol with zero listed contracts requires an explicit documented no-data outcome, not a silent success.

PAXG options are already listed on Deribit, so it is a valid extension of the initial configuration.

Sources: [Deribit instrument API](https://skmtc.dev/deribit/apis/deribit-api-2/docs/public/get_instruments/get) and [PAXG launch](https://insights.deribit.com/education/pax-gold-paxg-from-paxos-launches-on-deribit/).

## Parquet contract

One row per instrument per captured snapshot. Explicit Arrow schema and schema version; do not infer table types from one day's data. Preserve original decimal values as float64 (nullable) without arbitrary pre-quantization. Core columns:

- Identity: `provider`, `dataset`, `instrument_name`, `base_currency`, `quote_currency`, `settlement_currency`, `instrument_type`, `option_type`.
- Contract: `expiration_at` (UTC), `strike`, `contract_size`, `tick_size`.
- Market: `bid_price`, `ask_price`, `mid_price`, `mark_price`, `last_price`, `mark_iv`, `underlying_price`, `underlying_index`, `open_interest`, `volume`, `volume_usd` and current public real-time/delayed volume fields where present.
- Timing: `snapshot_slot_at` and `captured_at` (UTC). Record `schema_version`, provider, dataset, underlying, count, capture bounds and collector version in file metadata.

Keep `NULL` for missing quotes/fields (never coerce to zero). Keep price/volume **native units** and currency annotations; do not silently treat all values as USD. Do not claim book summaries contain official Greeks; their separate acquisition/modeling is a distinct dataset decision. Document any publication lag, particularly open interest, and do not represent lagged values as real-time.

## Scheduling and Git publishing

- Proposed cadence: every four hours, off the hour (UTC 00:37, 04:37, 08:37, 12:37, 16:37, 20:37), plus manual dispatch.
- A job collects **all configured symbols**, validates every generated Parquet, and publishes the batch atomically in one commit. A partial failed batch must not be reported as a full snapshot.
- On first publish, create an orphan root commit with **zero parents** and create `refs/heads/market-data`.
- For subsequent publishes, use GitHub Git Data API to create blobs and a new tree based on the existing tip tree, create a commit with the existing tip as its parent and advance the branch ref without `force`.
- Do not checkout the `market-data` branch or fetch its historical blob objects in the collector. The normal `main` checkout uses depth 1.
- Idempotency is per `provider/dataset/underlying/UTC-slot`: never overwrite a historical file or force-push. Verify **all** configured slot paths are either absent or already a complete identical snapshot; partial or conflicting writes fail loudly.
- Protect against overlapping scheduled/manual runs with workflow concurrency. Avoid retrying API failures as blank or artificially reconstructed historical snapshots.
- The collector needs only a scoped `GITHUB_TOKEN` (`contents: write`) on the same repository; do not store third-party API secrets for public market endpoints.
- The orphan branch is an independent Git history but **shares repository object storage** and GitHub repo-size considerations with `main`. Use shallow/single-branch clones and inspect total repository size over time.

## Consumption and lifecycle

- Historical research reads `market-data` Parquet files via DuckDB/PyArrow after fetching only the requested files/refs, rather than mixing data into the regular code checkout.
- Pages may later publish a deliberately reduced JSON summary; it should not download or parse years of raw option-chain files in the browser.
- Schema changes must be explicit and backward-readable (new `schema=v2` dataset or schema metadata/reader evolution), never silently reinterpret existing columns.
- Monitor actual ZSTD Parquet bytes, number of rows, success time, Git storage growth and missing UTC slots. Validate with real snapshots before treating any estimated per-file size as a guarantee.

## Acceptance

See [issue #6](https://github.com/BestNathan/nasset/issues/6) for the detailed implementation checklist and test fixtures.
