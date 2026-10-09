"use strict";

const state = {
  universe: [], stocks: {}, payload: null, sector: "all", group: "all", sort: "name",
  search: "", selected: null, compare: new Set(), reinvest: false, error: null
};
const sectors = { banking: "银行", telecom: "电信运营商", infrastructure: "基础设施" };
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const valid = value => value !== null && value !== undefined && Number.isFinite(Number(value));
const percent = value => valid(value) ? Number(value).toFixed(2) + "%" : "—";
const cny = value => valid(value) ? "¥" + Number(value).toLocaleString("zh-CN", {maximumFractionDigits:2}) : "—";
const decimal = (value, precision=4) => valid(value) ? Number(value).toFixed(precision) : "—";
const data = row => state.stocks[row?.symbol] || null;
const stats = row => data(row)?.statistics || {};
const metric = (name, value, note="") => '<div class="metric"><div class="label">' +
  escapeHtml(name) + '</div><div class="num">' + escapeHtml(value) +
  '</div>' + (note ? '<small>' + escapeHtml(note) + '</small>' : '') + '</div>';
const card = (label, value, sub) => '<div class="card"><div class="label">' + label +
  '</div><div class="value">' + value + '</div><div class="sub">' + sub + '</div></div>';

function filtered() {
  const q = state.search.trim().toLowerCase();
  const rows = state.universe.filter(row =>
    (state.sector === "all" || row.sector === state.sector) &&
    (state.group === "all" || row.group === state.group) &&
    (!q || row.name.toLowerCase().includes(q) || row.symbol.toLowerCase().includes(q))
  );
  const key = state.sort;
  if (key !== "name") {
    const field = row => key === "yield" ? data(row)?.implemented_fy_yield_pct :
      key === "cagr" ? stats(row).cagr_pct :
      key === "cuts" ? stats(row).observed_adjacent_pairs ? stats(row).observed_cuts : null :
      data(row)?.risk?.max_drawdown_pct;
    rows.sort((a,b) => {
      const av=field(a), bv=field(b);
      if (!valid(av)) return valid(bv) ? 1 : a.name.localeCompare(b.name,"zh");
      if (!valid(bv)) return -1;
      const direction = key === "cuts" ? -1 : 1;
      return (Number(bv) - Number(av))*direction || a.name.localeCompare(b.name,"zh");
    });
  }
  return rows;
}

function renderFilters() {
  $("topic-industries").innerHTML = [["all","全部行业"], ...Object.entries(sectors)]
    .map(([key,title]) => '<button class="topic-filter' +
      (state.sector === key ? ' active' : '') + '" data-sector="' + key +
      '" aria-pressed="' + (state.sector === key) + '">' + title + '</button>').join("");
  document.querySelectorAll("[data-sector]").forEach(btn => btn.onclick=()=> {
    state.sector=btn.dataset.sector;state.group="all";state.selected=null;render();
  });
  const groups = [...new Set(state.universe.filter(row => state.sector === "all" || row.sector === state.sector)
    .map(row => row.group))];
  $("topic-group").innerHTML = '<option value="all">全部子行业</option>' +
    groups.map(g => '<option value="' + escapeHtml(g) + '">' + escapeHtml(g) + '</option>').join("");
  $("topic-group").value = groups.includes(state.group) ? state.group : "all";
  $("topic-sort").value = state.sort;
}

function renderStats(visible) {
  const total = state.universe.length;
  const observed = Object.values(state.stocks).filter(s => s.status === "observed").length;
  const priced = Object.values(state.stocks).filter(s => s.price?.close_cny != null).length;
  const banks = state.universe.filter(row => row.sector === "banking").length;
  $("topic-stats").innerHTML = [
    card("A股研究池", total, banks + " 家银行 · 3 家运营商 · " + (total-banks-3) + " 家基建"),
    card("已取得分红", observed + "/" + total, "按实际实施、按报告财年归属"),
    card("已有收盘价", priced + "/" + total, "以行情日期为准，缺失不填0"),
    card("当前筛选", visible.length, "可勾选最多5家公司进行对比")
  ].join("");
  if (state.payload?.generated_at) {
    $("topic-freshness").textContent = "数据生成：" +
      new Date(state.payload.generated_at).toLocaleString("zh-CN", {hour12:false});
  } else {
    $("topic-freshness").textContent = state.error || "历史数据尚未生成";
  }
}

function renderNav(rows) {
  const nav=$("topic-nav");
  nav.innerHTML = rows.map(row => {
    const yieldPct=data(row)?.implemented_fy_yield_pct;
    return '<div class="topic-nav-row"><button class="asset-btn' +
      (row.symbol===state.selected?' active':'') + '" data-select="' + escapeHtml(row.symbol) + '">' +
      '<span class="topic-item"><span>' + escapeHtml(row.name) +
      '<small>' + escapeHtml(row.group) + ' · ' + escapeHtml(row.symbol) +
      '</small></span><em>' + percent(yieldPct) + '</em></span></button>' +
      '<label class="topic-compare-check" title="加入跨公司比较"><input type="checkbox" data-compare="' +
      escapeHtml(row.symbol) + '" '+(state.compare.has(row.symbol)?'checked':'')+' aria-label="对比' +
      escapeHtml(row.name)+'"></label></div>';
  }).join("") || '<div class="topic-empty">无匹配证券</div>';
  nav.querySelectorAll("[data-select]").forEach(btn => btn.onclick = () => {
    state.selected=btn.dataset.select;
    renderDetail();
    renderNav(rows);
    $("topic-detail").scrollIntoView({behavior:"smooth",block:"start"});
  });
  nav.querySelectorAll("[data-compare]").forEach(input => input.onchange = () => {
    if(input.checked && state.compare.size>=5){
      input.checked=false;
      window.alert("最多对比5家公司");
      return;
    }
    if(input.checked)state.compare.add(input.dataset.compare);
    else state.compare.delete(input.dataset.compare);
    renderCompare();
  });
}

function bars(years, kind="dividend") {
  const entries = Object.entries(years || {}).filter(([year,value])=>
    Number(year)>=2015 && Number(year)<=(kind==="cash"?new Date().getFullYear():2025) && valid(value)
  ).sort(([a],[b])=>Number(a)-Number(b));
  if (!entries.length) return '<p class="topic-empty">该时间区间尚无可核验记录</p>';
  const max = Math.max(...entries.map(([,v]) => Number(v)), 0.00001);
  return '<div class="topic-bar-list">' + entries.map(([year,val])=>{
    const value=Number(val);
    return '<div class="topic-bar-row"><span>'+year+'</span><div class="topic-bar-track"><div class="topic-bar-fill" style="width:'+
      Math.max(0,Math.min(100,value/max*100)).toFixed(2)+'%"></div></div><strong>'+
      (kind==="dividend" ? value.toFixed(4)+"元" : cny(value))+'</strong></div>';
  }).join("") + '</div>';
}

function renderMetrics(row, entry) {
  const summary=entry?.statistics || {}, risk=entry?.risk || {}, price=entry?.price || {};
  const cash=entry?.simulation || {};
  return '<div class="metrics topic-metrics">' +
    metric("2025已实施股息率",percent(entry?.implemented_fy_yield_pct),
      "税前 · 仅已实施 · 收盘价"+(price.as_of||"未知")) +
    metric("分红CAGR (2015—25)",percent(summary.cagr_pct),
      "仅11年完整且无未经调整送转股") +
    metric("已观测减息",valid(summary.observed_cuts)&&summary.observed_adjacent_pairs?
      summary.observed_cuts + " 次 / " + summary.observed_adjacent_pairs + " 对":"—",
      "相邻财年样本；不等于未来减息概率") +
    metric("最大回撤",percent(risk.max_drawdown_pct),"Yahoo复权总回报序列") +
    metric("当前股价",cny(price.close_cny),"A股 · CNY · "+(price.as_of||"无日期")) +
    metric("近12个月现金股息率",percent(entry?.ttm_cash_yield_pct),"除息日最近365天 / 当前股价") +
    metric("初始100万元累计现金",cny(cash.cumulative_cash_cny),"非再投资，税费未扣") +
    metric(state.reinvest?"再投资总回报":"总回报（未再投）",
      percent(state.reinvest?cash.reinvested?.total_return_pct:cash.total_return_pct_no_reinvest),
      "当前市值"+(state.reinvest?"（股息复购）":"+累计分红")+"，税前") +
    metric("2025投入成本股息率",percent(cash.calendar_2025_yield_on_cost_pct),"2025自然年现金分红 / 初始买入成本") +
    metric("连续有现金股息",valid(summary.consecutive_years_to_2025)?
      summary.consecutive_years_to_2025+" 年":"—","截至已实施 FY2025；不预测未来") +
    '</div>';
}

function rollingTable(risk) {
  const rolling=risk?.rolling_returns || {};
  const rows=[1,3,5,10].map(year => {
    const e=rolling[String(year)];
    return '<tr><td>'+year+'年</td><td>'+(e?.n||"—")+'</td><td>'+
      percent(e?.p05_pct)+'</td><td>'+percent(e?.median_pct)+
      '</td><td>'+percent(e?.p95_pct)+'</td></tr>';
  }).join("");
  return '<div class="topic-table-scroll"><table class="topic-table"><thead><tr><th>持有期</th><th>滚动样本数</th><th>P05</th><th>中位数</th><th>P95</th></tr></thead><tbody>'+
    rows+'</tbody></table></div>';
}

function financePanel(row, entry) {
  const f=entry?.financials||{};
  const bank=row.sector==="banking";
  const labels=bank ? [
    ["净息差","net_interest_margin_pct","%"],["不良贷款率","nonperforming_loan_pct","%"],
    ["资本充足率","capital_adequacy_pct","%"],["ROE","roe_pct","%"]
  ] : [
    ["归母净利润","net_profit_cny","元"],["经营现金流","operating_cashflow_cny","元"],
    ["ROE","roe_pct","%"]
  ];
  return '<div class="topic-finance">'+labels.map(([label,key,unit])=>
    '<div class="card"><div class="label">'+label+'</div><div class="value">'+
    (valid(f[key]) ? (unit==="%" ? percent(f[key]) : cny(f[key])) : "—") +
    '</div></div>').join("")+'</div><p class="topic-muted">来源：'+
    escapeHtml(f.source || "暂无可核实的财务数据")+
    (f.fiscal_year ? " · FY"+escapeHtml(f.fiscal_year) : "")+
    '。非银行公司自由现金流覆盖、资本开支和高速公路收费期限数据尚未接入时不作推断。</p>';
}

function renderDetail() {
  const row=state.universe.find(x=>x.symbol===state.selected);
  if(!row){$("topic-detail").innerHTML='<p class="topic-empty">选择公司查看数据</p>';return;}
  const entry=data(row),stat=entry?.statistics||{},risk=entry?.risk||{},sim=entry?.simulation||{};
  const sourceLink="https://data.eastmoney.com/yjfp/detail/"+row.symbol.split(".")[0]+".html";
  const status=entry?.status==="observed"?"已取得实施分红":entry?.status==="stale"?"历史分红缓存": "待获取数据";
  $("topic-detail").innerHTML=
    '<div class="detail-head"><div><div class="topic-label">'+escapeHtml(sectors[row.sector])+
      ' / '+escapeHtml(row.group)+'</div><h2>'+escapeHtml(row.name)+'</h2>'+
      '<div class="muted">'+escapeHtml(row.symbol)+' · A股 · 人民币</div></div>'+
      '<span class="badge">'+status+'</span></div>'+
    renderMetrics(row,entry)+
    '<section class="topic-section"><h3>逐财年现金分红 / 每股税前</h3>'+
    bars(entry?.years)+
    '<p class="topic-muted">按照 REPORT_DATE 财年合计已实施的中期和末期派息。缺失年表示尚未取得可核实记录，不等于零分红。'+
    (stat.share_adjustment_applied?'历史分红已按送股/转增折算为可比份额单位；原始每股金额保留于事件数据。':(stat.share_adjustment_required?'存在未调整股本变动，CAGR不可比。':''))+'</p>'+
    (entry?.events||[]).filter(e=>e.verified_exception).map(e=>'<p class="topic-muted">已按普通A股持有者口径核验调整 FY'+e.fiscal_year+'：'+escapeHtml(e.verified_exception.reason)+' <a target="_blank" rel="noopener" href="'+escapeHtml(e.verified_exception.source_url)+'">实施公告 ↗</a></p>').join("")+
    '<div class="topic-year-tags">缺失财年：'+(stat.missing_fiscal_years?.join("、")||"—")+'</div></section>'+
    '<section class="topic-section"><h3>100万元买入持有 · 现金流与复购</h3>'+
    '<div class="topic-toggle" role="group" aria-label="股息再投资"><button data-reinvest="no" class="topic-filter'+(!state.reinvest?' active':'')+'">分红取现</button><button data-reinvest="yes" class="topic-filter'+(state.reinvest?' active':'')+'">股息再投资</button></div>'+
    bars(state.reinvest?sim.reinvested?.cashflow_by_payment_year_cny:sim.cashflow_by_payment_year_cny,"cash")+
    '<p class="topic-muted">买入日 '+escapeHtml(sim.start_date||"未知")+
    '；初始股数 '+escapeHtml(sim.initial_shares??"—")+
    '；初始投入 '+cny(sim.invested_cny)+
    '；当前持股估值 '+cny(sim.latest_position_value_cny)+
    '。按除息支付年份统计，100股整手买入，税前、送转按公开事件调整股数。'+
    (state.reinvest?'当前再投持股 '+decimal(sim.reinvested?.current_shares_estimated,0)+' 股，未使用现金 '+cny(sim.reinvested?.leftover_cash_cny)+'；现金分红按交易日收盘价以100股整数倍复购。':'历年分红直接取现，不再投资。')+'</p></section>'+
    '<section class="topic-section"><h3>风险与滚动总收益分布</h3>'+
    '<div class="metrics">'+metric("年化波动率",percent(risk.annual_volatility_pct))+metric("日收益CVaR 95%",percent(risk.daily_cvar_95_pct))+
       metric("Sortino (Rf=0)",valid(risk.sortino_zero_rf)?decimal(risk.sortino_zero_rf,2):"—")+
       metric("日收益观测数",risk.observations??"—")+'</div>'+
    rollingTable(risk)+'<p class="topic-muted">Yahoo复权收盘价近似现金股息再投资总回报。滚动窗口按252交易日/年，重叠窗口的样本并非独立观测；数据不足返回“—”。</p></section>'+
    '<section class="topic-section"><h3>行业基本面 · 分红支付能力</h3>'+
    financePanel(row,entry)+'</section>'+
    '<section class="topic-section"><h3>来源与质量</h3><p class="topic-muted">'+
      '<a href="'+sourceLink+'" target="_blank" rel="noopener">东方财富：分红送配明细 ↗</a>'+
      ' · Yahoo Finance：市场行情及Adj Close。'+
      ' 数据只涵盖A股；特别分红需结合公告单独核实，静态股息率不等于可预测回报。</p>'+
      (entry?.errors?.length?'<p class="topic-error">'+entry.errors.map(escapeHtml).join("；")+'</p>':'')+
      '</section>';
  document.querySelectorAll("[data-reinvest]").forEach(button=>{
    button.onclick=()=>{state.reinvest=button.dataset.reinvest==="yes";renderDetail();};
  });
}

function renderCompare(){
  const section=$("topic-compare");
  const rows=state.universe.filter(r=>state.compare.has(r.symbol));
  if (!rows.length){section.innerHTML='<div class="topic-compare-hint">勾选左侧公司复选框，可跨行业比较最多5家公司的现金收益率、分红增长与回撤。</div>';return;}
  const columns=[
    ["2025实施股息率", r=>percent(data(r)?.implemented_fy_yield_pct)],
    ["10年分红CAGR",r=>percent(stats(r).cagr_pct)],
    ["已观测减息次数",r=>stats(r).observed_adjacent_pairs?String(stats(r).observed_cuts):"—"],
    ["已覆盖财年",r=>String(stats(r).years_with_cash??"—")],
    ["最大回撤",r=>percent(data(r)?.risk?.max_drawdown_pct)],
    ["年化波动率",r=>percent(data(r)?.risk?.annual_volatility_pct)],
    ["100万元累计现金",r=>cny(data(r)?.simulation?.cumulative_cash_cny)],
    ["未再投总回报",r=>percent(data(r)?.simulation?.total_return_pct_no_reinvest)],
    ["股息再投总回报",r=>percent(data(r)?.simulation?.reinvested?.total_return_pct)],
    ["2025成本股息率",r=>percent(data(r)?.simulation?.calendar_2025_yield_on_cost_pct)],
    ["连续派息",r=>valid(stats(r).consecutive_years_to_2025)?stats(r).consecutive_years_to_2025+"年":"—"]
  ];
  section.innerHTML='<h2>跨公司比较 <small>'+rows.length+'/5</small></h2><div class="topic-table-scroll"><table class="topic-table"><thead><tr><th>指标</th>'+
  rows.map(r=>'<th>'+escapeHtml(r.name)+'<small>'+r.symbol+'</small></th>').join("")+
  '</tr></thead><tbody>'+columns.map(([label,fn])=>'<tr><td>'+label+'</td>'+
    rows.map(r=>'<td>'+escapeHtml(fn(r))+'</td>').join("")+'</tr>').join("")+
  '</tbody></table></div><p class="topic-muted">不同上市日期、财年数据覆盖和价格观察窗口不一致；不适合直接当作风险调整后的跨股排名。</p>';
}

function render(){
  renderFilters();
  const rows=filtered();
  if(!rows.some(row=>row.symbol===state.selected))state.selected=rows[0]?.symbol||null;
  renderStats(rows);renderNav(rows);renderDetail();renderCompare();
}

$("topic-search").addEventListener("input",e=>{state.search=e.target.value;state.selected=null;render();});
$("topic-sort").addEventListener("change",e=>{state.sort=e.target.value;render();});
$("topic-group").addEventListener("change",e=>{state.group=e.target.value;state.selected=null;render();});

Promise.all([
  fetch("./data/topics-universe.json",{cache:"no-store"}).then(r=>{if(!r.ok)throw new Error("证券池HTTP "+r.status);return r.json();}),
  fetch("./data/topics-dividends.json",{cache:"no-store"}).then(r=>r.ok?r.json():null).catch(()=>null)
]).then(([universe,payload])=>{
  state.universe=universe;state.payload=payload;state.stocks=payload?.stocks||{};
  if(!payload)state.error="研究数据未生成，请检查每日工作流";
  const symbol=new URLSearchParams(location.search).get("symbol");
  if(symbol && universe.some(row=>row.symbol===symbol))state.selected=symbol;
  render();
}).catch(err=>{$("topic-freshness").textContent="数据不可用："+err.message;});
