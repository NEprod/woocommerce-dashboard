import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/sale-prices.js',import.meta.url),'utf8');
function harness() {
  let time=Date.parse('2026-10-15T16:00:00Z'), next=0;
  class Clock extends Date { static now(){return time;} }
  const context={Date:Clock,AbortController};vm.runInNewContext(source,context);
  const timers=new Map(),events={},documentEvents={},calls=[];
  const env={document:{hidden:false,querySelector:()=>({dataset:{salePricesUrl:'/api/products/pricing'}}),
      querySelectorAll:()=>[],addEventListener:(k,fn)=>documentEvents[k]=fn,removeEventListener:k=>delete documentEvents[k]},
    setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id),
    addEventListener:(k,fn)=>events[k]=fn,removeEventListener:k=>delete events[k],
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({items:[ended]})};}};
  const scheduled={id:1,price:{minimum:'3.50',maximum:'3.50',state:'scheduled',label:'Scheduled',as_of:'2026-10-15T16:00:00Z',next_transition:'2026-10-15T16:01:00Z'}};
  const ended={id:1,price:{minimum:'3.50',maximum:'3.50',state:'expired',label:'Ended',as_of:'2026-10-15T16:02:00Z',next_transition:null}};
  return {api:context.TLCSalePrices,env,timers,events,documentEvents,calls,scheduled,ended,advance:ms=>time+=ms};
}

test('exact server-formatted amounts and zero prices remain displayable',()=>{
  const {api}=harness();
  assert.equal(api.format({minimum:'0.00',maximum:'0.00'}),'£0.00');
  assert.equal(api.format({minimum:'1.50',maximum:'3.50'}),'£1.50–£3.50');
  assert.equal(api.format({minimum:null}),'Not set');
});
test('one local refresh is scheduled for the earliest known boundary',()=>{
  const {api,scheduled}=harness();
  assert.equal(api.delay([scheduled]),60300);
  assert.equal(api.delay([{price:{next_transition:null}}]),null);
  assert.equal(api.delay([scheduled,{price:{as_of:scheduled.price.as_of,next_transition:'2026-10-15T16:00:30Z'}}]),30300);
});
test('no sale/no future transition does not create a polling loop',()=>{
  const {api,env,timers,ended}=harness();api.watch([ended],()=>{},env);
  assert.equal(timers.size,0);
});
test('already-open page revalidates at boundary, updates price and stops after expiry',async()=>{
  const h=harness(),results=[];h.api.watch([h.scheduled],items=>results.push(items),h.env);
  const timer=[...h.timers.values()][0];h.advance(timer.ms);await timer.fn();
  assert.equal(h.calls.length,1);assert.equal(results[0][0].price.minimum,'3.50');
  assert.equal(results[0][0].price.label,'Ended');
  assert.equal(h.calls[0].url,'/api/products/pricing?ids=1');
  assert.equal(h.calls[0].options.method,undefined);assert.equal(h.calls[0].options.cache,'no-store');
});
test('hidden/suspended tab catches up once visible without Shopify polling',async()=>{
  const h=harness();h.api.watch([h.scheduled],()=>{},h.env);h.advance(120000);
  h.env.document.hidden=true;h.documentEvents.visibilitychange();assert.equal(h.calls.length,0);
  h.env.document.hidden=false;h.documentEvents.visibilitychange();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.calls.length,1);h.events.focus();assert.equal(h.calls.length,1);
});
test('concurrent focus and boundary events coalesce while the local request is pending',async()=>{
  const h=harness();let resolve;
  h.env.fetch=()=>{h.calls.push(true);return new Promise(r=>resolve=r);};
  h.api.watch([h.scheduled],()=>{},h.env);h.advance(120000);
  h.events.focus();h.events.pageshow();h.documentEvents.visibilitychange();
  assert.equal(h.calls.length,1);resolve({ok:true,json:async()=>({items:[h.ended]})});
  await new Promise(r=>setImmediate(r));
});
test('network failure never initiates writes or repeated automatic requests',async()=>{
  const h=harness();h.env.fetch=async()=>{h.calls.push(true);throw new Error('offline');};
  const notice={textContent:'£1.50'};h.env.document.querySelectorAll=()=>[notice];
  const watcher=h.api.watch([h.scheduled],()=>assert.fail('No invented prices'),h.env);
  await watcher.refresh();assert.equal(h.calls.length,1);
  assert.match(notice.textContent,/Local price refresh unavailable/);
  assert.equal([...h.timers.values()].filter(t=>t.ms===15000).length,0);
  h.events.focus();await new Promise(r=>setImmediate(r));assert.equal(h.calls.length,2);
});
test('navigation/filter replacement stops old timers and ignores late responses',async()=>{
  const h=harness();let resolve;
  h.env.fetch=()=>new Promise(r=>resolve=r);
  const watcher=h.api.watch([h.scheduled],()=>assert.fail('Old page must not overwrite new view'),h.env);
  const pending=watcher.refresh();watcher.stop();resolve({ok:true,json:async()=>({items:[h.ended]})});await pending;
  assert.equal(Object.keys(h.events).length,0);
});
test('date-only/timezone/decimal arithmetic remains exclusively server-authoritative',()=>{
  assert.doesNotMatch(source,/compareAtPrice|parseFloat|setInterval|\/api\/shopify/);
  assert.match(source,/Saved local schedule, not proof of Shopify execution/);
});
