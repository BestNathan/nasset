"use strict";
// The production loader must resolve a permanently pending HTTP request,
// display an actionable error, and keep its retry affordance usable.
const fs=require("node:fs");
const vm=require("node:vm");
const assert=require("node:assert/strict");
const script=fs.readFileSync("site/deribit.js","utf8");
const html=fs.readFileSync("site/deribit.html","utf8");
assert.match(html,/__nassetDeribitWatchdog/);
assert.match(html,/正在下载已计算的市场快照/);
assert.ok(!html.includes("正在计算隐含波动率"));

const elements=new Map();
function el(id){
  if(!elements.has(id))elements.set(id,{innerHTML:"",textContent:"",hidden:false,addEventListener(){}});
  return elements.get(id);
}
let attempts=0,click;
const win={};
const context=vm.createContext({
  console, Date, Number, Math, Set, Map, Intl, Array, Object, AbortController,
  window:win,
  setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,5)),
  clearTimeout,
  document:{
    getElementById:el,
    querySelectorAll:()=>[],
    body:{addEventListener:(name,fn)=>{if(name==="click")click=fn;}},
  },
  fetch:()=>{attempts++;return new Promise(()=>{});},
});
vm.runInContext(script,context,{timeout:10000,filename:"site/deribit.js"});
setTimeout(()=>{
  try{
    assert.equal(attempts,2,"each hung request should time out and retry exactly once");
    assert.match(el("quant-asof").innerHTML,/数据读取失败/);
    assert.match(el("quant-content").innerHTML,/超时/);
    assert.match(el("quant-content").innerHTML,/data-retry-deribit/);
    assert.equal(win.__nassetDeribitLoadSettled,true);
    click({target:{closest:selector=>selector==="[data-retry-deribit]"?{}:null}});
    assert.match(el("quant-content").innerHTML,/正在读取 GitHub Pages/);
    setTimeout(()=>{
      try {
        assert.equal(attempts,4,"manual retry must start fresh bounded requests");
        assert.match(el("quant-content").innerHTML,/超时/);
        console.log("Deribit loader handles hung fetches, retry, and script watchdog");
      }catch(err){console.error(err);process.exitCode=1;}
    },60);
  }catch(err){console.error(err);process.exitCode=1;}
},60);
