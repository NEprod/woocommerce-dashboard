import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source=fs.readFileSync(new URL('../../app/static/assets/js/shopify-preview-ui.js',import.meta.url),'utf8');
function fixture({automatic='yes',pending=false,fields={},response=true,failure=false,action='',delay=false}={}) {
  const calls=[],events=[],handlers={},status={textContent:''},button={disabled:false},oldApproval={disabled:false};
  const form={dataset:{autoPreview:automatic},getAttribute:()=>action,checkValidity:()=>true,
    querySelector:s=>s==='[data-preview-status]'?status:button,
    addEventListener:(k,fn)=>{handlers[k]=fn;}, fields:{action:'preview',product_id:'1',csrf_token:'fixture-csrf',...fields}};
  const groups=[{dataset:{reviewGroup:'Product'},open:true},{dataset:{reviewGroup:'Variant FIC-1'},open:false}],matching={open:false};
  const result={setAttribute:()=>{},removeAttribute:()=>{},querySelector:s=>matching,querySelectorAll:s=>s==='[data-review-group]'?groups:[oldApproval],replaceChildren(...nodes){this.rendered=nodes;}};
  const document={querySelector:s=>s==='[data-preview-form]'?form:result,addEventListener:(k,fn)=>{handlers['doc:'+k]=fn;},
    importNode:n=>n,dispatchEvent:e=>events.push(e.type)};
  let resolve;
  const promise=delay?new Promise(r=>{resolve=r;}):Promise.resolve();
  const context={document,URL,URLSearchParams,Array,FormData:class{constructor(f){return Object.entries(f.fields);}},
    DOMParser:class{parseFromString(){return{querySelector:()=>({childNodes:['fresh preview']})};}},
    AbortController:class{constructor(){this.signal={};}abort(){this.aborted=true;}},CustomEvent:class{constructor(type){this.type=type;}},
    fetch:async(url,options)=>{calls.push({url,body:options.body.toString(),method:options.method});await promise;if(failure)throw Error('Temporary connection loss');return{ok:response,text:async()=>'<fixture>'};},
    location:{href:'https://fixture.test/products/1/shopify/review',origin:'https://fixture.test',pathname:'/products/1/shopify/review'},
    sessionStorage:{getItem:()=>pending?'existing-receipt':null},addEventListener:(k,fn)=>{handlers[k]=fn;}};
  context.window=context;
  vm.runInNewContext(source,context);
  return{calls,events,handlers,status,button,oldApproval,result,form,resolve,groups,matching};
}
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};

test('opening scoped review calls preview once, with CSRF and no approval',async()=>{
  const f=fixture();await settle();assert.equal(f.calls.length,1);
  const body=new URLSearchParams(f.calls[0].body);
  assert.equal(body.get('action'),'preview');assert.equal(body.get('csrf_token'),'fixture-csrf');
  assert.equal(body.has('review'),false);assert.equal(body.has('acknowledge'),false);
  assert.equal(f.calls[0].method,'POST');assert.match(f.status.textContent,/Preview ready/);
  assert.deepEqual(f.events,['tlc:review-ready']);assert.deepEqual(f.result.rendered,['fresh preview']);
});
test('unscoped page retains manual preview without an automatic request',async()=>{
  const f=fixture({automatic:'no'});await settle();assert.equal(f.calls.length,0);
  await f.handlers.submit({preventDefault(){}});assert.equal(f.calls.length,1);
});
test('advanced limited scope values are preserved exactly',async()=>{
  const f=fixture({fields:{inventory_only:'yes'}});await settle();
  const b=new URLSearchParams(f.calls[0].body);assert.equal(b.get('inventory_only'),'yes');assert.equal(b.has('customisations_only'),false);
});
test('overlapping preview clicks do not create duplicate preview calls',async()=>{
  const f=fixture({delay:true});assert.match(f.status.textContent,/Loading preview/);
  await f.handlers.submit({preventDefault(){}});assert.equal(f.calls.length,1);
  f.resolve();await settle();assert.equal(f.button.disabled,false);
});
test('a temporary failure keeps old approval disabled and never retries',async()=>{
  const f=fixture({failure:true});await settle();assert.equal(f.calls.length,1);
  assert.equal(f.oldApproval.disabled,true);assert.match(f.status.textContent,/stale \/ rebuild required/);
  assert.match(f.status.textContent,/No publishing or automatic retry/);assert.equal(f.events.length,0);
});
test('blocked preview displays server response without confirming or retrying',async()=>{
  const f=fixture({response:false});await settle();assert.equal(f.calls.length,1);
  assert.match(f.status.textContent,/failed or blocked/);assert.deepEqual(f.result.rendered,['fresh preview']);
});
test('active publishing receipt prevents automatic preview rebuild',async()=>{
  const f=fixture({pending:true});await settle();assert.equal(f.calls.length,0);assert.match(f.status.textContent,/Existing publishing receipt/);
});
test('preview can never submit an approval or another origin',async()=>{
  for(const options of [{fields:{review:'signed'}},{fields:{acknowledge:'yes'}},{action:'https://elsewhere.test/'}]){
    const f=fixture(options);await settle();assert.equal(f.calls.length,0);
  }
});
test('variant expand/collapse reveals matching variants without collapsing product changes',()=>{
  const f=fixture({automatic:'no'});
  for(const expanded of ['yes','no']){
    f.handlers['doc:click']({target:{closest:()=>({dataset:{reviewExpand:expanded}})}});
    assert.equal(f.groups[0].open,true);
    assert.equal(f.groups[1].open,expanded==='yes');assert.equal(f.matching.open,expanded==='yes');
  }
  assert.equal(f.calls.length,0);
});
test('new approval forms are bound once without changing the publishing handler',()=>{
  const publisher=fs.readFileSync(new URL('../../app/static/assets/js/shopify-publish.js',import.meta.url),'utf8');
  const code=publisher.slice(publisher.indexOf('  const boundForms ='),publisher.indexOf('  try { pending ='));
  const forms=[{count:0,addEventListener(){this.count++;}}],listeners={};
  vm.runInNewContext(code,{WeakSet,document:{querySelectorAll:()=>forms,addEventListener:(type,fn)=>{listeners[type]=fn;}}});
  listeners['tlc:review-ready']();assert.equal(forms[0].count,1);
  forms.push({count:0,addEventListener(){this.count++;}});listeners['tlc:review-ready']();listeners['tlc:review-ready']();
  assert.deepEqual(forms.map(f=>f.count),[1,1]);
});
