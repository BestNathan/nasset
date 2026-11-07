"use strict";
// Verify the *deployed HTML* contains the full verified data and app without
// an additional runtime JS or JSON request.
const fs=require("node:fs");
const assert=require("node:assert/strict");
const html=fs.readFileSync(process.argv[2]||"site/deribit.html","utf8");
const expected=JSON.parse(fs.readFileSync(process.argv[3],"utf8"));
const marker='<script id="deribit-dashboard-bootstrap" type="application/json">';
const start=html.indexOf(marker);
assert.ok(start>0,"snapshot JSON must be embedded in the HTML");
const close=html.indexOf("</script>",start+marker.length);
assert.ok(close>start,"embedded JSON must have a close tag");
const data=JSON.parse(html.slice(start+marker.length,close).trim());
assert.equal(data.archive_commit,expected.archive_commit);
assert.equal(data.latest_slot,expected.latest_slot);
assert.equal(data.latest.option_chain.length,expected.latest.option_chain.length);
assert.equal(Object.keys(data.analytics.assets.BTC.markets.inverse.greeks_by_instrument).length,
  Object.keys(expected.analytics.assets.BTC.markets.inverse.greeks_by_instrument).length);
assert.ok(html.includes('<script data-nasset-deribit-inline-app>'));
assert.ok(html.includes('const embedded=$("deribit-dashboard-bootstrap")'));
assert.ok(!/<script src="\.\/deribit\.js/.test(html),"no external JS dependency");
console.log("Standalone production HTML includes",data.latest.option_chain.length,
  "options and all compiled analytics without a runtime data request");
