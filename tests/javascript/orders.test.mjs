// Orders task polling touches protected local receipts only, never Shopify.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source=await readFile(new URL('../../app/static/assets/js/orders.js',import.meta.url),'utf8');
function world({active=true,hidden=false,response={ok:true,json:async()=>({active:false,label:'Saved',message:'Import complete',last_successful:'16/10/2026 12:00 BST'})},fail=false}={}) {
  const nodes=Object.fromEntries(['label','message','time','feedback'].map(k=>[`[data-order-sync-${k}]`,{textContent:''}]));
  const panel={dataset:{active:String(active),statusUrl:'/orders/status'},querySelector:key=>nodes[key]};
  const timers=[],calls=[];
  const context={document:{hidden,querySelector:()=>panel},AbortSignal:{timeout:ms=>({ms})},
    setTimeout:(fn,delay)=>timers.push({fn,delay}),fetch:async(url,opts)=>{calls.push([url,opts]);if(fail)throw Error('private details');return response;}};
  vm.runInNewContext(source,context);
  return {nodes,timers,calls,context};
}
test('idle page never polls or starts import',()=>{const w=world({active:false});assert.equal(w.timers.length,0);assert.equal(w.calls.length,0);});
test('active task uses one bounded local GET and updates accessible text',async()=>{
  const w=world();await w.timers.shift().fn();
  assert.equal(w.calls.length,1);assert.equal(w.calls[0][0],'/orders/status');
  assert.equal(w.calls[0][1].cache,'no-store');assert.equal(w.calls[0][1].signal.ms,8000);
  assert.equal(w.nodes['[data-order-sync-label]'].textContent,'Saved');
  assert.match(w.nodes['[data-order-sync-feedback]'].textContent,/Refresh the local list/);
  assert.equal(w.timers.length,0);
});
test('running import schedules bounded continuation without reload',async()=>{
  const w=world({response:{ok:true,json:async()=>({active:true,label:'Importing',message:'Saved',last_successful:'Not yet'})}});
  await w.timers.shift().fn();assert.equal(w.timers[0].delay,8000);assert.equal(w.calls.length,1);
});
test('network failure is local feedback, never publishing failure or retry storm',async()=>{
  const w=world({fail:true});await w.timers.shift().fn();
  assert.match(w.nodes['[data-order-sync-feedback]'].textContent,/server-side import is unaffected/);
  assert.doesNotMatch(w.nodes['[data-order-sync-feedback]'].textContent,/private details/);assert.equal(w.timers.length,0);
});
test('hidden tab makes no additional request',async()=>{const w=world({hidden:true});await w.timers.shift().fn();assert.equal(w.calls.length,0);});
test('expired session safely stops polling',async()=>{const w=world({response:{ok:false}});await w.timers.shift().fn();assert.equal(w.timers.length,0);});
test('customer strings are assigned as text, not HTML',async()=>{
  const w=world({response:{ok:true,json:async()=>({active:false,label:'<script>bad</script>',message:'safe',last_successful:'Not yet'})}});
  await w.timers.shift().fn();assert.equal(w.nodes['[data-order-sync-label]'].textContent,'<script>bad</script>');
  assert.ok(!source.includes('innerHTML'));
});
