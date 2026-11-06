# Deribit Market Data — GitHub Pages dashboard

## Why a separate view

The original Pages application is a portfolio cashflow dashboard with a
risk ladder, options income strategies and A-share dividends. The Deribit
view is a distinct data observatory, not another asset type or a duplicate
of those strategy widgets. It shows the raw, timestamped exchange data that
can later feed backtests or seller-executable premium analysis.

## Information architecture

1. **Capture status and summary:** latest UTC slot, archive freshness derived
   from browser time, number of verified slots in the displayed window, total
   option instruments and the most recent compressed batch size.
2. **Overview:** asset selector BTC / ETH / PAXG; real reference indexes
   distinguished by full `index_name` and quote currency; sampled history
   (with non-adjacent slots disconnected); BTC/ETH last closed-hour DVOL;
   latest dataset row coverage and exact reference index values.
3. **Option chain / IV:** BTC/ETH only; DTE and OTM strike-distance matrix
   with mean native `mark_iv` and real sample counts, clickable cells that
   filter a sortable paginated list of native bid/ask quotes and OI.
   A matrix cell is NOT Delta, a cash yield or an executable premium.
4. **Futures:** contracts for BTC, ETH and PAXG, distinguished as dated
   futures or perpetual, listing mark/bid/ask, OI, source volume and
   settlement currency. No synthetic cross-quote basis is calculated.
5. **Archive:** every included UTC slot with links to the Git source of
   its Manifest and Receipt; no fabricated continuity, no empty-hour zeros.

## Data boundary and trust model

Live market capture is owned by `.github/workflows/deribit-archive.yml`,
which publishes immutable Parquet/ZSTD files to a separate `market-data`
orphan branch. Browser code never contacts Deribit or Github APIs directly.
The Pages build reads the actual archive through GitHub Git Data REST with
its ephemeral workflow `GITHUB_TOKEN` (contents:read), pins the exact commit,
verifies the manifest's SHA inventory and the receipt's required paths,
then reads typed Parquet. Each Parquet part must carry the expected
dataset and UTC slot identity. No plain Git token is ever written to site/.
Malformed/missing data aborts the Pages deployment rather than replacing
a working page with a misleading partial snapshot.

Generated JSON lives only in the Pages artifact at
`site/data/deribit-dashboard.json`, not as an hourly source commit on main.
The builder keeps at most the latest 24 recorded UTC slots in the dashboard
view; all older raw data remains on the market-data branch. Each refresh
transfers only the needed recent blobs and latest option/futures snapshots.
Historical volatility/delivery events are available in the raw archive
when due; they are not conflated with hourly exchange quotes.

## Operational refresh

- Daily Pages workflow regenerates the projection after persisting the
  daily asset and dividend history, and before uploading `site/`.
- Hourly Pages workflow is triggered by **successful** archive workflow
  completion on main, checks out trusted main and regenerates exactly the
  same projection. This is a `workflow_run` trust boundary: downstream code
  does not execute upstream artifacts, checkout arbitrary SHA, or access
  untrusted PR content.
- Both Pages jobs serialize under `nasset-pages` and avoid cancelling
  an in-progress deployment. New hourly snapshots require no extra commits
  to main, and Pages deployment does not publish to market-data.
- If the refresh fails, inspect the generated workflow logs and verify the
  archive Git tree/manifest integrity before retrying.

## Future scope

Native option Greeks, settlement event time-series analytics, term-structure
basis models, full data downloads and cross-venue gold options require
separate source contracts and explicit schema/version changes. Do not
back-calculate an unlabelled Delta from moneyness or invent gold options.
