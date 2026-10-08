const state = { snapshot: null, timeline: null, selected: null };

const pct = (v) => v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2) + "%";
const money = (v) => v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const cls = (v) => Number(v) >= 0 ? "good" : "bad";
const signedPct = (v) => (Number(v) >= 0 ? "+" : "") + Number(v).toFixed(1) + "%";
const attr = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll('"', "&quot;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll("\n", "&#10;");

async function boot() {
  const snapshotResponse = await fetch("./data/latest.json", { cache: "no-store" });
  const timelineResponse = await fetch("./data/timeline.json", { cache: "no-store" });
  state.snapshot = await snapshotResponse.json();
  state.timeline = await timelineResponse.json();
  state.selected = state.snapshot.assets[0] ? state.snapshot.assets[0].id : null;
  render();
}

function render() {
  const s = state.snapshot;
  document.getElementById("freshness").innerHTML = "Generated<br><strong>" + new Date(s.generated_at).toLocaleString() + "</strong>";
  renderSummary();
  renderRiskLadder();
  renderYieldLadder();
  renderNav();
  renderDetail();
}

function renderSummary() {
  const assets = state.snapshot.assets;
  const optionAssets = assets.filter(a => (a.strategies || []).some(x => x.kind === "option_matrix"));
  const incomeAssets = assets.filter(a => (a.best_strategy || {}).annualized_cash_yield_pct != null);
  const top = incomeAssets.slice().sort((a,b) =>
    Number((b.best_strategy || {}).annualized_cash_yield_pct || 0) -
    Number((a.best_strategy || {}).annualized_cash_yield_pct || 0)
  )[0];
  const avg = incomeAssets.length
    ? incomeAssets.reduce((x,a) => x + Number((a.best_strategy || {}).annualized_cash_yield_pct || 0), 0) / incomeAssets.length
    : 0;

  document.getElementById("summary").innerHTML = [
    summaryCard("Assets tracked", String(assets.length), "daily snapshots"),
    summaryCard("Option matrices", String(optionAssets.length * 2), "covered call + cash-secured put"),
    summaryCard("Mean cash yield", pct(avg), "cross-asset, not risk-adjusted"),
    summaryCard("Highest selected yield", top ? pct(top.best_strategy.annualized_cash_yield_pct) : "—", top ? top.name : "no data")
  ].join("");
}

function summaryCard(label, value, sub) {
  return '<div class="card"><div class="label">' + label + '</div><div class="value">' +
    value + '</div><div class="sub">' + sub + '</div></div>';
}

function renderRiskLadder() {
  const taxonomy = state.snapshot.taxonomy || [];
  const assets = state.snapshot.assets || [];
  const html = taxonomy.map(layer => {
    const layerAssets = assets.filter(a => a.layer_id === layer.id);
    const yields = layerAssets
      .map(a => Number((a.best_strategy || {}).annualized_cash_yield_pct))
      .filter(Number.isFinite);
    const yieldText = yields.length
      ? Math.min(...yields).toFixed(1) + "% – " + Math.max(...yields).toFixed(1) + "%"
      : "no live asset yet";
    return '<article class="risk-layer risk-' + layer.level + '">' +
      '<div class="risk-layer-top"><span class="risk-number">0' + layer.level + '</span>' +
      '<div><div class="risk-name">' + layer.name + '</div><div class="risk-label">' + layer.risk_label + '</div></div></div>' +
      '<p>' + layer.description + '</p>' +
      '<div class="risk-subs">' + layer.subcategories.map(s => '<span>' + s.name + '</span>').join("") + '</div>' +
      '<div class="risk-stats"><strong>' + yieldText + '</strong><span>' + layerAssets.length + ' tracked assets</span></div>' +
    '</article>';
  }).join("");

  document.getElementById("riskLadder").innerHTML =
    '<div class="risk-intro"><div><div class="eyebrow">CASHFLOW RISK LADDER</div>' +
    '<h2>Contractual cashflow → engineered yield</h2></div>' +
    '<p>This is a conceptual progression in cashflow complexity and actively assumed risk, not a universal probability-of-loss rating. Duration, leverage and valuation can still make a lower layer volatile.</p></div>' +
    '<div class="risk-grid">' + html + '</div>';
}

function renderYieldLadder() {
  const assets = (state.snapshot.assets || []).slice().sort((a, b) => {
    if (a.risk_level !== b.risk_level) return a.risk_level - b.risk_level;
    return Number((b.best_strategy || {}).annualized_cash_yield_pct || -Infinity) -
      Number((a.best_strategy || {}).annualized_cash_yield_pct || -Infinity);
  });

  const rows = assets.map(asset => {
    const v = asset.valuation || {};
    const best = asset.best_strategy || {};
    const localReturn = v.quote_currency && v.quote_currency !== "USD" && v.local_price_return_pct != null
      ? pct(v.local_price_return_pct) + " " + v.quote_currency
      : "—";
    const usdReturn = v.price_return_pct != null ? pct(v.price_return_pct) : "—";
    const total = v.annualized_total_return_pct != null ? pct(v.annualized_total_return_pct) : "—";
    const cash = best.annualized_cash_yield_pct != null ? pct(best.annualized_cash_yield_pct) : "—";
    return '<tr class="yield-row" data-asset="' + asset.id + '">' +
      '<td><span class="layer-pill">L' + asset.risk_level + '</span></td>' +
      '<td class="yield-asset"><strong>' + asset.name + '</strong><small>' + asset.subcategory_name + ' · ' + asset.market + '</small></td>' +
      '<td>' + cash + '</td>' +
      '<td class="' + cls(v.price_return_pct) + '">' + usdReturn + '</td>' +
      '<td>' + localReturn + '</td>' +
      '<td class="' + cls(v.annualized_total_return_pct) + '">' + total + '</td>' +
      '<td>' + asset.source_cadence + '</td>' +
    '</tr>';
  }).join("");

  document.getElementById("yieldLadder").innerHTML =
    '<div class="yield-head"><div><div class="eyebrow">CASHFLOW OPPORTUNITY SET</div><h2>Yield by risk layer</h2></div>' +
    '<p>Cash yield is shown separately from principal movement. Click any row to open the asset view. Option overlays show the currently selected risk-adjusted matrix cell.</p></div>' +
    '<div class="yield-table-wrap"><table class="yield-table"><thead><tr>' +
      '<th>Layer</th><th>Asset</th><th>Cash Yield</th><th>Asset Δ USD</th><th>Asset Δ Local</th><th>Total CAGR USD</th><th>Source</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>';

  document.querySelectorAll(".yield-row").forEach(row => {
    row.addEventListener("click", () => selectAsset(row.dataset.asset));
  });
}


function renderNav() {
  const taxonomy = state.snapshot.taxonomy || [];
  const assets = state.snapshot.assets || [];

  if (!taxonomy.length) {
    document.getElementById("assetNav").innerHTML = assets.map(renderAssetButton).join("");
  } else {
    document.getElementById("assetNav").innerHTML = taxonomy.map(layer => {
      const groups = layer.subcategories.map(subcategory => {
        const groupAssets = assets.filter(a =>
          a.layer_id === layer.id && a.subcategory_id === subcategory.id
        );
        if (!groupAssets.length) return "";
        return '<div class="nav-subgroup"><div class="nav-subtitle">' + subcategory.name + '</div>' +
          groupAssets.map(renderAssetButton).join("") + '</div>';
      }).join("");
      if (!groups) return "";
      return '<section class="nav-layer"><div class="nav-layer-title"><span>L' + layer.level + '</span>' +
        layer.name + '</div>' + groups + '</section>';
    }).join("");
  }

  const nav = document.getElementById("assetNav");
  nav.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => selectAsset(btn.dataset.id));
  });
  ensureSelectedAssetVisible(nav);
}

function selectAsset(assetId) {
  state.selected = assetId;
  renderNav();
  renderDetail();

  const detail = document.getElementById("detail");
  if (detail) {
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function ensureSelectedAssetVisible(nav) {
  const active = nav.querySelector(".asset-btn.active");
  if (!active) return;

  const navTop = nav.scrollTop;
  const navBottom = navTop + nav.clientHeight;
  const itemTop = active.offsetTop;
  const itemBottom = itemTop + active.offsetHeight;
  const margin = 16;

  if (itemTop < navTop + margin) {
    nav.scrollTo({ top: Math.max(0, itemTop - margin), behavior: "smooth" });
  } else if (itemBottom > navBottom - margin) {
    nav.scrollTo({
      top: Math.max(0, itemBottom - nav.clientHeight + margin),
      behavior: "smooth"
    });
  }
}

function renderAssetButton(a) {
  const active = a.id === state.selected ? " active" : "";
  return '<button class="asset-btn' + active + '" data-id="' + a.id + '">' +
    a.name + '<small>' + a.symbol + ' · ' + a.market + '</small></button>';
}

function renderDetail() {
  const asset = state.snapshot.assets.find(a => a.id === state.selected);
  if (!asset) return;
  const v = asset.valuation;
  const best = asset.best_strategy || {};
  const history = ((state.timeline.assets || {})[asset.id] || []);
  const strategyHtml = (asset.strategies || []).map(renderStrategy).join("");
  const optionGuide = renderOptionGuide(asset);
  const localMarket = v.quote_currency && v.quote_currency !== "USD"
    ? " · Local " + v.quote_currency + " " + money(v.local_current_price) +
      " · FX " + Number(v.fx_to_usd).toFixed(6) + " USD/" + v.quote_currency
    : "";
  const freshness = asset.stale ? "STALE · " : "";
  const unit = v.valuation_unit || asset.unit || "unit";
  const unitSuffix = unit ? " / " + unit : "";
  const sourceLine = v.source
    ? '<div class="source-line">Source: ' + v.source +
      (v.source_effective_date ? ' · effective ' + v.source_effective_date : '') +
      (v.source_cadence ? ' · ' + v.source_cadence + ' source' : '') + '</div>'
    : "";
  const classification = '<div class="asset-tags">' +
    '<span>L' + asset.risk_level + ' · ' + asset.layer_name + '</span>' +
    '<span>' + asset.subcategory_name + '</span>' +
    '<span>' + asset.market + '</span>' +
    '<span>' + asset.source_cadence + ' source</span>' +
    '</div>';
  const valuationNotes = (v.notes || []).length
    ? '<div class="asset-notes">' + v.notes.map(note => '<p>' + note + '</p>').join("") + '</div>'
    : "";
  const localMetric = v.quote_currency && v.quote_currency !== "USD" && v.local_price_return_pct != null
    ? metric("Asset change (" + v.quote_currency + ")", pct(v.local_price_return_pct), cls(v.local_price_return_pct))
    : "";

  document.getElementById("detail").innerHTML =
    '<div class="detail-head"><div><h2>' + asset.name + '</h2><div class="muted">' +
    asset.description + localMarket + '</div>' + classification + sourceLine +
    '</div><div class="badge">' + freshness + asset.measurement_window_days + 'D window</div></div>' +
    '<div class="metrics">' +
      metric("Current price (USD" + unitSuffix + ")", "$" + money(v.current_price), "") +
      localMetric +
      metric(v.quote_currency && v.quote_currency !== "USD" ? "Asset change (USD)" : "Asset value change", pct(v.price_return_pct), cls(v.price_return_pct)) +
      metric("Measured cash yield", pct(v.annualized_cash_yield_pct), "") +
      metric("Total return CAGR (USD)", pct(v.annualized_total_return_pct), cls(v.annualized_total_return_pct)) +
    '</div>' +
    valuationNotes +
    renderBest(best) +
    optionGuide +
    strategyHtml +
    '<h3 class="section-title">Daily history</h3>' +
    '<div class="charts"><div class="chart-card"><h4>Current asset value index</h4>' +
      lineChart(history, "current_asset_value_index", "line-a") +
      '</div><div class="chart-card"><h4>Best cashflow annualized yield</h4>' +
      lineChart(history, "annualized_cash_yield_pct", "line-b") +
      '</div></div>' +
    '<div class="method">Window: ' + v.start_date + ' → ' + v.end_date +
      '. Start with 100, buy once, do not reinvest distributions. Slow assets may use monthly source observations while the repository still records a daily snapshot. Option income uses a seller-executable premium proxy and is mechanically annualized; the risk score is only a relative ranking tool.</div>' +
    renderErrors(asset.id);
}

function metric(label, value, klass) {
  return '<div class="metric"><div class="label">' + label + '</div><div class="num ' +
    (klass || "") + '">' + value + '</div></div>';
}

function renderOptionGuide(asset) {
  const hasOptions = (asset.strategies || []).some(strategy => strategy.kind === "option_matrix");
  if (!hasOptions) return "";

  const bands = [
    ["5Δ", "Tail harvesting", "Very far OTM. Low premium, large price buffer, minimal convexity sold."],
    ["10Δ", "Conservative income", "Far OTM. A common long-horizon income bucket with meaningful buffer."],
    ["15Δ", "Balanced income", "More premium, but the strike starts moving materially closer to spot."],
    ["20Δ", "Income / short vol", "Higher cashflow with noticeably more assignment and convexity risk."],
    ["25Δ", "Active short vol", "Aggressive. The strike is relatively close to spot; treat it as an active volatility trade."]
  ];

  return '<section class="option-guide">' +
    '<div class="option-guide-head"><div><div class="label">How to read the option matrix</div>' +
    '<h3>DTE × Delta stays fixed; price location moves with the market.</h3></div>' +
    '<div class="guide-formula"><span>Delta ↑</span><b>→</b><span>strike closer</span><b>→</b><span>premium ↑</span><b>→</b><span>convexity sold ↑</span></div></div>' +
    '<div class="guide-copy"><p><strong>DTE</strong> is days to expiry. <strong>Delta</strong> is the option sensitivity bucket: a 5Δ call is roughly +0.05 delta and a 5Δ put roughly −0.05 delta. For income strategies, lower delta generally means a farther OTM strike and more price room.</p>' +
    '<p>Delta is useful because it already reflects spot, strike, volatility and time. It can be used as a rough risk coordinate, but <strong>5Δ does not mean “95% guaranteed win”</strong> and is not a literal true probability.</p>' +
    '<p class="guide-hint">Hover a Delta column header to see today’s approximate strike / price location for every DTE in that column. Hover a matrix cell for the exact selected contract.</p></div>' +
    '<div class="delta-bands">' +
      bands.map(([delta, title, text]) =>
        '<div class="delta-band"><div class="delta-band-top"><strong>' + delta + '</strong><span>' + title + '</span></div><p>' + text + '</p></div>'
      ).join("") +
    '</div></section>';
}

function deltaHeaderTooltip(matrix, targetDelta) {
  const side = matrix.option_type === "call" ? "Call" : "Put";
  const lines = matrix.target_dtes.map(targetDte => {
    const cell = matrix.cells.find(c =>
      c.target_dte === targetDte &&
      c.target_delta_pct === targetDelta &&
      c.available
    );
    if (!cell || !cell.spot || !cell.strike) return targetDte + "D: no liquid match";
    const distance = (Number(cell.strike) / Number(cell.spot) - 1) * 100;
    return targetDte + "D: ≈ $" + money(cell.strike) + " (" + signedPct(distance) + " vs spot)";
  });

  return targetDelta + "Δ " + side +
    "\nApproximate current strike locations:" +
    "\n" + lines.join("\n") +
    "\n\nDelta is a standardized risk bucket, not a guaranteed ITM probability.";
}

function matrixCellTooltip(cell) {
  const distance = cell.spot && cell.strike
    ? (Number(cell.strike) / Number(cell.spot) - 1) * 100
    : null;
  return [
    cell.instrument || "Selected option",
    "Strike: $" + money(cell.strike) + (distance == null ? "" : " (" + signedPct(distance) + " vs spot)"),
    "Spot: $" + money(cell.spot),
    "Actual delta: " + (Number(cell.delta_abs) * 100).toFixed(1) + "Δ",
    "IV: " + pct(cell.iv_pct),
    "DTE: " + Number(cell.dte).toFixed(1),
    "Seller premium: " + money(cell.premium) + " " + (cell.premium_currency || ""),
    "Mechanical annualized yield: " + pct(cell.annualized_yield_pct),
    "Source: " + (cell.source || "market data")
  ].join("\n");
}

function renderBest(best) {
  if (!best || !best.strategy_name) return "";
  const detail = best.dte
    ? Math.round(best.dte) + ' DTE · ' + (Number(best.delta_abs) * 100).toFixed(1) + 'Δ · strike ' + money(best.strike)
    : "measured distribution income";
  return '<div class="best"><div><div class="label">Current risk-adjusted choice</div><div class="big">' +
    best.strategy_name + '</div><div class="muted">' + detail +
    '</div></div><div><div class="label">Annualized cash yield</div><div class="big"><strong>' +
    pct(best.annualized_cash_yield_pct) + '</strong></div></div></div>';
}

function renderStrategy(strategy) {
  if (strategy.kind === "income") {
    const basis = strategy.yield_basis || "historical cash distributions";
    const source = strategy.source ? " · " + strategy.source : "";
    return '<h3 class="section-title">' + strategy.name +
      '</h3><div class="card"><div class="label">Annualized cash yield / APR</div><div class="value">' +
      pct(strategy.annualized_yield_pct) + '</div><div class="sub">' +
      basis + source + '</div></div>';
  }

  const matrix = strategy.matrix;
  const best = matrix.best_risk_adjusted || {};
  const maxYield = Math.max(1, ...matrix.cells.filter(c => c.available).map(c => Number(c.annualized_yield_pct || 0)));
  const head = '<tr><th class="axis-help" title="DTE = days to expiry. Shorter DTE usually means more gamma risk; longer DTE locks the position for more time.">DTE / Delta</th>' +
    matrix.target_deltas_pct.map(d =>
      '<th class="delta-head" title="' + attr(deltaHeaderTooltip(matrix, d)) + '">' +
      d + 'Δ <span class="hover-mark">⌁</span></th>'
    ).join("") + '</tr>';

  const rows = matrix.target_dtes.map(dte => {
    const cells = matrix.target_deltas_pct.map(delta => {
      const cell = matrix.cells.find(c => c.target_dte === dte && c.target_delta_pct === delta);
      if (!cell || !cell.available) return '<td>—</td>';
      const heat = Math.round(Math.min(42, Math.max(5, Number(cell.annualized_yield_pct) / maxYield * 42)));
      const isBest = best.instrument === cell.instrument &&
        best.target_dte === cell.target_dte &&
        best.target_delta_pct === cell.target_delta_pct;
      return '<td class="heat' + (isBest ? ' best-cell' : '') +
        '" title="' + attr(matrixCellTooltip(cell)) + '" style="--heat:' + heat + '%">' + pct(cell.annualized_yield_pct) +
        '<small>' + Number(cell.delta_abs * 100).toFixed(1) + 'Δ · ' +
        Math.round(cell.dte) + 'D</small></td>';
    }).join("");
    return '<tr><td>' + dte + 'D</td>' + cells + '</tr>';
  }).join("");

  return '<h3 class="section-title">' + strategy.name +
    '</h3><div class="matrix-wrap"><table>' + head + rows +
    '</table></div><div class="method">Outlined cell = highest current risk-adjusted score. Matrix values are mechanically annualized premium yields. Highest premium alone is intentionally not treated as optimal.</div>';
}

function lineChart(rows, key, lineClass) {
  const clean = rows.filter(r => r[key] != null && Number.isFinite(Number(r[key])));
  if (clean.length < 2) {
    return '<div class="muted" style="padding:80px 8px;text-align:center">History starts accumulating after the first daily run.</div>';
  }
  const w = 720, h = 210, padX = 36, padY = 24;
  const vals = clean.map(r => Number(r[key]));
  let min = Math.min(...vals), max = Math.max(...vals);
  if (min === max) { min -= 1; max += 1; }
  const x = i => padX + i * (w - 2 * padX) / Math.max(1, clean.length - 1);
  const y = v => h - padY - (v - min) * (h - 2 * padY) / (max - min);
  const points = clean.map((r,i) => x(i).toFixed(1) + "," + y(Number(r[key])).toFixed(1)).join(" ");
  let grid = "";
  for (let i=0;i<4;i++) {
    const gy = padY + i * (h - 2 * padY) / 3;
    grid += '<line class="grid-line" x1="' + padX + '" y1="' + gy +
      '" x2="' + (w-padX) + '" y2="' + gy + '"/>';
  }
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img">' +
    grid + '<polyline class="' + lineClass + '" points="' + points + '"/>' +
    '<text class="axis-label" x="' + padX + '" y="' + (h-4) + '">' + clean[0].date + '</text>' +
    '<text class="axis-label" text-anchor="end" x="' + (w-padX) + '" y="' + (h-4) + '">' +
      clean[clean.length-1].date + '</text>' +
    '<text class="axis-label" x="4" y="' + (padY+4) + '">' + max.toFixed(2) + '</text>' +
    '<text class="axis-label" x="4" y="' + (h-padY+4) + '">' + min.toFixed(2) + '</text></svg>';
}

function renderErrors(assetId) {
  const errors = (state.snapshot.errors || []).filter(e => e.asset_id === assetId);
  return errors.map(e => '<div class="error">' + e.error + '</div>').join("");
}

boot().catch(err => {
  document.body.innerHTML = '<main class="shell"><div class="error">Failed to load dashboard data: ' +
    String(err) + '</div></main>';
});
