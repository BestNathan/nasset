"use strict";
/* Execute the actual static browser app with the real archived projection in
 * a no-network DOM stub. Throws on missing research panels / runtime errors.
 * This verifies data-to-view integration, not pixel-perfect rendering. */
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const script = fs.readFileSync("site/deribit.js", "utf8");
const elements = new Map();
const nodes = new Map();

function element(key) {
  if(!elements.has(key))elements.set(key,{
    innerHTML:"", hidden:false, addEventListener(){},
  });
  return elements.get(key);
}
function button(key) {
  if(!nodes.has(key))nodes.set(key,{
    dataset: {}, disabled:false, classList:{toggle(){}},
    setAttribute(){},
  });
  return nodes.get(key);
}
const assets = ["BTC","ETH","PAXG"].map(id=>{
  const el=button("asset:"+id);el.dataset.asset=id;return el;
});
const families = ["inverse","linear"].map(id=>{
  const el=button("family:"+id);el.dataset.family=id;return el;
});
const tabs = ["surface","risk","futures","options"].map(id=>{
  const el=button("tab:"+id);el.dataset.tab=id;return el;
});
const context = vm.createContext({
  console, Date, Number, Math, Set, Map, Intl, Array, Object,
  document:{
    getElementById:element,
    querySelectorAll(query){
      if(query==="[data-asset]")return assets;
      if(query==="[data-family]")return families;
      if(query==="[data-tab]")return tabs;
      throw new Error("unexpected DOM selector "+query);
    },
    body:{addEventListener(){}},
  },
  fetch:async url=>{
    assert.ok(url.startsWith("./data/deribit-dashboard.json?t="));
    return {ok:true,json:async()=>data};
  },
});
vm.runInContext(script,context,{timeout:10000,filename:"site/deribit.js"});
const evaluate = cmd => vm.runInContext(cmd,context,{timeout:10000});
const contains=(text)=>{
  const content=element("quant-content").innerHTML;
  assert.ok(content.includes(text),"expected "+text+" in rendered "+content.slice(0,300));
};
setImmediate(()=>{
  try{
    contains("Volatility Surface");
    contains("quant-heatmap");
    assert.ok(element("quant-kpis").innerHTML.includes("30D ATM IV"));
    evaluate('state.tab="risk";render()');
    contains("Convexity &amp; Positioning");
    contains("Gamma concentration");
    evaluate('state.tab="options";render()');
    contains("Option Chain &amp; Greeks");
    contains("BTC");
    evaluate('state.asset="PAXG";state.tab="futures";render()');
    contains("Futures Basis Surface");
    assert.equal(evaluate("state.family"),"linear");
    assert.ok(element("quant-kpis").innerHTML.includes("PAXG")||
      element("quant-kpis").innerHTML.includes("永续 / 期货"));
    evaluate('state.asset="ETH";state.tab="surface";state.family="inverse";render()');
    contains("Volatility Surface");
    console.log("Rendered BTC/ETH IV, option Greeks, OI risk and PAXG futures from",
      data.analytics.as_of,"without simulated data");
  }catch(err){console.error(err);process.exitCode=1;}
});
