"use strict";

/* Read-only projection of immutable market-data. No GitHub tokens in browser. */
const ROOT = "https://github.com/BestNathan/nasset/blob/market-data/";
const state = {
  data: null, error: null, asset: "BTC", tab: "overview",
  optionSide: "all", optionDte: "all", optionOtm: "all",
  optionSort: "expiry", optionSearch: "", optionPage: 0,
};
const $ = id => document.getElementById(id);
const esc = text => String(text ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const valid = n => n !== null && n !== undefined && n !== "" && Number.isFinite(Number(n));
const num = (value, decimals=2) => valid(value) ? Number(value).toLocaleString("en-US", {
  minimumFractionDigits: decimals, maximumFractionDigits: decimals,
}) : "—";
const integer = value => valid(value) ? Number(value).toLocaleString("en-US", {maximumFractionDigits:0}) : "—";
const pct = value => valid(value) ? num(value, 1) + "%" : "—";
const kb = bytes => valid(bytes) ? num(Number(bytes) / 1024, 1) + " KB" : "—";
const localTime = iso => iso ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Singapore", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(iso)) : "—";
const hhmm = iso => iso ? new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(iso)) : "—";
const last = () => state.data?.latest || {};
const latestSlot = () => state.data?.latest_slot || "";
const dataRows = dataset => last()[dataset] || [];
const nativeLink = path => ROOT + path.split("/").map(encodeURIComponent).join("/");

function health() {
  const slot = latestSlot();
  if (!slot) return {label: "暂无归档", klass: "stale"};
  const elapsed = (Date.now() - new Date(slot).getTime()) / 60000;
  if (elapsed < 0) return {label: "时段时间异常", klass: "stale"};
  if (elapsed > 95) return {label: "归档已延迟 · 请检查调度", klass: "stale"};
  return {label: "归档正在更新", klass: ""};
}

function renderSummary() {
  const d = state.data;
  const cards = [
    ["最近已归档时段", d.latest_slot ? hhmm(d.latest_slot) + " UTC" : "—", "UTC 小时标记，不是快照采样秒数"],
    ["已验证小时回执", integer(d.slots.length), "最近 " + integer(d.slots.length) + " 个已存档时段（最多 24）"],
    ["期权合约快照", integer(dataRows("option_chain").length), "BTC / ETH · 当前最新批次"],
    ["最新 Parquet 体积", kb(d.slots.at(-1)?.parquet_bytes), "ZSTD · 不包含 Manifest / Receipt"],
  ];
  $("market-summary").innerHTML = cards.map(([label,value,desc]) =>
    '<div class="market-stat"><div class="market-stat-title">' + esc(label) +
    '</div><div class="market-stat-value">' + esc(value) +
    '</div><div class="market-stat-desc">' + esc(desc) + '</div></div>').join("");
  const status = health();
  $("market-health").className = "market-health" + (status.klass ? " " + status.klass : "");
  $("market-health").innerHTML = '<span class="market-health-dot"></span>' + esc(status.label);
  $("market-last-slot").textContent = d.latest_slot ? hhmm(d.latest_slot) + " UTC" : "—";
  $("market-last-local").textContent = d.latest_slot ? localTime(d.latest_slot) + " · UTC+8 归档时段" : "当前尚无可验证小时回执";
  const receipt = d.slots.at(-1)?.receipt_path;
  if (receipt) $("market-receipt-link").href = nativeLink(receipt);
}

function panelTitle(title, subtitle, badge="") {
  return '<div class="market-panel-top"><div><h3>' + esc(title) +
    '</h3><div class="market-panel-sub">' + esc(subtitle) + '</div></div>' +
    (badge ? '<span class="market-source-tag">' + esc(badge) + '</span>' : '') + '</div>';
}

function chart(points, {secondary=false, suffix="", decimals=2}={}) {
  const clean = points.filter(p => valid(p.value) && Number.isFinite(new Date(p.slot).getTime()))
    .map(p => ({ms: new Date(p.slot).getTime(), value: Number(p.value)}))
    .sort((a,b) => a.ms-b.ms);
  if (!clean.length) return '<div class="market-empty">该标的在已归档时段内没有可用观测值。</div>';
  const W=700,H=260,L=67,R=18,T=17,B=39,plotW=W-L-R,plotH=H-T-B;
  const values=clean.map(p=>p.value), min0=Math.min(...values),max0=Math.max(...values);
  const margin=Math.max((max0-min0)*.14, Math.abs(max0)*.006, .01);
  const min=min0-margin,max=max0+margin;
  const first=clean[0].ms,last=clean.at(-1).ms;
  const x=p=>L+((p.ms-first)/(last-first || 1))*plotW;
  const y=p=>T+(1-(p.value-min)/(max-min))*plotH;
  const steps=4;
  const grids=Array.from({length:steps+1},(_,i)=>{
    const yy=T+(plotH*i/steps), v=max-(max-min)*i/steps;
    return '<line class="market-chart-grid" x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'"></line>' +
      '<text class="market-chart-axis" x="'+(L-9)+'" y="'+(yy+4)+'" text-anchor="end">'+
      esc(num(v,decimals))+'</text>';
  }).join("");
  const seg=[];let current=[];
  for(const p of clean){
    if(current.length && p.ms-current.at(-1).ms>90*60000){seg.push(current);current=[];}
    current.push(p);
  }
  if(current.length)seg.push(current);
  const pathClass="market-chart-path"+(secondary?" secondary":"");
  const lines=seg.map(points=>{
    if(points.length===1){const p=points[0];return '<circle class="market-chart-dot" cx="'+x(p)+'" cy="'+y(p)+'" r="4"></circle>';}
    return '<polyline class="'+pathClass+'" points="'+points.map(p=>x(p).toFixed(1)+","+y(p).toFixed(1)).join(" ")+'"></polyline>';
  }).join("");
  const timestamps=[clean[0],clean[Math.floor((clean.length-1)/2)],clean.at(-1)];
  const axis=timestamps.map((p,i)=>'<text class="market-chart-axis" x="'+x(p)+'" y="'+(H-10)+'" text-anchor="'+(i===0?"start":i===2?"end":"middle")+'">'+esc(hhmm(new Date(p.ms).toISOString()))+'</text>').join("");
  return '<svg class="market-line-chart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+esc(clean.length+' 次归档记录，'+suffix)+'">'+grids+lines+axis+'</svg>' +
    '<div class="market-series-label"><span>首条：'+esc(num(clean[0].value,decimals))+esc(suffix)+'</span>' +
    '<strong>最新：'+esc(num(clean.at(-1).value,decimals))+esc(suffix)+'</strong></div>';
}

function indexSeries(asset) {
  const all=(state.data.history || []).flatMap(row=>row.indexes.map(x=>({...x,slot:row.slot})))
    .filter(x=>x.base_currency===asset && valid(x.index_price));
  const names=[...new Set(all.map(x=>x.index_name))].sort((a,b)=>{
    const priority=x=>x.endsWith("_usd")?0:x.endsWith("_usdc")?1:2;
    return priority(a)-priority(b)||a.localeCompare(b);
  });
  const name=names[0]||null;
  return {name,points:all.filter(x=>x.index_name===name).map(x=>({slot:x.slot,value:x.index_price}))};
}

function dvolSeries(asset) {
  return (state.data.history || []).flatMap(row=>row.dvol.filter(x=>x.base_currency===asset)
    .map(x=>({slot:row.slot,value:x.close,event_timestamp:x.event_timestamp})));
}

function renderOverview() {
  const asset=state.asset, idx=indexSeries(asset),dv=dvolSeries(asset),indexes=dataRows("index_price")
    .filter(x=>x.base_currency===asset), future=dataRows("futures_curve").filter(x=>x.base_currency===asset);
  const options=dataRows("option_chain").filter(x=>x.base_currency===asset);
  const panels='<div class="market-grid">' +
    '<article class="market-panel">'+panelTitle("指数价格 · " + asset,"按已归档时段的真实指数值绘制；不插值缺失小时。",idx.name||"无指数")+
    chart(idx.points,{decimals:2})+'<p class="market-note">每条线只连接相邻小时，空档表示没有归档记录。不同 index_name 不混算。</p></article>'+
    '<article class="market-panel">'+panelTitle("波动率指数 · DVOL","交易所发布的最后完成小时 K 线收盘值；非当小时即时隐含波动率。",asset==="PAXG"?"不支持":"DVOL")+
    chart(dv,{suffix:"%",decimals:1,secondary:true})+
    '<p class="market-note">DVOL 来源于 Deribit 原生小时 K 线，BTC/ETH 可用；PAXG 不提供此数据集。</p></article></div>';

  const bars=[["期权合约","期权链（仅 BTC / ETH）",options.length],["期货 / 永续","真实在列合约",future.length],
    ["指数价格","完整指数名称",indexes.length],["DVOL","已完成小时记录",dv.length?1:0]];
  const total=Math.max(...bars.map(x=>x[2]),1);
  const coverage='<article class="market-panel"><div class="market-mini-head"><h3>'+esc(asset)+' · 最新时段覆盖</h3><span class="market-source-tag">SOURCE COVERAGE</span></div>'+
    '<div class="market-bars">'+bars.map(([name,note,count])=>'<div class="market-bar-row"><div class="market-bar-name">'+esc(name)+'<small>'+esc(note)+'</small></div>'+
      '<div class="market-bar-track"><div class="market-bar-fill" style="width:'+((count/total)*100).toFixed(2)+'%"></div></div><div class="market-bar-value">'+integer(count)+'</div></div>').join("")+
    '</div><p class="market-note">柱长相对当前标的最多行数，仅表示数量，不表示数据质量或流动性。期权 Delta 尚未归档。</p></article>';
  const indexTable='<article class="market-panel">'+panelTitle("最新原始指数","原生计价货币和完整指数标识，不合并 USD / USDC。", "INDEX NAMES")+
    (indexes.length?'<div class="market-indices">'+indexes.map(x=>'<div class="market-index-row"><span>'+esc(x.index_name)+'</span><strong>'+esc(num(x.index_price,2))+'</strong><small>'+esc(x.quote_currency)+'</small></div>').join("")+'</div>':'<div class="market-empty">无该标的指数观测。</div>')+
    '<p class="market-note">指数价格和期权报价单位不同；请勿将原生期权 bid 直接视作美元权利金。</p></article>';
  return panels+'<div class="market-section-heading"><div><h3>最新快照的真实覆盖</h3><p>只统计最新 UTC 时段成功归档的原始记录。</p></div>'+
    '<span class="market-count-chip">'+esc(hhmm(latestSlot()))+' UTC</span></div><div class="market-grid">'+coverage+indexTable+'</div>';
}

function dte(row) {
  if (!valid(row.expiration_timestamp) || !latestSlot()) return null;
  return (Number(row.expiration_timestamp)-new Date(latestSlot()).getTime())/86400000;
}
const DTE_BUCKETS=[["0-7","≤ 7D",0,8],["8-30","8–30D",8,31],["31-60","31–60D",31,61],["61-90","61–90D",61,91],["91+","90D+",91,Infinity]];
const OTM_BUCKETS=[["0-2","0–2%",0,2],["2-5","2–5%",2,5],["5-10","5–10%",5,10],["10-20","10–20%",10,20],["20+","20%+",20,Infinity]];
function bucket(value, defs) {
  if (!valid(value)) return null;
  return defs.find(([k,n,min,max])=>Number(value)>=min && Number(value)<max)?.[0] || null;
}
function otm(row) {
  if (!valid(row.strike) || !valid(row.underlying_price) || Number(row.underlying_price)<=0) return null;
  const ratio=(Number(row.strike)-Number(row.underlying_price))/Number(row.underlying_price)*100;
  return row.option_type==="put" ? -ratio : row.option_type==="call"?ratio:null;
}
function optionSource() {
  return dataRows("option_chain").filter(row=>row.base_currency===state.asset);
}
function matchesBase(row) {
  return (state.optionSide==="all"||row.option_type===state.optionSide) &&
    (!state.optionSearch||row.instrument_name.toLowerCase().includes(state.optionSearch.toLowerCase()));
}
function filteredOptions() {
  const rows=optionSource().filter(row=>matchesBase(row) &&
    (state.optionDte==="all" || bucket(dte(row),DTE_BUCKETS)===state.optionDte) &&
    (state.optionOtm==="all" || bucket(otm(row),OTM_BUCKETS)===state.optionOtm));
  const key=state.optionSort;
  rows.sort((a,b)=>{
    const av=key==="iv" ? a.mark_iv : key==="oi"?a.open_interest:key==="strike"?a.strike:dte(a);
    const bv=key==="iv" ? b.mark_iv : key==="oi"?b.open_interest:key==="strike"?b.strike:dte(b);
    const fallback= key==="oi"||key==="iv" ? -Infinity : Infinity;
    return (key==="oi"||key==="iv" ? Number(bv??fallback)-Number(av??fallback) :
      Number(av??fallback)-Number(bv??fallback)) || a.instrument_name.localeCompare(b.instrument_name);
  });
  return rows;
}
function matrix() {
  const rows=optionSource().filter(row=>(state.optionSide==="all"||row.option_type===state.optionSide) &&
    valid(row.mark_iv));
  const parts=DTE_BUCKETS.map(([dKey,dName])=>'<tr><td>'+esc(dName)+'</td>'+OTM_BUCKETS.map(([oKey])=>{
    const values=rows.filter(r=>bucket(dte(r),DTE_BUCKETS)===dKey && bucket(otm(r),OTM_BUCKETS)===oKey);
    if(!values.length) return '<td><div class="market-matrix-missing">—</div></td>';
    const avg=values.reduce((sum,r)=>sum+Number(r.mark_iv),0)/values.length;
    const active=state.optionDte===dKey&&state.optionOtm===oKey;
    return '<td><button class="market-matrix-btn'+(active?' active':'')+'" type="button" data-matrix-dte="'+esc(dKey)+'" data-matrix-otm="'+esc(oKey)+'" aria-pressed="'+active+'" title="'+esc(dName+' · '+oKey+'% 虚值 · '+values.length+' 个合约')+'"><b>'+pct(avg)+'</b><small>'+integer(values.length)+' contracts</small></button></td>';
  }).join("")+'</tr>').join("");
  return '<div class="market-matrix-scroll"><table class="market-matrix"><thead><tr><th>剩余天数 / 虚值距离</th>'+
    OTM_BUCKETS.map(([key,label])=>'<th>'+esc(label)+'</th>').join("")+'</tr></thead><tbody>'+parts+'</tbody></table></div>';
}
function optionTable() {
  const rows=filteredOptions(), pageSize=35, pages=Math.max(1,Math.ceil(rows.length/pageSize));
  state.optionPage=Math.max(0,Math.min(state.optionPage,pages-1));
  const shown=rows.slice(state.optionPage*pageSize,(state.optionPage+1)*pageSize);
  const trows=shown.map(row=>{
    const expiry=valid(row.expiration_timestamp)?new Date(Number(row.expiration_timestamp)).toISOString().slice(0,10):"—";
    return '<tr><td><strong>'+esc(row.instrument_name)+'</strong><span class="secondary">'+esc(row.settlement_currency||"原生结算")+'</span></td>'+
      '<td>'+esc(row.option_type||"—")+'</td><td>'+esc(expiry)+'</td><td>'+esc(valid(dte(row))?num(dte(row),1)+"D":"—")+'</td>'+
      '<td>'+esc(num(row.strike,1))+'</td><td>'+esc(valid(otm(row))?num(otm(row),1)+"%":"—")+'</td>'+
      '<td>'+esc(num(row.bid_price,4))+'</td><td>'+esc(num(row.ask_price,4))+'</td>'+
      '<td class="cell-good">'+esc(pct(row.mark_iv))+'</td><td>'+esc(num(row.open_interest,2))+'</td></tr>';
  }).join("");
  return '<div class="market-section-heading"><div><h3>期权原始报价</h3><p>Bid / Ask / OI 保留原生合约单位；IV 为交易所标记隐含波动率。</p></div><span class="market-count-chip">'+integer(rows.length)+' contracts</span></div>'+
    '<div class="market-table-wrap"><table class="market-table"><thead><tr>'+
    '<th>合约 / 结算</th><th>方向</th><th>到期日</th><th>DTE</th><th>行权价</th><th>OTM</th><th>Bid (native)</th><th>Ask (native)</th><th>Mark IV</th><th>Open interest</th>'+
    '</tr></thead><tbody>'+ (trows||'<tr><td colspan="10">当前筛选条件下没有已归档合约。</td></tr>') +'</tbody></table></div>'+
    '<div class="market-pagination"><span>'+integer(rows.length?state.optionPage*pageSize+1:0)+'–'+integer(Math.min(rows.length,(state.optionPage+1)*pageSize))+' / '+integer(rows.length)+'</span>'+
    '<div><button class="market-btn" data-page="-1" '+(state.optionPage===0?"disabled":"")+'>上一页</button>'+
    '<button class="market-btn" data-page="1" '+(state.optionPage>=pages-1?"disabled":"")+'>下一页</button></div></div>';
}
function optionsView() {
  if(state.asset==="PAXG")return '<div class="market-empty"><strong>当前不采集 PAXG 期权。</strong><p>Deribit 当前未提供符合采集范围的 PAXG 期权合约，因此不会构造虚假期权数据。PAXG 的期货 / 永续和指数价格仍可在其他标签查看。</p><button class="market-btn" data-go="futures">查看 PAXG 期货 →</button></div>';
  const options=optionSource();
  if(!options.length)return '<div class="market-empty">当前最新归档中没有 '+esc(state.asset)+' 期权快照。</div>';
  const controls='<div class="market-filterbar"><label>方向<select class="market-select" data-filter="side">'+
    [["all","全部"],["call","Call"],["put","Put"]].map(([key,label])=>'<option value="'+key+'" '+(state.optionSide===key?"selected":"")+'>'+label+'</option>').join("")+'</select></label>'+
    '<label>DTE 分组<select class="market-select" data-filter="dte"><option value="all">全部期限</option>'+
    DTE_BUCKETS.map(([key,label])=>'<option value="'+key+'" '+(state.optionDte===key?"selected":"")+'>'+esc(label)+'</option>').join("")+'</select></label>'+
    '<label>OTM 距离<select class="market-select" data-filter="otm"><option value="all">全部</option>'+
    OTM_BUCKETS.map(([key,label])=>'<option value="'+key+'" '+(state.optionOtm===key?"selected":"")+'>'+esc(label)+'</option>').join("")+'</select></label>'+
    '<label>排序<select class="market-select" data-filter="sort">'+[["expiry","到期时间 ↑"],["strike","行权价 ↑"],["iv","标记 IV ↓"],["oi","持仓量 ↓"]].map(([key,label])=>
      '<option value="'+key+'" '+(state.optionSort===key?"selected":"")+'>'+esc(label)+'</option>').join("")+'</select></label>'+
    '<label>合约搜索<input class="market-input" data-filter="search" type="search" placeholder="如 BTC-..." value="'+esc(state.optionSearch)+'"></label>'+
    '<button class="market-btn" data-reset-options="true">重置筛选</button></div>';
  return controls+'<article class="market-panel">'+panelTitle(state.asset+" · OTM / DTE IV 矩阵","每格是该区间内有效 Mark IV 的算术平均，点击可筛选下方原始合约。", "MARK IV / NOT DELTA")+
    matrix()+'<p class="market-note">纵轴为距到期天数，横轴为按标的参考价计算的虚值行权价距离；不代表期权 Delta、收益率或可成交价格。只统计 OTM 合约。</p></article>'+
    '<div id="option-table-area">'+optionTable()+'</div>';
}
function futuresView() {
  const all=dataRows("futures_curve").filter(x=>x.base_currency===state.asset);
  if(!all.length)return '<div class="market-empty">当前最新归档中没有 '+esc(state.asset)+' 期货或永续合约。</div>';
  const sorted=all.slice().sort((a,b)=>(Number(a.expiration_timestamp)||0)-(Number(b.expiration_timestamp)||0));
  const perps=sorted.filter(x=>!valid(x.expiration_timestamp)||Number(x.expiration_timestamp)<=0);
  const dated=sorted.filter(x=>valid(x.expiration_timestamp)&&Number(x.expiration_timestamp)>0);
  const ref=indexSeries(state.asset).name || "无指数";
  const rows=sorted.map(row=>'<tr><td><strong>'+esc(row.instrument_name)+'</strong></td>'+
    '<td>'+esc(valid(row.expiration_timestamp)&&Number(row.expiration_timestamp)>0?
      new Date(Number(row.expiration_timestamp)).toISOString().slice(0,10):"永续 / Perpetual")+'</td>'+
    '<td>'+esc(num(row.mark_price,2))+'</td><td>'+esc(num(row.bid_price,2))+'</td>'+
    '<td>'+esc(num(row.ask_price,2))+'</td><td>'+esc(num(row.open_interest,2))+'</td>'+
    '<td>'+esc(num(row.volume_usd,0))+'</td><td>'+esc(row.settlement_currency||"—")+'</td></tr>').join("");
  const spots=dataRows("index_price").filter(x=>x.base_currency===state.asset);
  return '<div class="market-grid"><article class="market-panel">'+panelTitle("期货 / 永续结构","仅展示交易所原生合约标记价格，不跨结算单位合成基差。","FUTURES CURVE")+
    '<div class="market-indices"><div class="market-index-row"><span>在列合约</span><strong>'+integer(all.length)+'</strong><small>contracts</small></div>'+
    '<div class="market-index-row"><span>有到期日的合约</span><strong>'+integer(dated.length)+'</strong><small>listed</small></div>'+
    '<div class="market-index-row"><span>永续合约</span><strong>'+integer(perps.length)+'</strong><small>perpetual</small></div></div></article>'+
    '<article class="market-panel">'+panelTitle("标的参考指数","指数与期货报价分别保留原生单位。","INDEX SOURCE")+
    (spots.length?'<div class="market-indices">'+spots.map(x=>'<div class="market-index-row"><span>'+esc(x.index_name)+'</span><strong>'+esc(num(x.index_price,2))+'</strong><small>'+esc(x.quote_currency)+'</small></div>').join("")+'</div>':'<div class="market-empty">暂无参考指数。</div>')+
    '<p class="market-note">期货到期与永续产品展示在同一明细中，结算货币见每行。合约不一定与 '+esc(ref)+' 同单位。</p></article></div>'+
    '<div class="market-section-heading"><div><h3>'+esc(state.asset)+' · 合约期限表</h3><p>期货 Mark、Bid、Ask、OI、交易量皆来自最新 UTC 时段 Parquet。</p></div><span class="market-count-chip">'+integer(sorted.length)+' listed</span></div>'+
    '<div class="market-table-wrap"><table class="market-table"><thead><tr><th>合约</th><th>到期</th><th>Mark</th><th>Bid</th><th>Ask</th><th>Open interest</th><th>Volume USD</th><th>结算</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}
function archiveView() {
  const d=state.data, slots=(d.slots||[]).slice().reverse();
  return '<div class="market-archive-head"><article class="market-panel">'+panelTitle("归档验证链","每个 UTC 批次都保留 Receipt、Manifest 与 Parquet 数据文件。","APPEND ONLY")+
    '<p class="market-note">该页面的数据来自已完成的 Git 归档，不依赖实时行情接口。生成器核对 Manifest 中的每个文件及其 Git blob SHA，读取 Parquet 时验证数据集和 UTC Slot 一致性。</p></article>'+
    '<article class="market-panel">'+panelTitle("最新已确认时段", "列出最近最多 24 个已经持久化的时段，不把缺失小时补成 0。","UTC SLOT")+
    '<div class="market-stat-value">'+esc(hhmm(latestSlot()))+' UTC</div><p class="market-note">'+esc(localTime(latestSlot()))+' UTC+8 · 共 '+integer(slots.length)+' 个回执被纳入当前视图。</p></article></div>'+
    '<div class="market-section-heading"><div><h3>逐小时不可变记录</h3><p>Manifest 列出具体文件及 Git SHA；Receipt 记录该 UTC 时段的必需数据集。</p></div></div>'+
    '<div class="market-table-wrap"><table class="market-table"><thead><tr><th>UTC 时段</th><th>本地 (UTC+8)</th><th>文件数</th><th>Parquet 体积</th><th>校验</th><th>原始记录</th></tr></thead><tbody>'+
    slots.map(s=>'<tr><td><strong>'+esc(s.slot.slice(0,16))+'Z</strong></td><td>'+esc(localTime(s.slot))+'</td>'+
      '<td>'+integer(s.artifact_count)+'</td><td>'+esc(kb(s.parquet_bytes))+'</td>'+
      '<td><span class="market-archive-badge">Manifest verified</span></td><td>'+
      '<a class="market-archive-link" href="'+esc(nativeLink(s.manifest_path))+'" target="_blank" rel="noopener noreferrer">Manifest ↗</a> &nbsp; '+
      '<a class="market-archive-link" href="'+esc(nativeLink(s.receipt_path))+'" target="_blank" rel="noopener noreferrer">Receipt ↗</a></td></tr>').join("")+
    '</tbody></table></div><p class="market-note">页面最多列出最近 24 个已有回执的时段；此前归档仍保留在 market-data 分支。缺失时段不代表由该视图自动补采成功。</p>';
}
function renderView() {
  if(!state.data)return;
  $("market-assets").querySelectorAll("[data-asset]").forEach(btn=>{
    const active=btn.dataset.asset===state.asset;btn.classList.toggle("active",active);
    btn.setAttribute("aria-pressed",String(active));
  });
  $("market-tabs").querySelectorAll("[data-tab]").forEach(btn=>{
    const active=btn.dataset.tab===state.tab;btn.classList.toggle("active",active);
    btn.setAttribute("aria-selected",String(active));
  });
  $("market-body").innerHTML = state.tab==="overview" ? renderOverview() :
    state.tab==="options" ? optionsView() :
    state.tab==="futures" ? futuresView() : archiveView();
}
async function boot() {
  try {
    const resp=await fetch("./data/deribit-dashboard.json?version="+Date.now(),{cache:"no-store"});
    if(!resp.ok)throw new Error("HTTP "+resp.status+"：页面数据尚未生成或发布。");
    const d=await resp.json();
    if(d.version!==1||!Array.isArray(d.slots)||!d.latest||!Array.isArray(d.history))throw new Error("数据格式不匹配");
    state.data=d;renderSummary();renderView();
  }catch(e){
    state.error=e;
    $("market-health").className="market-health stale";
    $("market-health").innerHTML='<span class="market-health-dot"></span> 数据暂不可用';
    $("market-body").innerHTML='<div class="market-error"><strong>Deribit 归档视图暂时无法读取。</strong><p>'+
      esc(e.message||"请求失败")+'</p><p>可在 GitHub market-data 分支检查原始 Parquet、Manifest 和 Receipt。页面不会用模拟数据代替真实采集。</p>'+
      '<a class="market-archive-link" href="https://github.com/BestNathan/nasset/tree/market-data/deribit" target="_blank" rel="noopener noreferrer">检查原始归档 ↗</a></div>';
    $("market-summary").innerHTML="";
  }
}
function bindEvents() {
  $("market-assets").addEventListener("click",e=>{
    const b=e.target.closest("[data-asset]");if(!b)return;
    state.asset=b.dataset.asset;state.optionPage=0;state.optionDte="all";state.optionOtm="all";renderView();
  });
  $("market-tabs").addEventListener("click",e=>{
    const b=e.target.closest("[data-tab]");if(!b)return;
    state.tab=b.dataset.tab;renderView();
  });
  $("market-body").addEventListener("click",e=>{
    const page=e.target.closest("[data-page]");
    if(page&&!page.disabled){state.optionPage+=Number(page.dataset.page);$("option-table-area").innerHTML=optionTable();return;}
    const grid=e.target.closest("[data-matrix-dte]");
    if(grid){
      const same=state.optionDte===grid.dataset.matrixDte&&state.optionOtm===grid.dataset.matrixOtm;
      state.optionDte=same?"all":grid.dataset.matrixDte;state.optionOtm=same?"all":grid.dataset.matrixOtm;
      state.optionPage=0;renderView();return;
    }
    const reset=e.target.closest("[data-reset-options]");
    if(reset){Object.assign(state,{optionSide:"all",optionDte:"all",optionOtm:"all",optionSort:"expiry",optionSearch:"",optionPage:0});renderView();return;}
    const go=e.target.closest("[data-go]");
    if(go){state.tab=go.dataset.go;renderView();}
  });
  $("market-body").addEventListener("change",e=>{
    const target=e.target,filter=target.dataset.filter;
    if(!filter||filter==="search")return;
    if(filter==="side")state.optionSide=target.value;
    if(filter==="dte")state.optionDte=target.value;
    if(filter==="otm")state.optionOtm=target.value;
    if(filter==="sort")state.optionSort=target.value;
    state.optionPage=0;renderView();
  });
  $("market-body").addEventListener("input",e=>{
    if(e.target.dataset.filter!=="search")return;
    state.optionSearch=e.target.value;state.optionPage=0;
    const region=$("option-table-area");if(region)region.innerHTML=optionTable();
  });
}
bindEvents();
boot();
