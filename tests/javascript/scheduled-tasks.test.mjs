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
  const calls=[], destinations=[];
  const fetch=async (url,options)=>{
    calls.push([url,options]);
    return {ok:!failure,json:async()=>failure?{error:"Try again later"}:{state:options?.method==="POST"&&url.endsWith("/run")?"queued":"completed",outcome:"completed",run_id:options?.method==="POST"&&url.endsWith("/run")||active?"run-1":null,history_url:"/settings/tasks/runs/run-1",progress:{checked:3,up_to_date:2,updates:1}}};
  };
  vm.runInNewContext(source,{document:{hidden,querySelector:()=>panel},fetch,window:{location:{assign:url=>destinations.push(url)}},Date,Object,Number,Error});
  return {nodes,calls,destinations};
}

test("page load initiates no task or polling",()=>assert.equal(setup().calls.length,0));
test("Run Now posts once and follows the accepted run rather than task-wide status",async()=>{
  const {nodes,calls,destinations}=setup();await nodes.run.events.click();await nodes.run.events.click();
  assert.equal(calls.length,1);
  assert.equal(calls[0][1].headers["X-CSRFToken"],"fictional_csrf");
  assert.deepEqual(destinations,["/settings/tasks/runs/run-1"]);
  assert.equal(nodes.run.disabled,true);
});
test("hidden settings page still follows the accepted run; shared run page owns visibility polling",async()=>{
  const {nodes,calls,destinations}=setup({hidden:true});await nodes.run.events.click();
  assert.equal(calls.length,1);assert.equal(destinations.length,1);
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
