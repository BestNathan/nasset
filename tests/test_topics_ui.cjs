"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const elements = new Map();
function el(id) {
  if (!elements.has(id)) elements.set(id, {
    value: "", innerHTML: "", textContent: "",
    addEventListener() {}, querySelectorAll() { return []; }
  });
  return elements.get(id);
}
const universe = [
  {symbol:"601398.SS",name:"工商银行",sector:"banking",group:"国有大行"},
  {symbol:"600941.SS",name:"中国移动",sector:"telecom",group:"综合运营商"}
];
const payload = {
  generated_at:"2026-10-09T00:00:00Z", stocks:{"601398.SS":{
    status:"observed",implemented_fy_yield_pct:3.5,
    price:{close_cny:8.2,as_of:"2026-10-08"},
    statistics:{years_with_cash:11,observed_adjacent_pairs:10,observed_cuts:0,
      cagr_pct:3.5,missing_fiscal_years:[],share_adjustment_required:false},
    years:{"2015":0.2333,"2025":0.3103},
    simulation:{start_date:"2015-01-05",initial_shares:10000,invested_cny:50000,
      cumulative_cash_cny:40000,cashflow_by_payment_year_cny:{"2025":3000}},
    risk:{annual_volatility_pct:17,max_drawdown_pct:-20,daily_cvar_95_pct:-2.3,
      observations:2500,rolling_returns:{}}
  }}
};
const context = {
  document:{getElementById:el,querySelectorAll:()=>[]},
  fetch:async url=>({ok:true,json:async()=>url.includes("universe")?universe:payload}),
  location:{search:""},window:{alert:()=>{}},URLSearchParams,console
};
vm.runInNewContext(fs.readFileSync("site/topics.js","utf8"),context,{filename:"site/topics.js"});
setImmediate(()=>{
  assert.match(el("topic-detail").innerHTML,/工商银行/);
  assert.match(el("topic-detail").innerHTML,/100万元买入持有/);
  assert.match(el("topic-detail").innerHTML,/历史年度|逐财年/);
  assert.match(el("topic-stats").innerHTML,/A股研究池/);
  assert.match(el("topic-nav").innerHTML,/中国移动/);
  console.log("Topic UI fixture render: PASS");
});
