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
let fetchCount=0;


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
const products = ["options","futures"].map(id=>{
  const el=button("product:"+id);el.dataset.product=id;return el;
});
const tabs = ["surface","risk","options"].map(id=>{
  const el=button("tab:"+id);el.dataset.tab=id;return el;
});
// Production Pages embeds the verified snapshot; no JSON HTTP request is required.
element("deribit-dashboard-bootstrap").textContent=JSON.stringify(data);
let bodyClick;
const context = vm.createContext({
  console, Date, Number, Math, Set, Map, Intl, Array, Object,
  setTimeout, clearTimeout, AbortController, window:{},
  document:{
    getElementById:element,
    querySelectorAll(query){
      if(query==="[data-asset]")return assets;
      if(query==="[data-family]")return families;
      if(query==="[data-tab]")return tabs;
      if(query==="[data-product]")return products;
      throw new Error("unexpected DOM selector "+query);
    },
    body:{addEventListener(name,handler){if(name==="click")bodyClick=handler;}},
  },
  fetch:async ()=>{
    fetchCount++;
    throw new Error("embedded Deribit snapshot must not trigger network fetch");
  },
});
vm.runInContext(script,context,{timeout:10000,filename:"site/deribit.js"});
const evaluate = cmd => vm.runInContext(cmd,context,{timeout:10000});
const click=(selector,control)=>bodyClick({target:{closest:q=>q===selector?control:null}});
const contains=(text)=>{
  const content=element("quant-content").innerHTML;
  assert.ok(content.includes(text),"expected "+text+" in rendered "+content.slice(0,300));
};
setImmediate(()=>{
  try{
    contains("Volatility Surface");
    contains("quant-heatmap");
    assert.equal(evaluate("state.product"),"options");
    assert.equal(element("quant-tabs").hidden,false);
    assert.ok(element("quant-kpis").innerHTML.includes("30D ATM IV"));
    click("[data-tab]",tabs.find(x=>x.dataset.tab==="risk"));
    contains("Convexity &amp; Positioning");
    contains("Gamma concentration");
    click("[data-tab]",tabs.find(x=>x.dataset.tab==="options"));
    contains("Option Chain &amp; Greeks");
    contains("BTC");

    // Changing product never leaves option IV/Gamma indicators in futures KPIs.
    click("[data-product]",products.find(x=>x.dataset.product==="futures"));
    assert.equal(evaluate("state.product"),"futures");
    assert.equal(element("quant-tabs").hidden,true);
    contains("Futures Basis Surface");
    assert.ok(element("quant-kpis").innerHTML.includes("近月年化基差"));
    assert.ok(!element("quant-kpis").innerHTML.includes("30D ATM IV"));
    assert.ok(!element("quant-kpis").innerHTML.includes("Gross Gamma"));

    // The same product uses its own available settlement families, including PAXG linear.
    click("[data-asset]",assets.find(x=>x.dataset.asset==="PAXG"));
    assert.equal(evaluate("state.family"),"linear");
    assert.equal(evaluate("state.product"),"futures");
    assert.equal(products.find(x=>x.dataset.product==="options").disabled,true);
    contains("Futures Basis Surface");
    click("[data-product]",products.find(x=>x.dataset.product==="options"));
    assert.equal(evaluate("state.product"),"futures");
    click("[data-asset]",assets.find(x=>x.dataset.asset==="ETH"));
    click("[data-product]",products.find(x=>x.dataset.product==="options"));
    assert.equal(evaluate("state.product"),"options");
    assert.equal(evaluate("state.tab"),"options"); // previously selected option view restored
    assert.equal(element("quant-tabs").hidden,false);
    contains("Option Chain &amp; Greeks");
    click("[data-tab]",tabs.find(x=>x.dataset.tab==="surface"));
    contains("Volatility Surface");
    click("[data-asset]",assets.find(x=>x.dataset.asset==="PAXG"));
    assert.equal(evaluate("state.product"),"futures"); // automatic fallback
    assert.equal(evaluate("state.family"),"linear");
    assert.ok(element("quant-product-note").textContent.includes("没有可验证"));

    assert.equal(fetchCount,0,"embedded bootstrap must render without a separate HTTP request");
    console.log("Verified distinct options/futures navigation, KPI separation, ETH/BTC markets and PAXG-only futures from",
      data.analytics.as_of);
  }catch(err){console.error(err);process.exitCode=1;}
});
