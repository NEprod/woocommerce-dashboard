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

// Browser-shaped harness: named controls deliberately shadow form.action.
// It executes the submit/poll listeners, rather than only inspecting source text.
function page({stored=null}={}) {
  class Element {
    constructor() { this.listeners={}; this.hidden=true; this.textContent=''; this.open=false; }
    addEventListener(name,fn) { (this.listeners[name] ||= []).push(fn); }
    async emit(name) { for(const fn of this.listeners[name] || []) await fn({preventDefault(){}}); }
    showModal() { this.open=true; }
    close() { this.open=false; this.emit('close'); }
  }
  const selectors=['stage','message','history','products','product','details','completed','close'];
  const elements=Object.fromEntries(selectors.map(name=>[name,new Element()]));
  const dialog=new Element(),resume=new Element(),document=new Element(),button=new Element(),form=new Element();
  dialog.querySelector=selector=>elements[selector.match(/data-publish-(.+)\]/)[1]];
  form.action={name:'action',value:'confirm',toString(){return '[object HTMLInputElement]';}};
  form.getAttribute=name=>null; form.checkValidity=()=>true;
  form.dataset={progressUrl:'/products/7/shopify/progress',title:'Fictional product',sku:'FIC-7'};
  form.fields={action:'confirm',review:'signed-review',csrf_token:'csrf',return_to:'/products#product-7'};
  form.querySelectorAll=()=>[button];
  document.querySelector=selector=>selector==='[data-shopify-progress]' ? dialog : resume;
  document.querySelectorAll=()=>[form];
  const storage=new Map(),key='tlc-shopify-publish:/products/7/shopify/review';
  if(stored) storage.set(key,JSON.stringify(stored));
  let now=0,next=1; const timers=new Map(),requests=[],redirects=[];
  const location={pathname:'/products/7/shopify/review',href:'https://tlc.test/products/7/shopify/review',origin:'https://tlc.test',assign:url=>redirects.push(url)};
  const context={URL,URLSearchParams,AbortController,document,location,Date:class extends Date {static now(){return now;}},
    FormData:class {constructor(form){return Object.entries(form.fields);}},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    setTimeout:(fn,delay)=>{const id=next++;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
    fetch:(url,options)=>new Promise((resolve,reject)=>{requests.push({url,options,resolve:value=>resolve({ok:true,headers:{get:()=> 'application/json'},json:async()=>value}),reject});}),
    addEventListener:(name,fn)=>document.addEventListener(name,fn)};
  vm.createContext(context); vm.runInContext(source,context);
  const flush=async()=>{for(let i=0;i<10;i++) await Promise.resolve();};
  const timer=async delay=>{const entry=[...timers].find(([,value])=>value.delay===delay);assert.ok(entry,`Missing ${delay}ms timer`);timers.delete(entry[0]);entry[1].fn();await flush();};
  return {form,dialog,resume,elements,requests,timers,storage,redirects,key,flush,timer,advance:ms=>{now+=ms;}};
}

test('hidden action control cannot hijack the single confirmed POST destination',async()=>{
  const p=page(); const first=p.form.emit('submit'); await p.flush();
  assert.equal(p.requests[0].url,'https://tlc.test/products/7/shopify/review');
  assert.equal(p.requests[0].options.body.get('action'),'confirm');
  assert.equal(p.requests[0].options.signal,undefined); // never abort the writer on close
  await p.form.emit('submit'); assert.equal(p.requests.length,1);
  p.requests[0].resolve({state:'running',stage:'Validating signed confirmation'});await first;
  assert.match(p.elements.stage.textContent,/Validating/);
});

test('closing progress cancels reads and timers, not the outstanding mutation',async()=>{
  const p=page(); const submission=p.form.emit('submit'); await p.flush();
  await p.timer(500);assert.equal(p.requests.length,2);
  const read=p.requests[1];assert.equal(read.url,'/products/7/shopify/progress');
  await p.elements.close.emit('click');
  assert.equal(read.options.signal.aborted,true);assert.equal(p.dialog.open,false);
  assert.equal(p.requests[0].options.signal,undefined);
  read.reject(new Error('AbortError'));await p.flush();assert.equal(p.timers.size,0);
  p.requests[0].resolve({state:'completed',stage:'Verified'});await submission;
  assert.equal(p.redirects.length,0);assert.equal(p.timers.size,0);
});

test('reload reconnects only to the saved receipt and renders durable server stages',async()=>{
  const p=page({stored:{review:'signed-review',csrf:'csrf',url:'/products/7/shopify/progress',returnTo:'/products'}});
  assert.equal(p.requests.length,1);assert.equal(p.requests[0].url,'/products/7/shopify/progress');
  assert.equal(p.requests[0].options.body.get('action'),null);
  p.requests[0].resolve({state:'running',stage:'Verifying independent Shopify readback',elapsed_seconds:21,server_checked_at:'fictional server stamp',sku:'FIC-7',completed_stages:['Updating variants']});
  await p.flush();assert.match(p.elements.completed.textContent,/Updating variants/);
  assert.match(p.elements.details.textContent,/21 s elapsed.*fictional server stamp/);
  await p.elements.close.emit('click');assert.equal(p.timers.size,0);
  await p.resume.emit('click');assert.equal(p.requests.length,2);assert.equal(p.requests[1].url,'/products/7/shopify/progress');
  p.requests[1].resolve({state:'recovery_required',message:'Inspect retained identity',history_url:'/shopify/drafts/identities/2'});
  await p.flush();assert.equal(p.redirects.length,0);assert.equal(p.timers.size,0);
  assert.equal(p.elements.history.hidden,false);assert.equal(p.storage.has(p.key),false);
});

test('missing acceptance receipt is explicit and never automatically resubmitted',async()=>{
  const p=page({stored:{review:'signed-review',csrf:'csrf',url:'/products/7/shopify/progress',returnTo:'/products'}});
  p.advance(31000);p.requests[0].resolve({state:'waiting'});await p.flush();
  assert.match(p.elements.message.textContent,/No accepted operation receipt found/);
  assert.equal(p.timers.size,0);assert.equal(p.requests.length,1);assert.equal(p.storage.has(p.key),true);
});

test('completed acceptance-only receipt is not relabelled as API verified',async()=>{
  const p=page({stored:{review:'signed-review',csrf:'csrf',url:'/products/7/shopify/progress',returnTo:'/products'}});
  p.requests[0].resolve({state:'completed',completion_label:'Completed — Shopify Accepted',message:'Independent package readback unavailable'});
  await p.flush();
  assert.equal(p.elements.stage.textContent,'Completed — Shopify Accepted — returning to Products');
  assert.match(p.elements.message.textContent,/readback unavailable/);
  assert.equal(p.requests.length,1);
});

test('temporary receipt failure preserves the recovery ticket and can be read again',async()=>{
  const p=page({stored:{review:'signed-review',csrf:'csrf',url:'/products/7/shopify/progress',returnTo:'/products'}});
  p.requests[0].resolve({state:'receipt_unavailable',message:'Receipt temporarily unavailable'});await p.flush();
  assert.equal(p.storage.has(p.key),true);assert.equal(p.timers.size,0);
  await p.resume.emit('click');assert.equal(p.requests[1].url,'/products/7/shopify/progress');
  p.requests[1].resolve({state:'completed',return_url:'/products#product-7'});await p.flush();
  await p.timer(1200);assert.equal(p.redirects[0],'https://tlc.test/products#product-7');
});

test('read deadline and repeated failures pause checking without retrying a write',async()=>{
  const p=page({stored:{review:'signed-review',csrf:'csrf',url:'/products/7/shopify/progress',returnTo:'/products'}});
  await p.timer(10000);assert.equal(p.requests[0].options.signal.aborted,true);
  p.requests[0].reject(new Error('Read timed out'));await p.flush();
  for(let failures=1;failures<5;failures++) {
    await p.timer(Math.min(10000,2000*2**failures));
    p.requests.at(-1).reject(new Error('Receipt unavailable'));await p.flush();
  }
  assert.equal(p.timers.size,0);assert.equal(p.resume.hidden,false);
  assert.equal(p.storage.has(p.key),true);assert.equal(p.requests.length,5);
  assert.ok(p.requests.every(r=>r.url==='/products/7/shopify/progress'));
  assert.match(p.elements.message.textContent,/No mutation is retried/);
});
