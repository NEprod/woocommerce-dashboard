import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const source=fs.readFileSync(new URL('../../app/static/assets/js/shopify-publish.js',import.meta.url),'utf8');
const context={URL}; vm.createContext(context); vm.runInContext(source,context);
const client=context.ShopifyPublishClient;
test('only independently reported terminal outcomes end progress',()=>{
  for(const s of ['waiting','running','creating','updating']) assert.equal(client.terminal(s),false);
  for(const s of ['completed','processing','attention','failed','recovery_required']) assert.equal(client.terminal(s),true);
});
test('return destination preserves Products context but rejects other origins/routes',()=>{
  assert.equal(client.destination('/products?q=Hero#product-7','https://tlc.test'),'https://tlc.test/products?q=Hero#product-7');
  assert.throws(()=>client.destination('https://other.test/products','https://tlc.test'));
  assert.throws(()=>client.destination('/shopify/drafts','https://tlc.test'));
});
test('reload restores polling, never a second confirmation request',()=>{
  const reconnect=source.slice(source.indexOf('try { pending = JSON.parse'));
  assert.match(reconnect,/poll\(\)/); assert.doesNotMatch(reconnect,/fetch\(/);
  assert.match(source,/if \(submitted\) return/);
  assert.match(source,/sessionStorage\.setItem/);
  assert.doesNotMatch(source,/percent|progressbar|retry\(/);
});
