import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source=fs.readFileSync(new URL("../../app/static/assets/js/scheduled-tasks.js",import.meta.url),"utf8");
function setup({active=false,hidden=false,failure=false}={}) {
  const names=["feedback","run","state","outcome","duration","progress","refresh","settings"];
  const nodes=Object.fromEntries(names.map(name=>[name,{textContent:"",dataset:{},events:{},addEventListener(event,callback){this.events[event]=callback;}}]));
  nodes.run.dataset.runUrl="/settings/tasks/shopify_products/run";
  nodes.settings.getAttribute=()=>"/settings/tasks/shopify_products";
  nodes.settings.elements={enabled:{checked:true},interval:{value:"30"}};
  const panel={dataset:{statusUrl:"/settings/tasks/status",csrf:"fictional_csrf"},querySelector(selector){return nodes[selector.replace("[data-task-","").replace("]","")];},querySelectorAll(){return [];}};
  const calls=[];
  const fetch=async (url,options)=>{
    calls.push([url,options]);
    return {ok:!failure,json:async()=>failure?{error:"Try again later"}:{state:options?.method==="POST"&&url.endsWith("/run")?"queued":"completed",outcome:"completed",run_id:options?.method==="POST"&&url.endsWith("/run")||active?"run-1":null,progress:{checked:3,up_to_date:2,updates:1}}};
  };
  vm.runInNewContext(source,{document:{hidden,querySelector:()=>panel},fetch,setTimeout:callback=>callback(),Date,Object,Number,Error});
  return {nodes,calls};
}

test("page load initiates no task or polling",()=>assert.equal(setup().calls.length,0));
test("Run Now queues one CSRF-protected request then reads completion",async()=>{
  const {nodes,calls}=setup();await nodes.run.events.click();
  assert.equal(calls.filter(([,o])=>o?.method==="POST").length,1);
  assert.equal(calls[0][1].headers["X-CSRFToken"],"fictional_csrf");
  assert.equal(calls.length,2);assert.equal(nodes.state.textContent,"completed");
  assert.match(nodes.progress.textContent,/checked: 3/);
});
test("polling is bounded and background execution survives hidden tab",async()=>{
  const visible=setup({active:true});await visible.nodes.run.events.click();assert.equal(visible.calls.length,13);
  const hidden=setup({hidden:true});await hidden.nodes.run.events.click();assert.equal(hidden.calls.length,1);
  assert.match(hidden.nodes.feedback.textContent,/Background work continues/);
});
test("saving interval does not start a task",async()=>{
  const {nodes,calls}=setup();await nodes.settings.events.submit({preventDefault(){},currentTarget:nodes.settings});
  assert.deepEqual(JSON.parse(calls[0][1].body),{enabled:true,interval:30});
  assert.equal(calls.length,1);
});
test("unavailable acceptance is visible without resubmitting",async()=>{
  const {nodes,calls}=setup({failure:true});await nodes.run.events.click();
  assert.equal(calls.length,1);assert.equal(nodes.feedback.textContent,"Try again later");assert.equal(nodes.run.disabled,false);
});
