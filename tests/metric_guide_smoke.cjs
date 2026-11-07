"use strict";
const fs=require("node:fs");
const vm=require("node:vm");
const assert=require("node:assert/strict");
const context=vm.createContext({});
vm.runInContext(fs.readFileSync("site/metric-guide.js","utf8"),context,{filename:"metric-guide.js"});
const guide=context.NassetMetricGuide;
assert.ok(guide);

const labels=[
  "30D ATM IV","25Δ Risk Reversal","25Δ Butterfly","DVOL","Options OI",
  "Gross Gamma / 1%","GEX / 1%","Vanna / 1 vol pt","Charm / 1 day",
  "Volga / (1vol pt)²","Vega / 1 vol pt","Put / Call OI","Gamma Wall",
  "≤ 7D Gamma","Gamma","Delta","Vanna","Volga","Charm","Hourly RV",
  "IV − RV","Mark IV","OI","近月年化基差","90D 年化基差",
  "2025已实施股息率","分红CAGR (2015—25)","已观测减息",
  "近12个月现金股息率","最大回撤","年化波动率","Sortino (Rf=0)",
  "跟踪资产","期权矩阵","平均现金收益率","最高策略收益率",
  "Current price (USD / unit)","Asset change (CNY)","Measured cash yield",
  "Total return CAGR (USD)","Annualized cash yield / APR",
];
for(const label of labels) {
  assert.ok(guide.resolve(label),"Missing metric formula: "+label);
  const html=guide.wrap(label,'<div class="num">12%</div>',"metric");
  assert.match(html,/<details class="metric metric-disclosure"/);
  assert.match(html,/<summary>.*12%/s);
  assert.match(html,/计算公式 \/ 口径/);
  assert.match(html,/它说明什么/);
  assert.match(html,/限制与边界/);
  assert.ok(!html.includes("推导公式尚未"),"Unexpected placeholder for "+label);
}
// Semantics must not present gross OI Gamma as net dealer positioning.
const gamma=guide.wrap("Gross Gamma / 1%", "12", "metric");
assert.match(gamma,/不是净做市商 GEX/);
for(const [page,app] of [
  ["site/index.html","app.js"],
  ["site/topics.html","topics.js"],
  ["site/deribit.html","deribit.js"]
]) {
  const html=fs.readFileSync(page,"utf8");
  const i=html.indexOf('src="./metric-guide.js"');
  assert.ok(i>0&&i<html.indexOf('src="./'+app+'"'),"Shared guide must load first on "+page);
}
console.log("Metric disclosures and definitions: "+labels.length+" labels verified");
