const state = { snapshot: null, timeline: null, selected: null };

const pct = (v) => v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2) + "%";
const money = (v) => v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const cls = (v) => Number(v) >= 0 ? "good" : "bad";

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
    summaryCard("Highest current", top ? pct(top.best_strategy.annualized_cash_yield_pct) : "—", top ? top.name : "no data")
  ].join("");
}

function summaryCard(label, value, sub) {
  return '<div class="card"><div class="label">' + label + '</div><div class="value">' +
    value + '</div><div class="sub">' + sub + '</div></div>';
}

function renderNav() {
  const html = state.snapshot.assets.map(a => {
    const active = a.id === state.selected ? " active" : "";
    return '<button class="asset-btn' + active + '" data-id="' + a.id + '">' +
      a.name + '<small>' + a.symbol + ' · ' + a.category + '</small></button>';
  }).join("");
  const nav = document.getElementById("assetNav");
  nav.innerHTML = html;
  nav.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      state.selected = btn.dataset.id;
      renderNav();
      renderDetail();
    });
  });
}

function renderDetail() {
  const asset = state.snapshot.assets.find(a => a.id === state.selected);
  if (!asset) return;
  const v = asset.valuation;
  const best = asset.best_strategy || {};
  const history = ((state.timeline.assets || {})[asset.id] || []);
  const strategyHtml = (asset.strategies || []).map(renderStrategy).join("");

  document.getElementById("detail").innerHTML =
    '<div class="detail-head"><div><h2>' + asset.name + '</h2><div class="muted">' +
    asset.description + '</div></div><div class="badge">' + asset.measurement_window_days + 'D window</div></div>' +
    '<div class="metrics">' +
      metric("Current price", money(v.current_price), "") +
      metric("Asset value change", pct(v.price_return_pct), cls(v.price_return_pct)) +
      metric("Cash distributions", pct(v.annualized_cash_yield_pct), "") +
      metric("Total return CAGR", pct(v.annualized_total_return_pct), cls(v.annualized_total_return_pct)) +
    '</div>' +
    renderBest(best) +
    strategyHtml +
    '<h3 class="section-title">Daily history</h3>' +
    '<div class="charts"><div class="chart-card"><h4>Current asset value index</h4>' +
      lineChart(history, "current_asset_value_index", "line-a") +
      '</div><div class="chart-card"><h4>Best cashflow annualized yield</h4>' +
      lineChart(history, "annualized_cash_yield_pct", "line-b") +
      '</div></div>' +
    '<div class="method">Window: ' + v.start_date + ' → ' + v.end_date +
      '. Start with 100, buy once, do not reinvest distributions. Option income uses a seller-executable premium proxy and is mechanically annualized; the risk score is only a relative ranking tool.</div>' +
    renderErrors(asset.id);
}

function metric(label, value, klass) {
  return '<div class="metric"><div class="label">' + label + '</div><div class="num ' +
    (klass || "") + '">' + value + '</div></div>';
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
    return '<h3 class="section-title">' + strategy.name +
      '</h3><div class="card"><div class="label">Measured annualized distribution yield</div><div class="value">' +
      pct(strategy.annualized_yield_pct) + '</div><div class="sub">' +
      strategy.measurement_window_days + 'D measurement window</div></div>';
  }

  const matrix = strategy.matrix;
  const best = matrix.best_risk_adjusted || {};
  const maxYield = Math.max(1, ...matrix.cells.filter(c => c.available).map(c => Number(c.annualized_yield_pct || 0)));
  const head = '<tr><th>DTE / Delta</th>' +
    matrix.target_deltas_pct.map(d => '<th>' + d + 'Δ</th>').join("") + '</tr>';

  const rows = matrix.target_dtes.map(dte => {
    const cells = matrix.target_deltas_pct.map(delta => {
      const cell = matrix.cells.find(c => c.target_dte === dte && c.target_delta_pct === delta);
      if (!cell || !cell.available) return '<td>—</td>';
      const heat = Math.round(Math.min(42, Math.max(5, Number(cell.annualized_yield_pct) / maxYield * 42)));
      const isBest = best.instrument === cell.instrument &&
        best.target_dte === cell.target_dte &&
        best.target_delta_pct === cell.target_delta_pct;
      return '<td class="heat' + (isBest ? ' best-cell' : '') +
        '" style="--heat:' + heat + '%">' + pct(cell.annualized_yield_pct) +
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
