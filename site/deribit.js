"use strict";

/* Mathematical inputs are the offline-verified, versioned Pages JSON projection. */
const $ = id => document.getElementById(id);
const state = {
  data: null, asset: "BTC", product: "options", family: "inverse", tab: "surface", optionTab: "surface",
  selectedExpiry: null, optionSide: "all", optionTenor: "all",
  optionSearch: "", optionSort: "oi", optionPage: 0,
};
const esc = value => String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;")
  .replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
const finite = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
const money = (value, digits=1) => finite(value) ? Number(value).toLocaleString("en-US",{
  minimumFractionDigits:digits,maximumFractionDigits:digits,
}) : "—";
const signed = (value, digits=1) => finite(value) ? (Number(value)>0?"+":"")+money(value,digits) : "—";
const int = value => finite(value)?Number(value).toLocaleString("en-US",{maximumFractionDigits:0}):"—";
const compact = value => {
  if(!finite(value))return "—";
  const n=Number(value),abs=Math.abs(n);
  if(abs>=1e9)return (n/1e9).toFixed(2)+"B";
  if(abs>=1e6)return (n/1e6).toFixed(2)+"M";
  if(abs>=1e3)return (n/1e3).toFixed(1)+"K";
  return money(n,1);
};
const iv = value => finite(value)?money(value,1)+"%":"—";
const vp = value => finite(value)?signed(value,2)+" vol":"—";
const dateUtc = value => {
  if(!value || Number.isNaN(new Date(value).getTime()))return "—";
  return new Intl.DateTimeFormat("en-GB",{
    day:"2-digit",month:"short",timeZone:"UTC",year:"2-digit",
  }).format(new Date(value));
};
const ts = value => value?new Date(value).toISOString().slice(0,16).replace("T"," ")+" UTC":"—";
const assetModel = () => state.data?.analytics?.assets?.[state.asset] || null;
const market = () => assetModel()?.markets?.[state.family] || null;
const latestChain = () => (state.data?.latest?.option_chain || []).filter(r=>
  r.base_currency===state.asset &&
  (state.family==="inverse" ? r.settlement_currency===state.asset :
    ["USDC","USDT","USD"].includes(r.settlement_currency)));
const methodPill = label => '<span class="quant-pill">'+esc(label)+'</span>';
const panelTitle = (title,detail="") => '<div class="quant-panel-title"><h3>'+esc(title)+'</h3>'+
  (detail?'<span>'+esc(detail)+'</span>':"")+'</div>';
const empty = (text) => '<div class="quant-placeholder">'+esc(text)+'</div>';
const chartTag = (content,label) => '<svg viewBox="0 0 740 250" class="quant-chart" role="img" aria-label="'+esc(label)+'">'+content+'</svg>';

function lineChart(series, opts={}) {
  const W=740,H=250,L=65,R=20,T=18,B=37;
  const points=series.flatMap(s=>(s.points||[]).filter(p=>finite(p.x)&&finite(p.y)));
  if(!points.length)return empty("当前没有可计算的期限节点。");
  let loX=Math.min(...points.map(p=>Number(p.x))), hiX=Math.max(...points.map(p=>Number(p.x)));
  let loY=Math.min(...points.map(p=>Number(p.y))), hiY=Math.max(...points.map(p=>Number(p.y)));
  if(hiX===loX){loX-=1;hiX+=1;}
  const span=Math.max(hiY-loY,.8);loY-=span*.18;hiY+=span*.18;
  if(opts.includeZero){loY=Math.min(0,loY);hiY=Math.max(0,hiY);}
  const xx=x=>L+(Number(x)-loX)/(hiX-loX)*(W-L-R);
  const yy=y=>T+(hiY-Number(y))/(hiY-loY)*(H-T-B);
  let grid="";
  for(let i=0;i<=4;i++){
    const v=hiY-(hiY-loY)*i/4,y=yy(v);
    grid+='<line class="quant-gridline" x1="'+L+'" y1="'+y+'" x2="'+(W-R)+'" y2="'+y+'"/>'+
      '<text class="quant-axis" text-anchor="end" x="'+(L-9)+'" y="'+(y+4)+'">'+esc(money(v,opts.precision??1))+'</text>';
  }
  for(let i=0;i<=4;i++){
    const v=loX+(hiX-loX)*i/4;
    grid+='<text class="quant-axis" text-anchor="middle" x="'+xx(v)+'" y="'+(H-9)+'">'+
      esc(int(v))+esc(opts.xUnit||"D")+'</text>';
  }
  const paths=series.map((s,index)=>{
    const sorted=(s.points||[]).filter(p=>finite(p.x)&&finite(p.y)).sort((a,b)=>a.x-b.x);
    if(!sorted.length)return "";
    const klass=index===1?"alt":index===2?"third":"";
    const data=sorted.map((p,i)=>(i?"L":"M")+xx(p.x).toFixed(2)+","+yy(p.y).toFixed(2)).join(" ");
    return '<path class="quant-chart-line '+klass+'" d="'+data+'"></path>'+
      sorted.map(p=>'<circle cx="'+xx(p.x).toFixed(2)+'" cy="'+yy(p.y).toFixed(2)+'" r="3.5" fill="'+
        (index===1?"#8ab6f3":index===2?"#eda7a2":"#d4ff5f")+'"><title>'+esc(s.name+': '+money(p.y,2)+' @ '+money(p.x,1)+'D')+'</title></circle>').join("");
  }).join("");
  const legend='<div class="quant-chart-legend">'+series.map((s,i)=>'<span><b class="'+(i===1?"blue":i===2?"rose":"")+
    '"></b>'+esc(s.name)+'</span>').join("")+'</div>';
  return chartTag(grid+paths,opts.label||"期限结构图")+legend;
}

function surfacePoint(expiry,column) {
  if(!expiry || !finite(expiry.nodes?.[column]))return null;
  return Number(expiry.nodes[column]);
}

function kpi(label,value,tag,note,klass="") {
  const compact='<div class="label"><span>'+esc(label)+
    '</span><span class="tag">'+esc(tag)+'</span></div><div class="number '+klass+'">'+
    esc(value)+'</div><div class="note">'+esc(note)+'</div>';
  return NassetMetricGuide.wrap(label,compact,"quant-kpi");
}
function metricClusters(groups) {
  return groups.map(([title,explanation,rows])=>
    '<section class="quant-metric-cluster"><div class="quant-metric-cluster-head"><strong>'+
    esc(title)+'</strong><span>'+esc(explanation)+'</span></div>'+
    '<div class="quant-metric-cluster-cards">'+rows.map(row=>kpi(...row)).join("")+'</div></section>'
  ).join("");
}

function renderKpis(){
  const a=assetModel(), m=market(), risk=m?.risk, metrics=risk?.metrics || {};
  const t30=m?.tenors?.find(n=>n.target_days===30);
  const notional=finite(metrics.oi_units)&&finite(a?.reference_price) ?
    Number(metrics.oi_units)*Number(a.reference_price):null;
  const cards=state.asset==="PAXG" ? [
    ["参考指数",finite(a?.reference_price)?"$"+money(a.reference_price,2):"—","SPOT","独立指数参考"],
    ["永续 / 期货",int(a?.futures?.length||0),"FUT","当前上市合约"],
    ["期权市场","未上市","OPTIONS","不生成不存在的 Greeks"],
    ["DVOL","—","IV INDEX","该标的无 DVOL"],
    ["基差节点",int(a?.futures?.filter(f=>finite(f.annualized_basis_pct)).length||0),"BASIS","有可比指数的期限报价"],
    ["风险面","—","OI GREEKS","需要期权持仓与波动率"],
  ] : [
    ["30D ATM IV",iv(t30?.atm),"BLACK-76","期限间插值，缺值不外推"],
    ["25Δ Risk Reversal",vp(t30?.rr25),"25D / 30D","Call IV − Put IV"],
    ["25Δ Butterfly",vp(t30?.bf25),"25D / 30D","翼部 IV 溢价"],
    ["DVOL",iv(a?.dvol_pct),"DERIBIT","最后完成小时的 DVOL"],
    ["Options OI",notional===null?"—":"$"+compact(notional),"USD NOTIONAL","公示 OI 估计名义敞口"],
    ["Gross Gamma / 1%",finite(metrics.gamma_1pct_usd)?"$"+compact(metrics.gamma_1pct_usd):"—","MODEL","OI 加权美元 Delta 敏感度"],
  ];
  $("quant-kpis").innerHTML=state.asset==="PAXG"
    ? metricClusters([["上市覆盖","当前有效上市市场",cards.slice(0,3)],["期货指标","基差与衍生敏感度",cards.slice(3)]])
    : metricClusters([
      ["波动率水平","价格隐含的波动规模", [cards[0],cards[3]]],
      ["曲面与偏斜","看涨 / 看跌翼的相对定价",[cards[1],cards[2]]],
      ["持仓与凸性","公开 OI 的模型风险尺度",[cards[4],cards[5]]],
    ]);
}

const OPTION_TABS=["surface","risk","options"];
function optionFamilies(){
  const markets=assetModel()?.markets || {};
  return ["inverse","linear"].filter(f=>Boolean(markets[f]));
}
function futuresFamilies(){
  const a=assetModel();
  return ["inverse","linear"].filter(f=>
    (a?.futures||[]).some(row=>row.family===f) ||
    (a?.futures_surface?.[f]||[]).length>0);
}
function availableFamilies(){
  return state.product==="options"?optionFamilies():futuresFamilies();
}
function chooseProduct(){
  // Never render non-existent PAXG option IV or model Greeks.
  if(state.product==="options"&&!optionFamilies().length){
    state.product="futures";state.tab="futures";
  }
  if(state.product==="options"&&!OPTION_TABS.includes(state.tab)){
    state.tab=OPTION_TABS.includes(state.optionTab)?state.optionTab:"surface";
  }
  if(state.product==="futures")state.tab="futures";
  const available=availableFamilies();
  if(!available.includes(state.family)){
    state.family=available.includes("inverse")?"inverse":available[0]||"inverse";
  }
}
function renderFuturesKpis(){
  const a=assetModel();
  const rows=(a?.futures||[]).filter(f=>f.family===state.family);
  const quoted=rows.filter(r=>finite(r.days)&&Number(r.days)>=1&&finite(r.annualized_basis_pct))
    .sort((x,y)=>Number(x.days)-Number(y.days));
  const observations=a?.futures_surface?.[state.family]||[];
  const latest=observations.at(-1)?.nodes?.["90"];
  const cards=[
    ["参考指数",finite(a?.reference_price)?"$"+money(a.reference_price,2):"—","SPOT","标的指数参考，非期货成交价"],
    ["上市期货 / 永续",int(rows.length),"CONTRACTS","当前结算结构的合约"],
    ["可比到期合约",int(quoted.length),"MATURITIES","剩余至少1天且具备可比基差"],
    ["近月年化基差",quoted.length?signed(quoted[0].annualized_basis_pct,2)+"%":"—","FRONT","最近有效到期合约"],
    ["90D 年化基差",finite(latest)?signed(latest,2)+"%":"—","90D","最后可用观测的90天节点"],
    ["期限观测时段",int(observations.length),"SLOTS","已验证的真实采集时段"],
  ];
  $("quant-kpis").innerHTML=metricClusters([
    ["价格与合约","真实参考指数和上市覆盖",cards.slice(0,3)],
    ["期限与基差","不同期限的年化升贴水",cards.slice(3)],
  ]);
}
function syncControls(){
  $("quant-asof").innerHTML='<span>SNAPSHOT / UTC</span><strong>'+esc(ts(state.data?.latest_slot))+'</strong>';
  document.querySelectorAll("[data-asset]").forEach(b=>{
    const active=b.dataset.asset===state.asset;b.classList.toggle("active",active);b.setAttribute("aria-pressed",String(active));
  });
  const opts=optionFamilies(),families=availableFamilies();
  document.querySelectorAll("[data-product]").forEach(b=>{
    const active=b.dataset.product===state.product;
    b.classList.toggle("active",active);b.setAttribute("aria-pressed",String(active));
    b.disabled=b.dataset.product==="options"&&!opts.length;
  });
  $("quant-product-note").textContent=!opts.length
    ? state.asset+" 当前没有可验证的 Deribit 期权市场，已切换到期货研究。"
    : state.product==="options"
      ? "期权研究 · 隐含波动率、合约与模型风险（不代表做市商净持仓）。"
      : "期货研究 · 永续与交割合约、期限结构及可比年化基差。";
  $("quant-research-title").innerHTML=state.product==="options"
    ?"Options <span>&amp; Volatility.</span>":"Futures <span>&amp; Basis.</span>";
  $("quant-research-description").textContent=state.product==="options"
    ?"隐含波动率、偏斜、期权风险敞口及上市合约。"
    :"期货期限结构、年化基差与永续及交割合约明细。";
  $("quant-family-switch").hidden=families.length<=1;
  document.querySelectorAll("[data-family]").forEach(b=>{
    const active=b.dataset.family===state.family;b.classList.toggle("active",active);b.setAttribute("aria-pressed",String(active));
    b.disabled=!families.includes(b.dataset.family);
  });
  $("quant-command-hint").innerHTML=state.product==="options"
    ?"MARK IV <span>·</span> BLACK-76 DERIVED GREEKS"
    :"FUTURES <span>·</span> ANNUALIZED BASIS";
  $("quant-tabs").hidden=state.product!=="options";
  document.querySelectorAll("[data-tab]").forEach(b=>{
    const active=b.dataset.tab===state.tab;b.classList.toggle("active",active);b.setAttribute("aria-selected",String(active));
  });
  if(state.product==="options")renderKpis();else renderFuturesKpis();
}

function sectionHead(kicker,title,subtitle,tag=""){
  return '<div class="quant-section-head"><div><div class="kicker">'+esc(kicker)+'</div><h2>'+
    esc(title)+'</h2><p class="quant-subtitle">'+esc(subtitle)+'</p></div>'+
    (tag?methodPill(tag):"")+'</div>';
}

const COLS=[["10p","10Δ Put"],["25p","25Δ Put"],["atm","ATM"],["25c","25Δ Call"],["10c","10Δ Call"]];
function surfaceHeatmap(){
  const m=market();
  if(!m?.expiries?.length)return empty("该产品线没有足够的 IV 标记数据构建波动率平面。");
  const expiries=m.expiries.filter(e=>e.days>0);
  const all=expiries.flatMap(e=>COLS.map(([k])=>surfacePoint(e,k))).filter(finite);
  const min=all.length?Math.min(...all):0,max=all.length?Math.max(...all):100;
  const map=(value)=>{
    const share=max>min?(value-min)/(max-min):.5;
    return (18+share*45).toFixed(0)+"%";
  };
  const html=expiries.map(e=>{
    const selected=state.selectedExpiry===e.expiry;
    return '<tr><td>'+esc(dateUtc(e.expiry))+'<small>'+esc(money(e.days,0))+' DTE</small></td>'+
      COLS.map(([k])=>{
        const value=surfacePoint(e,k);
        if(value===null)return '<td><span class="quant-heatcell none">—</span></td>';
        const active=selected?" selected":"";
        return '<td><button class="quant-heatcell'+active+'" type="button" data-surface-expiry="'+
          esc(e.expiry)+'" data-surface-node="'+esc(k)+'" style="--intensity:'+map(value)+'" title="'+
          esc(dateUtc(e.expiry)+" "+k+" "+money(value,2)+"% Mark IV")+'"><strong>'+iv(value)+
          '</strong><small>'+esc(k==="atm"?"近远期":"Black-76 Δ")+'</small></button></td>';
      }).join("")+'</tr>';
  }).join("");
  return '<div class="quant-heatmap-outer"><table class="quant-heatmap">'+
    '<thead><tr><th>到期 / Tenor</th>'+COLS.map(([k,label])=>'<th>'+esc(label)+'</th>').join("")+
    '</tr></thead><tbody>'+html+'</tbody></table></div>'+
    '<p class="quant-surface-note">每个格子使用已挂牌 Mark IV；10/25Δ 节点仅在相邻模型 Delta 有真实双侧报价时插值，缺失值保留为空。点击到期月份查看期限摘要。</p>';
}
function surfaceDetail(){
  const m=market();
  const selected=m?.expiries?.find(e=>e.expiry===state.selectedExpiry) ||
    m?.expiries?.find(e=>e.days>=7&&e.days<=90) || m?.expiries?.[0];
  if(!selected)return empty("没有可检查的期权到期日。");
  state.selectedExpiry=selected.expiry;
  const stats=[["ATM IV",iv(selected.atm)],["25Δ RR",vp(selected.rr25)],
    ["25Δ BF",vp(selected.bf25)],["Forward",finite(selected.forward)?"$"+money(selected.forward,0):"—"]];
  const display=stats.map(([key,val])=>'<div class="quant-expiry-stat"><span class="label">'+esc(key)+
    '</span><strong>'+esc(val)+'</strong></div>').join("");
  return '<article class="quant-panel">'+panelTitle("选中期限 · "+dateUtc(selected.expiry),money(selected.days,1)+" DTE")+
    '<div class="quant-expiry-details">'+display+'</div><p class="quant-panel-desc" style="margin-top:14px">'+
    esc(int(selected.contracts))+' 个挂牌合约参与 IV 节点构建。25Δ 风险逆转与蝶式反映的是翼部价格结构，不是方向预测。</p></article>';
}

function surfaceView(){
  const m=market();if(!m)return empty("当前市场没有期权 IV 数据。请选择 BTC / ETH 的有效合约市场。");
  const tenors=m.tenors||[];
  const atm=tenors.map(n=>({x:n.target_days,y:n.atm}));
  const rr=tenors.map(n=>({x:n.target_days,y:n.rr25}));
  const bf=tenors.map(n=>({x:n.target_days,y:n.bf25}));
  const a=assetModel();
  return sectionHead("01 / IMPLIED VOLATILITY","Volatility Surface",
    "按到期日 × 模型 Delta 展示挂牌期权的隐含波动率，实时处理数据来自最近归档的完整期权链。",
    state.family==="inverse"?"COIN SETTLED":"USDC LINEAR")+
    '<article class="quant-panel">'+panelTitle("IV surface · Expiry × Delta","MARK IV / %")+surfaceHeatmap()+'</article>'+
    '<div class="quant-grid equal quant-section-space">'+
      '<article class="quant-panel">'+panelTitle("ATM 期限结构","7 / 14 / 30 / 60 / 90 / 180D")+
        lineChart([{name:"ATM Mark IV",points:atm}],{label:"ATM 隐含波动率期限结构"})+'</article>'+
      '<article class="quant-panel">'+panelTitle("偏斜与尾部溢价","RISK REVERSAL / BUTTERFLY")+
        lineChart([{name:"25Δ RR",points:rr},{name:"25Δ BF",points:bf}],
                  {label:"25D Risk Reversal 与 Butterfly",includeZero:true,precision:1})+'</article></div>'+
    '<div class="quant-grid equal"><div>'+surfaceDetail()+'</div>'+
      '<article class="quant-panel">'+panelTitle("IV vs Realized","ANNUALIZED / %")+
        '<div class="quant-risk-stats">'+
        riskNumber("DVOL","官方30D隐波指数",iv(a?.dvol_pct))+
        riskNumber("Hourly RV","相邻 1H 收益率年化",iv(a?.rv_hourly_annualized_pct))+
        riskNumber("30D ATM","远期模型点",iv(m.tenors?.find(n=>n.target_days===30)?.atm))+
        riskNumber("IV − RV","仅比较有效观测",vp(
          finite(a?.rv_hourly_annualized_pct) && finite(m.tenors?.find(n=>n.target_days===30)?.atm) ?
            m.tenors.find(n=>n.target_days===30).atm-a.rv_hourly_annualized_pct:null))+
        '</div><p class="quant-surface-note">RV 至少需要 12 个真实连续小时收益率；采集历史较短时保持空值。</p></article></div>';
}

function riskNumber(label,context,value){
  const compact='<span>'+esc(label)+'</span><strong>'+esc(value)+
    '</strong><small>'+esc(context)+'</small>';
  return NassetMetricGuide.wrap(label,compact,"quant-risk-stat");
}
function gammaStrikes(risk){
  const rows=(risk?.strikes||[]).slice(0,18);
  if(!rows.length)return empty("未发现可用持仓量，无法计算行权价凸性集中度。");
  const largest=Math.max(...rows.map(r=>Number(r.gamma_1pct_usd)||0),1);
  return '<div class="quant-gex-bands">'+rows.map((r,index)=>{
    const value=r.gamma_1pct_usd || 0,width=Math.max(1,value/largest*100);
    return '<div class="quant-gex-row'+(index===0?" active":"")+'"><span class="quant-gex-strike">$'+
      int(r.strike)+'</span><span class="quant-gex-meter" title="'+esc("Gamma "+money(value,2)+" USD / 1%")+
      '"><span class="quant-gex-fill" style="width:'+width.toFixed(2)+'%"></span></span>'+
      '<span class="quant-gex-value">$'+compact(value)+'</span></div>';
  }).join("")+'</div>';
}
function expiryConcentration(risk){
  const rows=(risk?.expiries||[]).slice().sort((a,b)=>(b.oi_units||0)-(a.oi_units||0)).slice(0,12);
  const biggest=Math.max(...rows.map(r=>Number(r.oi_units)||0),1);
  if(!rows.length)return empty("当前没有可聚合的到期持仓量。");
  return '<div class="quant-expiry-bars">'+rows.map(r=>{
    const call=Number(r.call_oi)||0,put=Number(r.put_oi)||0;
    return '<div class="quant-expiry-row"><span>'+esc(dateUtc(r.expiry))+'</span>'+
      '<div class="quant-stack" title="'+esc("Call "+money(call,2)+" · Put "+money(put,2))+'">'+
      '<span class="quant-stack-call" style="width:'+(100*call/biggest).toFixed(2)+'%"></span>'+
      '<span class="quant-stack-put" style="width:'+(100*put/biggest).toFixed(2)+'%"></span></div>'+
      '<strong>'+compact(r.oi_units)+' '+esc(state.asset)+'</strong></div>';
  }).join("")+'</div>'+
    '<div class="quant-chart-legend"><span><b></b>Call OI</span><span><b class="rose"></b>Put OI</span></div>';
}
function shockTable(risk){
  const rows=risk?.spot_shock||[];
  const biggest=Math.max(...rows.map(r=>Number(r.gross_delta_rehedge_usd)||0),1);
  return '<div class="quant-scenario">'+rows.map(r=>'<div class="quant-scenario-row'+
    (r.move_pct<0?" negative":"")+'"><span>'+signed(r.move_pct,0)+'%</span>'+
    '<div class="quant-scenario-track"><span class="quant-scenario-fill" style="width:'+
    (100*(r.gross_delta_rehedge_usd||0)/biggest).toFixed(2)+'%"></span></div>'+
    '<strong>$'+compact(r.gross_delta_rehedge_usd)+'</strong></div>').join("")+'</div>';
}
function riskView(){
  const m=market();if(!m)return empty("PAXG 当前没有 Deribit 期权链，因此无法建立期权凸性敞口。可切换至期货研究。");
  const risk=m.risk, metrics=risk?.metrics||{};
  const top=finite(risk?.gamma_max_strike)?"$"+int(risk.gamma_max_strike):"—";
  const overview='<div class="quant-grid equal">'+
    '<article class="quant-panel">'+panelTitle("OI-weighted gross Greeks","BLACK-76 / LONG-ALL ASSUMPTION")+
      '<div class="quant-risk-stats">'+
        riskNumber("GEX / 1%","标的 +1% 的美元 Delta 变化","$"+compact(metrics.gamma_1pct_usd))+
        riskNumber("Vanna / 1 vol pt","IV 上升1个百分点的美元 Delta 绝对变化","$"+compact(metrics.vanna_1volpt_delta_usd))+
        riskNumber("Charm / 1 day","一天时间流逝的美元 Delta 绝对变化","$"+compact(metrics.charm_1day_delta_usd))+
        riskNumber("Volga / (1vol pt)²","波动率上升1点的二阶期权 P&L 项","$"+compact(metrics.volga_1volpt2_usd))+
        riskNumber("Vega / 1 vol pt","IV 变动1点的美元期权价格敏感度","$"+compact(metrics.vega_1volpt_usd))+
        riskNumber("Put / Call OI","同一结算市场内的持仓量比值",money(risk.put_call_oi_ratio,2))+
      '</div></article>'+
    '<article class="quant-panel">'+panelTitle("Convexity concentration","GAMMA / EXPIRY")+
      '<div class="quant-risk-stats">'+
        riskNumber("Gamma Wall","最高总 Gamma 加权行权价",top)+
        riskNumber("≤ 7D Gamma","短期限占全部 Gross Gamma",iv(risk.gamma_7d_share_pct))+
      '</div><p class="quant-panel-desc" style="margin-top:17px">Gamma Wall 是全市场未平仓量的凸性集中位，不是价格支撑、阻力或所谓 Gamma Flip。</p>'+
      '<div class="quant-panel-desc">已建模合约：'+int(risk.oi_contracts_with_data)+' / '+int(m.contracts)+
      ' · 敞口仅使用该结算市场内的 OI</div></article></div>';
  const warning='<div class="quant-muted-warning"><strong>POSITIONING BOUNDARY</strong>　未平仓量无法分辨做市商是多头还是空头。下面数值均为所有 OI 假设多头情况下的绝对压力测试；它们不能推导真实对冲买卖方向。</div>';
  return sectionHead("02 / RISK SENSITIVITY","Convexity & Positioning",
      "Gamma 集中度、跨波动率敏感度、时间衰减和基于未平仓量的情景压力映射。",
      "MODEL-BASED · NOT DEALER NET")+
    warning+overview+
    '<div class="quant-grid quant-section-space"><article class="quant-panel">'+
      panelTitle("Gamma concentration · Strike","USD Δ CHANGE / +1% UNDERLYING")+
      gammaStrikes(risk)+'</article><article class="quant-panel">'+
      panelTitle("OI cluster · Expiry","CALL / PUT OPEN INTEREST")+
      expiryConcentration(risk)+'</article></div>'+
    '<div class="quant-grid equal"><article class="quant-panel">'+panelTitle("Gross Δ rehedge · Spot shocks","IV HELD CONSTANT")+
      shockTable(risk)+'<p class="quant-surface-note">该数值为全 OI 绝对 Delta 变化对应的美元名义量，排除净头寸抵消、库存方向和交易冲击。</p></article>'+
    '<article class="quant-panel">'+panelTitle("Second-order mechanics","CONVEXITY RESEARCH")+
      '<div class="quant-risk-stats">'+riskNumber("Gamma","∂Δ/∂F，标的移动敏感度","2nd order")+
      riskNumber("Vanna","∂Δ/∂σ，波动率变化下的 Delta 敏感度","2nd order")+
      riskNumber("Volga","∂Vega/∂σ，波动率凸性","2nd order")+
      riskNumber("Charm","时间流逝带来的 Delta 漂移","time cross")+
      '</div></article></div>';
}

function futuresMatrix(asset){
  const m=assetModel();
  const history=m?.futures_surface?.[state.family] || [];
  if(!history.length)return empty("该市场没有足够的历史期货期限结构。");
  const cols=["7","30","60","90","180"];
  const all=history.flatMap(h=>cols.map(k=>h.nodes?.[k])).filter(finite);
  if(!all.length)return empty("尚无可计算的多期限年化基差；可能只有永续合约或到期结构不足。");
  const cap=Math.max(...all.map(x=>Math.abs(x)),1);
  return '<div class="quant-heatmap-outer"><table class="quant-heatmap">'+
    '<thead><tr><th>UTC 观测</th>'+cols.map(k=>'<th>'+k+' D</th>').join("")+'</tr></thead><tbody>'+
    history.slice().reverse().map(h=>'<tr><td>'+esc(h.slot.slice(5,16).replace("T"," "))+'<small>UTC</small></td>'+
      cols.map(k=>{
        const val=h.nodes?.[k];if(!finite(val))return '<td><span class="quant-heatcell none">—</span></td>';
        const alpha=Math.min(65,20+Math.abs(Number(val))/cap*42);
        return '<td><span class="quant-heatcell" style="--intensity:'+alpha.toFixed(0)+'%;cursor:default;color:'+
          (val<0?"#ffd0c9":"#f2ffd8")+'" title="'+esc(k+"天 年化基差 "+money(val,2)+"%")+
          '">'+signed(val,2)+'%<small>annualized</small></span></td>';
      }).join("")+'</tr>').join("")+'</tbody></table></div>';
}
function futuresView(){
  const a=assetModel();const rows=(a?.futures||[]).filter(r=>r.family===state.family);
  const current=rows.filter(r=>finite(r.annualized_basis_pct)&&finite(r.days))
    .sort((x,y)=>x.days-y.days);
  const futuresChart=lineChart([{
    name:"Annualized basis",points:current.map(r=>({x:r.days,y:r.annualized_basis_pct})),
  }],{label:"远期期货年化基差期限结构",includeZero:true,precision:2});
  const summary='<div class="quant-grid equal"><article class="quant-panel">'+
    panelTitle("Annualized forward basis","TERM STRUCTURE · % PA")+
    futuresChart+'<p class="quant-surface-note">仅匹配相同计价体系的参考指数，且到期剩余≥1天。图中可能出现非均匀到期节点；不把永续资金费率混入基差。</p></article>'+
    '<article class="quant-panel">'+panelTitle("Forward market snapshot",state.family==="inverse"?"INVERSE / USD":"LINEAR / USDC")+
    '<div class="quant-risk-stats">'+
      riskNumber("Reference spot","匹配对应币种指数",finite(a?.reference_price)?"$"+money(a.reference_price,2):"—")+
      riskNumber("Tradable maturities","具备年化基差观测的合约",int(current.length))+
      riskNumber("Front contract basis","首个到期合约年化基差",current.length?signed(current[0].annualized_basis_pct,2)+"%":"—")+
      riskNumber("90D basis","最近有效90日期限插值",finite((a?.futures_surface?.[state.family]||[]).at(-1)?.nodes?.["90"])?signed((a.futures_surface[state.family]).at(-1).nodes["90"],2)+"%":"—")+
    '</div></article></div>';
  const table=rows.length?'<div class="quant-table-scroll"><table class="quant-table"><thead><tr>'+
    '<th>Instrument</th><th>Expiry</th><th>Days</th><th>Mark</th><th>Reference</th><th>Basis</th><th>Ann. basis</th>'+
    '</tr></thead><tbody>'+rows.map(r=>'<tr><td>'+esc(r.instrument)+'</td>'+
      '<td>'+esc(r.expiry?dateUtc(r.expiry):"PERP")+'</td><td>'+esc(finite(r.days)?money(r.days,1):"—")+'</td>'+
      '<td>'+esc(money(r.mark,2))+'</td><td>'+esc(money(r.reference,2))+'</td>'+
      '<td class="'+(r.basis_pct<0?"bad":"good")+'">'+esc(finite(r.basis_pct)?signed(r.basis_pct,3)+"%":"—")+'</td>'+
      '<td class="'+(r.annualized_basis_pct<0?"bad":"good")+'">'+esc(finite(r.annualized_basis_pct)?signed(r.annualized_basis_pct,2)+"%":"—")+'</td></tr>').join("")+
    '</tbody></table></div>':empty("该结算市场没有已上市期货合约。");
  return sectionHead("03 / FORWARD TERM STRUCTURE","Futures Basis Surface",
    "跨到期与跨观测时段的年化期货基差，按 USD/USDC 结算结构独立计算。",
    state.family==="inverse"?"COIN SETTLED":"USDC LINEAR")+
    summary+'<div class="quant-section-space"><article class="quant-panel">'+
    panelTitle("Basis surface · Observation × Tenor","7 / 30 / 60 / 90 / 180 D")+
    futuresMatrix(state.asset)+'</article></div>'+
    '<div class="quant-section-space">'+sectionHead("FORWARD BOOK","合约期限与基差明细",
      "原生合约数据参与计算，但这里只展示可比期限价值和年度化基差。")+table+'</div>';
}
function modelFor(row){
  return market()?.greeks_by_instrument?.[row.instrument_name]||null;
}
function optionRows(){
  const q=state.optionSearch.trim().toLowerCase();
  const now=new Date(state.data.latest_slot).getTime();
  const f=latestChain().filter(r=>{
    const days=(Number(r.expiration_timestamp)-now)/86400000;
    return (state.optionSide==="all"||r.option_type===state.optionSide) &&
      (state.optionTenor==="all"||
       (state.optionTenor==="7" && days<=7) ||
       (state.optionTenor==="30" && days>7 && days<=30) ||
       (state.optionTenor==="90" && days>30 && days<=90) ||
       (state.optionTenor==="long" && days>90)) &&
      (!q||String(r.instrument_name).toLowerCase().includes(q));
  });
  const key=state.optionSort;
  f.sort((a,b)=>{
    const av=key==="iv"?a.mark_iv:key==="delta"?Math.abs(modelFor(a)?.delta??-1):
      key==="strike"?a.strike:key==="expiry"?a.expiration_timestamp:a.open_interest;
    const bv=key==="iv"?b.mark_iv:key==="delta"?Math.abs(modelFor(b)?.delta??-1):
      key==="strike"?b.strike:key==="expiry"?b.expiration_timestamp:b.open_interest;
    const descending=key==="oi"||key==="iv";
    return (Number(descending?bv:av)||0)-(Number(descending?av:bv)||0);
  });
  return f;
}
function chainTable(){
  const list=optionRows(),pageSize=35,total=Math.max(1,Math.ceil(list.length/pageSize));
  state.optionPage=Math.max(0,Math.min(state.optionPage,total-1));
  const visible=list.slice(state.optionPage*pageSize,(state.optionPage+1)*pageSize);
  const items=visible.map(r=>{
    const g=modelFor(r)||{};
    return '<tr><td>'+esc(r.instrument_name)+'</td><td>'+esc(r.option_type)+'</td>'+
      '<td>'+esc(dateUtc(r.expiration_timestamp))+'</td><td>'+esc(money(r.strike,0))+'</td>'+
      '<td>'+esc(iv(r.mark_iv))+'</td><td>'+esc(money(g.delta,3))+'</td>'+
      '<td>'+esc(finite(g.gamma)?Number(g.gamma).toExponential(2):"—")+'</td>'+
      '<td>'+esc(money(g.vanna,3))+'</td><td>'+esc(money(g.volga,2))+'</td>'+
      '<td>'+esc(money(g.charm_day,5))+'</td><td>'+esc(money(r.open_interest,2))+'</td></tr>';
  }).join("");
  return '<div class="quant-table-scroll"><table class="quant-table"><thead><tr>'+
    '<th>Instrument</th><th>Side</th><th>Expiry</th><th>Strike</th><th>Mark IV</th><th>Δ*</th>'+
    '<th>Γ*</th><th>Vanna*</th><th>Volga*</th><th>Charm/day*</th><th>OI</th></tr></thead><tbody>'+
    (items||'<tr><td colspan="11">该筛选条件下没有已归档合约。</td></tr>')+'</tbody></table></div>'+
    '<div class="quant-pagination"><span>显示 '+int(list.length?state.optionPage*pageSize+1:0)+'–'+
    int(Math.min(list.length,(state.optionPage+1)*pageSize))+' / '+int(list.length)+'</span><div>'+
    '<button type="button" data-page="-1" '+(state.optionPage===0?"disabled":"")+'>上一页</button>'+
    '<button type="button" data-page="1" '+(state.optionPage>=total-1?"disabled":"")+'>下一页</button></div></div>';
}
function optionsView(){
  const m=market();if(!m)return empty("当前没有已归档期权链。PAXG 不提供可用 Deribit 期权。");
  const menus='<div class="quant-table-controls"><label>方向<select data-filter="side">'+
    [["all","全部"],["call","Call"],["put","Put"]].map(([key,label])=>
      '<option value="'+key+'" '+(state.optionSide===key?"selected":"")+'>'+label+'</option>').join("")+
    '</select></label><label>剩余天数<select data-filter="tenor">'+
    [["all","全部期限"],["7","≤7 天"],["30","8–30 天"],["90","31–90 天"],["long",">90 天"]].map(([key,label])=>
      '<option value="'+key+'" '+(state.optionTenor===key?"selected":"")+'>'+label+'</option>').join("")+
    '</select></label><label>排序<select data-filter="sort">'+
    [["oi","OI ↓"],["iv","IV ↓"],["delta","|Delta| ↑"],["expiry","到期 ↑"],["strike","行权价 ↑"]].map(([key,label])=>
      '<option value="'+key+'" '+(state.optionSort===key?"selected":"")+'>'+label+'</option>').join("")+
    '</select></label><label>合约搜索<input data-filter="search" type="search" placeholder="BTC- / ETH-..." value="'+
    esc(state.optionSearch)+'"></label></div>';
  return sectionHead("04 / CONTRACT SENSITIVITIES","Option Chain & Greeks",
    "逐合约模型敏感度用于核对曲面、期限集中度与聚合风险结果。",
    "MODEL Δ / Γ / VANNA / VOLGA / CHARM")+
    '<div class="quant-muted-warning">带 * 的 Greeks 均由 Black-76 从 Mark IV 推导，单位为单个标的的模型敏感度，非 Deribit 直接推送，未乘未平仓量。</div>'+
    '<article class="quant-panel">'+menus+'<div id="quant-chain-table">'+chainTable()+'</div></article>';
}
function render(){
  if(!state.data)return;
  chooseProduct();syncControls();
  const html=state.product==="futures"?futuresView():
    state.tab==="surface"?surfaceView():state.tab==="risk"?riskView():optionsView();
  $("quant-content").innerHTML=html;
}
function bindEvents(){
  document.body.addEventListener("click",ev=>{
    const retry=ev.target.closest("[data-retry-deribit]");
    if(retry){boot();return;}
    const product=ev.target.closest("[data-product]");
    if(product&&!product.disabled){
      state.product=product.dataset.product;
      state.tab=state.product==="options"?state.optionTab:"futures";
      state.family="inverse";state.selectedExpiry=null;state.optionPage=0;
      render();return;
    }
    const asset=ev.target.closest("[data-asset]");
    if(asset){state.asset=asset.dataset.asset;state.selectedExpiry=null;state.optionPage=0;
      state.family=state.asset==="PAXG"?"linear":"inverse";
      render();return;}
    const fam=ev.target.closest("[data-family]");
    if(fam&&!fam.disabled&&availableFamilies().includes(fam.dataset.family)){
      state.family=fam.dataset.family;state.selectedExpiry=null;state.optionPage=0;render();return;}
    const tab=ev.target.closest("[data-tab]");
    if(tab&&state.product==="options"&&OPTION_TABS.includes(tab.dataset.tab)){
      state.tab=tab.dataset.tab;state.optionTab=tab.dataset.tab;render();return;
    }
    const expiry=ev.target.closest("[data-surface-expiry]");
    if(expiry){state.selectedExpiry=expiry.dataset.surfaceExpiry;render();return;}
    const page=ev.target.closest("[data-page]");
    if(page&&!page.disabled){state.optionPage+=Number(page.dataset.page);
      const node=$("quant-chain-table");if(node)node.innerHTML=chainTable();}
  });
  $("quant-content").addEventListener("change",ev=>{
    const filter=ev.target.dataset.filter;if(!filter||filter==="search")return;
    if(filter==="side")state.optionSide=ev.target.value;
    if(filter==="tenor")state.optionTenor=ev.target.value;
    if(filter==="sort")state.optionSort=ev.target.value;
    state.optionPage=0;render();
  });
  $("quant-content").addEventListener("input",ev=>{
    if(ev.target.dataset.filter!=="search")return;
    state.optionSearch=ev.target.value;state.optionPage=0;
    const node=$("quant-chain-table");if(node)node.innerHTML=chainTable();
  });
}
const DATA_TIMEOUT_MS = 7000;
let loadSerial = 0;
let activeRequest = null;

function finishBoot(){
  if(typeof window !== "undefined"){
    window.__nassetDeribitLoadSettled = true;
    clearTimeout(window.__nassetDeribitWatchdog);
  }
}

async function fetchDashboard(){
  const controller = new AbortController();
  activeRequest = controller;
  let timeoutId;
  try {
    return await Promise.race([
      (async()=>{
        const res=await fetch("./data/deribit-dashboard.json?t="+Date.now(),{
          cache:"no-store",signal:controller.signal,
        });
        if(!res.ok)throw new Error("数据接口 HTTP "+res.status);
        return res.json();
      })(),
      new Promise((_,reject)=>{
        timeoutId=setTimeout(()=>{
          reject(new Error("下载市场数据超时（7 秒）"));
          controller.abort();
        },DATA_TIMEOUT_MS);
      }),
    ]);
  }finally{
    clearTimeout(timeoutId);
    if(activeRequest===controller)activeRequest=null;
  }
}

async function boot(){
  const serial=++loadSerial;
  if(activeRequest)activeRequest.abort();
  if(typeof window !== "undefined")window.__nassetDeribitLoadSettled=false;
  $("quant-kpis").innerHTML='<div class="quant-loading">正在下载已计算的市场快照…</div>';
  $("quant-asof").innerHTML='<span>SNAPSHOT / UTC</span><strong>正在读取…</strong>';
  $("quant-content").innerHTML='<div class="quant-loading">正在读取 GitHub Pages 市场数据，不会在设备上重新计算 Greeks。</div>';
  // Published Pages bundles a verified snapshot into the HTML. Prefer this
  // synchronous, same-document source: no second large network request.
  const embedded=$("deribit-dashboard-bootstrap");
  if(embedded){
    try{
      const data=JSON.parse(embedded.textContent);
      if(data.version!==1||data.analytics?.version!==1||!data.analytics?.assets?.BTC)
        throw new Error("嵌入数据模型版本无效");
      state.data=data;
      render();
      finishBoot();
      return;
    }catch(err){
      console.error("Embedded Deribit snapshot is invalid; trying JSON fallback",err);
    }
  }
  let lastError;
  for(let attempt=1;attempt<=2;attempt++){
    try{
      const data=await fetchDashboard();
      if(serial!==loadSerial)return;
      if(data.version!==1||data.analytics?.version!==1||!data.analytics?.assets)
        throw new Error("分析模型版本与页面不匹配");
      if(!data.analytics.assets.BTC)throw new Error("缺少 BTC 研究数据");
      state.data=data;
      render();
      finishBoot();
      return;
    }catch(err){
      if(serial!==loadSerial)return;
      lastError=err;
      console.error("Deribit dashboard load attempt "+attempt+" failed",err);
      if(attempt<2)await new Promise(resolve=>setTimeout(resolve,500));
    }
  }
  if(serial!==loadSerial)return;
  $("quant-kpis").innerHTML="";
  $("quant-asof").innerHTML='<span>STATUS</span><strong>数据读取失败</strong>';
  $("quant-content").innerHTML='<div class="quant-error"><strong>无法加载 Deribit 研究数据。</strong>'+
    '<p>'+esc(lastError?.message||"数据读取失败")+'</p>'+
    '<p>期权风险指标已在服务器预计算；请检查当前网络或重新加载。</p>'+
    '<p><button type="button" data-retry-deribit>重新加载</button> · '+
    '<a href="./data/deribit-dashboard.json" target="_blank" rel="noopener noreferrer">检查数据 JSON</a></p></div>';
  finishBoot();
}

bindEvents();
boot();
