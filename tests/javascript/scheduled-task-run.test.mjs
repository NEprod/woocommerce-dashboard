import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/scheduled-task-run.js',import.meta.url),'utf8');
const value=(state='running',checked=1)=>({id:'run-1',state,status_label:state.replaceAll('_',' '),
  terminal:['completed','partially_completed','failed','interrupted','cancelled'].includes(state),
  phase:state==='paused'?'Deferred — waiting for the next execution window':'Saved phase',counts:{checked},
  report_html:`<details><summary>Recent product outcomes</summary>${state} ${checked}</details>`});
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function setup({terminal=false,hidden=false,responses=[value()],open=true}={}) {
  const nodes=Object.fromEntries(['state','phase','counters','feedback'].map(key=>[key,{textContent:''}]));
  const timers=new Map(),calls=[],events={},windowEvents={};let id=0,index=0;
  const window={scrollX:0,scrollY:300,addEventListener:(key,fn)=>windowEvents[key]=fn,
    scrollTo(x,y){this.scrollX=x;this.scrollY=y;}};
  let html='initial';
  const report={details:[{open}],querySelectorAll(){return this.details;},
    get innerHTML(){return html;},set innerHTML(v){html=v;this.details=[{open:false}];window.scrollY=0;}};
  const panel={dataset:{runId:'run-1',terminal:String(terminal),statusUrl:'/settings/tasks/runs/run-1'},
    querySelector:key=>nodes[key.slice(10,-1)]};
  const document={hidden,querySelector:key=>key==='[data-task-run-view]'?panel:report,
    addEventListener:(key,fn)=>events[key]=fn};
  const navigator={onLine:true};
  async function fetch(url,options) {
    calls.push({url,options});
    const response=responses[Math.min(index++,responses.length-1)];
    if(response instanceof Error)throw response;
    if(response?.then)return new Promise((resolve,reject)=>{
      options.signal.addEventListener('abort',()=>reject(new Error('Aborted')),{once:true});
      response.then(resolve,reject);
    });
    if(response?.http)return {ok:false,status:response.http};
    return {ok:true,status:200,json:async()=>response};
  }
  const setTimeout=(fn,ms)=>{timers.set(++id,{fn,ms});return id;};
  vm.runInNewContext(source,{document,window,navigator,fetch,setTimeout,
    clearTimeout:id=>timers.delete(id),AbortController,Array,Object,Math,Error});
  async function tick() {
    const next=[...timers].filter(([,t])=>t.ms!==12000).sort((a,b)=>a[1].ms-b[1].ms)[0];
    assert.ok(next,'a read-only poll was scheduled');timers.delete(next[0]);await next[1].fn();await flush();
  }
  return {nodes,calls,timers,events,windowEvents,document,navigator,report,window,tick};
}

test('active page observes one fixed run with read-only uncached GETs every four seconds',async()=>{
  const p=setup({responses:[value('queued'),value('running',2),value('completed',3)]});await flush();
  assert.equal(p.calls.length,1);assert.match(p.nodes.counters.textContent,/checked: 1/);
  assert.ok([...p.timers.values()].some(t=>t.ms===4000));
  await p.tick();assert.match(p.nodes.counters.textContent,/checked: 2/);
  await p.tick();assert.match(p.nodes.state.textContent,/completed/);assert.equal(p.timers.size,0);
  for(const call of p.calls){
    assert.equal(call.url,'/settings/tasks/runs/run-1');assert.equal(call.options.method,undefined);
    assert.equal(call.options.headers.Accept,'application/json');assert.equal(call.options.cache,'no-store');
  }
});

test('terminal page does not poll, active failure or partial completion stops after final report',async()=>{
  const done=setup({terminal:true});await flush();assert.equal(done.calls.length,0);
  for(const state of ['failed','partially_completed','interrupted','cancelled']){
    const p=setup({responses:[value('running'),value(state,2)]});await flush();await p.tick();
    assert.match(p.report.innerHTML,new RegExp(state));assert.equal(p.timers.size,0);
    assert.match(p.nodes.feedback.textContent,/Final saved result/);
  }
});

test('deferred run remains active and resumes without another start',async()=>{
  const p=setup({responses:[value('paused'),value('running',2),value('completed',3)]});await flush();
  assert.match(p.nodes.phase.textContent,/Deferred/);await p.tick();await p.tick();assert.equal(p.calls.length,3);
});

test('temporary errors preserve last state and recover with bounded backoff',async()=>{
  const p=setup({responses:[value('running',1),new Error('disconnected'),value('completed',4)]});await flush();
  await p.tick();assert.equal(p.nodes.state.textContent,'running');assert.match(p.nodes.feedback.textContent,/state is unchanged/);
  assert.ok([...p.timers.values()].some(t=>t.ms===8000));
  await p.tick();assert.match(p.nodes.counters.textContent,/checked: 4/);assert.equal(p.timers.size,0);
});

test('hiding, navigation and offline state pause polling; returning resumes same run',async()=>{
  const p=setup();await flush();
  p.document.hidden=true;p.events.visibilitychange();assert.equal(p.timers.size,0);
  p.document.hidden=false;p.events.visibilitychange();await flush();assert.equal(p.calls.length,2);
  p.windowEvents.pagehide();assert.equal(p.timers.size,0);
  p.windowEvents.pageshow();await flush();assert.equal(p.calls.length,3);
  p.navigator.onLine=false;p.windowEvents.offline();assert.equal(p.timers.size,0);
  p.navigator.onLine=true;p.windowEvents.online();await flush();assert.equal(p.calls.length,4);
  assert.ok(p.calls.every(c=>!c.options.method));
});

test('expanded sections and scroll survive multiple report updates',async()=>{
  const p=setup({responses:[value('running'),value('running',2),value('completed',3)]});await flush();
  assert.equal(p.report.details[0].open,true);assert.equal(p.window.scrollY,300);
  p.report.details[0].open=false;await p.tick();assert.equal(p.report.details[0].open,false);
  assert.equal(p.window.scrollY,300);await p.tick();assert.equal(p.report.details[0].open,false);
});

test('wrong-run responses and authorization failures cannot replace retained progress',async()=>{
  const p=setup({responses:[value(),{...value('completed'),id:'other-run'},value('completed',3)]});await flush();
  await p.tick();assert.equal(p.nodes.state.textContent,'running');await p.tick();assert.equal(p.nodes.state.textContent,'completed');
  for(const http of [401,403,404]){
    const denied=setup({responses:[{http}]});await flush();assert.equal(denied.timers.size,0);
    assert.match(denied.nodes.feedback.textContent,/saved execution state has not been changed/);
  }
});

test('hidden initial page starts no request; terminal response is not repolled on pageshow',async()=>{
  const hidden=setup({hidden:true});await flush();assert.equal(hidden.calls.length,0);
  hidden.document.hidden=false;hidden.events.visibilitychange();await flush();assert.equal(hidden.calls.length,1);
  const done=setup({responses:[value('completed')]});await flush();done.windowEvents.pagehide();done.windowEvents.pageshow();
  done.windowEvents.online();await flush();assert.equal(done.calls.length,1);
});

test('slow requests do not overlap and timeout does not manufacture a failed task',async()=>{
  const unresolved=new Promise(()=>{});
  const p=setup({responses:[unresolved,value('completed',4)]});await flush();
  p.windowEvents.pageshow();assert.equal(p.calls.length,1);
  const timeout=[...p.timers.values()].find(t=>t.ms===12000);timeout.fn();await flush();
  assert.match(p.nodes.feedback.textContent,/saved state is unchanged/);
  assert.notEqual(p.nodes.state.textContent,'failed');await p.tick();assert.equal(p.calls.length,2);
  assert.equal(p.nodes.state.textContent,'completed');
});

test('temporary HTTP failure retries only the read endpoint',async()=>{
  const p=setup({responses:[{http:503},value('completed')]});await flush();
  assert.match(p.nodes.feedback.textContent,/connection interrupted/);
  await p.tick();assert.equal(p.nodes.state.textContent,'completed');
  assert.ok(p.calls.every(c=>c.options.method===undefined));
});
