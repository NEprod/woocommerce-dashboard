import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../app/static/assets/js/sale-editor.js',import.meta.url),'utf8');
function dates() { const context=vm.createContext({Intl,Date});vm.runInContext(source,context);return context.TLCSaleDates; }

test('UK combined date/time resolves GMT and BST independently of browser timezone',()=>{
  const d=dates();
  assert.equal(d.parse('01/07/2026 12:30'),'2026-07-01T11:30:00.000Z');
  assert.equal(d.parse('01/12/2026 12:30'),'2026-12-01T12:30:00.000Z');
  assert.equal(d.display(new Date('2026-07-01T11:30Z')),'01/07/2026 12:30');
  assert.equal(d.zone(new Date('2026-07-01T11:30Z')),'BST');
  assert.equal(d.zone(new Date('2026-12-01T11:30Z')),'GMT');
});
test('ambiguous autumn hour requires an explicit clock; spring gap and invalid calendar dates fail',()=>{
  const d=dates();
  assert.throws(()=>d.parse('25/10/2026 01:30'),/twice/);
  assert.equal(d.parse('25/10/2026 01:30','BST'),'2026-10-25T00:30:00.000Z');
  assert.equal(d.parse('25/10/2026 01:30','GMT'),'2026-10-25T01:30:00.000Z');
  for(const value of ['29/03/2026 01:30','31/02/2026 12:00','12/25/2026 12:00','2026-12-01']) assert.throws(()=>d.parse(value));
  assert.throws(()=>d.parse('01/12/2026 12:00','BST'),/incorrect/);
});
test('existing inclusive date-only end displays following midnight without changing raw semantics',()=>{
  const d=dates();
  assert.equal(d.stored('2026-03-29',true).text,'30/03/2026 00:00');
  assert.match(d.stored('2026-03-29',true).note,/retained unchanged/);
  assert.equal(d.stored('2026-10-25',false).text,'25/10/2026 00:00');
});
test('unchanged displayed controls retain original seconds, offset and date-only strings',()=>{
  const d=dates();
  for(const raw of ['2026-10-25','2026-07-01T12:30:45.123+01:00']) {
    const nodes={'[data-field]':{value:raw},'[data-sale-datetime]':{setCustomValidity(){}},'[data-sale-offset]':{},'[data-sale-date-note]':{},'[data-sale-date-error]':{}};
    const wrapper={dataset:{saleDateField:'sale_end_date'},querySelector:s=>nodes[s]};const root={querySelectorAll:()=>[wrapper]};
    d.refresh(root);d.sync(root);assert.equal(nodes['[data-field]'].value,raw);
    nodes['[data-sale-datetime]'].value='';d.sync(root);assert.equal(nodes['[data-field]'].value,'');
  }
});

class Node {
  constructor(){this.events={};this.children=[];this.dataset={};this.disabled=false;this.hidden=false;this.checked=false;this.textContent='';this.classes=new Set();this.classList={contains:v=>this.classes.has(v),add:v=>this.classes.add(v)};}
  addEventListener(name,fn){this.events[name]=fn;}
  async emit(name,event={}){return this.events[name]?.(event);}
  replaceChildren(...values){this.children=values;}
  appendChild(child){this.children.push(child);}
  focus(options){this.focused=options;}
}
function ui(){
  const root=new Node(),modal=new Node(),nodes=new Map(),calls=[],scrolls=[],intervals=[],timeouts=[];
  const node=key=>{if(!nodes.has(key))nodes.set(key,new Node());return nodes.get(key);};
  root.querySelector=node;root.querySelectorAll=()=>[];modal.querySelector=node;
  const editor={kind:'override',revision:'saved-revision',can_save:true,dirty:false,busy:false,data:{sale_price:'3.00'}};
  let now='2026-07-01T11:30:00Z', confirmWait=null, failure=null;
  class Clock extends Date{constructor(...args){super(...(args.length?args:[now]));}}
  const globalEvents={};
  const context=vm.createContext({Intl,Date:Clock,JSON,Promise,AbortController,Event:class {},
    document:{querySelector:s=>s==='[data-metadata-editor]'?root:{content:'csrf'},
      getElementById:id=>id==='saleReviewModal'?modal:{textContent:'{"sale_editor_url":"/sale/editor"}'},createElement:()=>new Node()},
    TLCMetadataSaleEditor:{snapshot:()=>structuredClone(editor)},
    bootstrap:{Modal:{getOrCreateInstance:()=>({show:()=>modal.classes.add('show')})}},scrollX:0,scrollY:450,
    scrollTo:(...args)=>scrolls.push(args),setInterval:fn=>{intervals.push(fn);return 1;},clearInterval:()=>{},setTimeout:(fn,ms)=>{timeouts.push({fn,ms});if(!ms)fn();return timeouts.length;},clearTimeout:()=>{},
    addEventListener:(name,fn)=>{globalEvents[name]=fn;},
    fetch:async(url,options)=>{
      const body=JSON.parse(options.body);calls.push({url,body,options});
      if(failure)throw new Error(failure);
      if(body.action==='confirm' && confirmWait)await confirmWait;
      const result=body.action==='status'?{ready:true,message:'Saved projection ready',schedule:{state:'authorised'}}:
        body.action==='preview'?{review:'signed-editor-review',product:{title:'TLC Product',sku:'TLC-1'},variants:[{sku:'TLC-1',regular:'5.00',sale:'3.00'}],start:'01/07/2026 12:30 BST',end:'Ongoing',schedule:{state:'scheduled',message:'Local'},action:'Authorise',warnings:['No Shopify write now']}:
        {authorised:true,message:'Authorisation saved',schedule:{state:'authorised'}};
      return {ok:true,json:async()=>result};
    }});
  vm.runInContext(source,context);
  return {context,editor,calls,modal,node,scrolls,intervals,timeouts,globalEvents,api:context.TLCSaleEditor,
    now:value=>{now=value;},failure:value=>{failure=value;},wait:value=>{confirmWait=value;}};
}
test('live UK clock updates each second and follows GMT/BST changes',()=>{
  const u=ui();assert.equal(u.node('[data-sale-clock]').textContent,'01/07/2026 12:30:00 BST');
  u.now('2026-12-01T11:30:01Z');u.intervals[0]();
  assert.equal(u.node('[data-sale-clock]').textContent,'01/12/2026 11:30:01 GMT');
});
test('unsaved, saving, scanner pending and unavailable sources disable review',async()=>{
  const u=ui();await u.api.check();assert.equal(u.node('[data-review-sale]').disabled,false);
  for(const key of ['dirty','busy']) {u.editor[key]=true;u.api.refresh();assert.equal(u.node('[data-review-sale]').disabled,true);u.editor[key]=false;}
  u.api.saved({run_id:'scan',scan_pending:true});assert.equal(u.node('[data-review-sale]').disabled,true);
  await u.api.scanned(true);await u.api.check();assert.equal(u.node('[data-review-sale]').disabled,false);
  u.editor.can_save=false;u.api.refresh();assert.equal(u.node('[data-review-sale]').disabled,true);
});
test('modal opening is read only; closing preserves editor draft, tab and scroll',async()=>{
  const u=ui(),before=structuredClone(u.editor);await u.api.check();await u.node('[data-review-sale]').emit('click');
  assert.deepEqual(u.calls.map(c=>c.body.action),['status','preview']);
  assert.equal(u.node('[data-sale-authorise]').disabled,true);
  await u.modal.emit('hidden.bs.modal');
  assert.deepEqual(u.editor,before);assert.deepEqual(u.scrolls,[[0,450]]);
  assert.equal(u.node('[data-review-sale]').focused.preventScroll,true);
  assert.equal(u.calls.filter(c=>c.body.action==='confirm').length,0);
});
test('edits after review invalidate confirmation and do not make a request',async()=>{
  const u=ui();await u.api.check();await u.node('[data-review-sale]').emit('click');
  u.node('[data-sale-ack]').checked=true;u.editor.dirty=true;u.api.refresh();
  assert.equal(u.node('[data-sale-authorise]').disabled,true);
  await u.node('[data-sale-authorise]').emit('click');assert.equal(u.calls.length,2);
});
test('explicit signed confirmation survives closing without cancellation or resubmission',async()=>{
  const u=ui();await u.api.check();await u.node('[data-review-sale]').emit('click');
  u.node('[data-sale-ack]').checked=true;await u.node('[data-sale-ack]').emit('change');
  let finish;u.wait(new Promise(resolve=>{finish=resolve;}));
  const operation=u.node('[data-sale-authorise]').emit('click');await Promise.resolve();
  await u.modal.emit('hidden.bs.modal');finish();await operation;
  const confirmations=u.calls.filter(c=>c.body.action==='confirm');assert.equal(confirmations.length,1);
  assert.equal(confirmations[0].body.review,'signed-editor-review');assert.equal(confirmations[0].options.headers['X-CSRFToken'],'csrf');
  assert.equal(u.node('[data-sale-readiness]').textContent,'Authorisation saved');
});
test('network failure is visible and never automatically retries authorisation',async()=>{
  const u=ui();await u.api.check();await u.node('[data-review-sale]').emit('click');
  u.node('[data-sale-ack]').checked=true;await u.node('[data-sale-ack]').emit('change');u.failure('Connection lost');
  await u.node('[data-sale-authorise]').emit('click');
  assert.match(u.node('[data-sale-modal-message]').textContent,/no automatic resubmission/);
  assert.equal(u.calls.filter(c=>c.body.action==='confirm').length,1);
  assert.equal(u.node('[data-sale-authorise]').disabled,true);
});
test('read and authorisation requests have bounded deadlines and closing does not abort confirmation',async()=>{
  const u=ui();await u.api.check();await u.node('[data-review-sale]').emit('click');
  assert.deepEqual(u.timeouts.map(t=>t.ms),[15000,120000]);
  u.node('[data-sale-ack]').checked=true;await u.node('[data-sale-ack]').emit('change');
  await u.node('[data-sale-authorise]').emit('click');
  const request=u.calls.at(-1);assert.equal(request.options.signal.aborted,false);
  await u.modal.emit('hidden.bs.modal');assert.equal(request.options.signal.aborted,false);
});
test('invalid Advanced JSON blocks readiness without an unhandled exception or remote request',async()=>{
  const u=ui();u.context.TLCMetadataSaleEditor.snapshot=()=>{throw new SyntaxError('Invalid JSON draft');};
  await u.api.check();u.globalEvents.focus();
  assert.equal(u.calls.length,0);assert.equal(u.node('[data-review-sale]').disabled,true);
  assert.match(u.node('[data-sale-readiness]').textContent,/invalid JSON/);
});
