# Pages information architecture

nasset has **three research domains**, not a single flat list of pages or data feeds.

| Domain | Route | User's primary question | Drill-down |
| --- | --- | --- | --- |
| Asset cashflow | `index.html` | What does an asset produce, and what happens to principal? | One asset → valuation / cashflow / income strategies → historical chart |
| Dividend equities | `topics.html` | Which companies have distributed cash consistently? | Industry → company → actual fiscal-year distributions / risk → peer comparison |
| Derivatives | `deribit.html` | What does the options/futures market imply about volatility and convexity? | Product (options / futures) → underlying / settlement family → product-specific analysis (options: IV / OI Greeks / chain; futures: basis curve / contracts) |

## Navigation contract

- Every route has the **same global navigation** in `site-header`, ordered as above; exactly one `aria-current="page"` matches its route. The same header is present without requiring JS or fetched data.
- Local navigation describes **only the current domain**: in-page sections for asset/dividend research, derivative research tabs for Deribit.
- Underlying/asset, cashflow **strategy**, product/settlement **family**, and analytical **view** are different dimensions; do not add another top-level tab just because a new indicator, strategy or dataset appears.
- Avoid duplicating cross-page shortcuts within hero panels. The top nav is the global domain switch.
- Keep the existing relative links and three static file URLs; no SPA or additional build step is necessary.

## Content order and progressive disclosure

- Assets: snapshot summary → selected asset details / strategies → cross-asset comparison → four-layer cashflow-risk model and methodology. `assetNav` is independently scrollable on desktop; clicking a row or asset selection scrolls to asset details.
- Dividends: coverage summary → sector/search/sort and company detail → opt-in comparison of up to five companies → dividend accounting caveats. The methods note is expandable, not a preface occupying the main work area.
- Derivatives: **options vs futures product domain** → underlying/family filter → relevant analytics. Options has IV surface, gross modeled sensitivities/OI and option chain; futures has basis term structure, real contract quotes and a product-specific KPI set. PAXG has futures only (no validated Deribit option chain); unsupported options cannot be selected. Assumptions and risk boundaries remain available in the methods disclosure.

Cross-asset **cash yield** is not the same as total return; modelled gross OI Greeks do not reveal dealer net positioning. Research wording must preserve these distinctions. Keep real data timestamps visible, preserve nulls, and do not introduce fabricated prices, yields or predictions.

## Maintenance / acceptance

- Preserve existing JS render mount IDs and data projection schemas during visual changes.
- Use shared `site-header`, `site-nav`, `section-nav`, `research-section` and `method-disclosure` styles instead of copying a fourth global navigation implementation.
- Test navigation links, current-route semantics, section ordering, control availability, Python/JS smoke suites, and desktop/mobile browser behavior before deployment.
- Deploy with existing GitHub Pages workflows; verify the live `index.html`, `topics.html`, and `deribit.html` routes after merge.
