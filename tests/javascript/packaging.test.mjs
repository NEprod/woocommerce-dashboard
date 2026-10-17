import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Node {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.events={};this.value='';this.options=[];this.attributes={};}
  append(...nodes){this.children.push(...nodes);} appendChild(n){this.append(n);}
  replaceChildren(...nodes){this.children=[...nodes];} add(n){this.options.push(n);}
  addEventListener(event,callback){(this.events[event]||=[]).push(callback);}
  setAttribute(k,v){this.attributes[k]=v;} querySelector(){return null;} querySelectorAll(){return [];}
}
const script=fs.readFileSync(new URL('../../app/static/assets/js/packaging.js',import.meta.url),'utf8');
function fixture(){
  const calls=[];const context=vm.createContext({structuredClone,setTimeout,clearTimeout,Option:function(label,value){this.label=label;this.value=value;},
    window:{},document:{querySelector:s=>s==='meta[name="csrf-token"]'?{content:'fixture-csrf'}:null,createElement:tag=>new Node(tag)},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({packages:[]})};}});
  vm.runInContext(script,context);return {...context.window.TLCPackaging,calls};
}
const plain=v=>JSON.parse(JSON.stringify(v));
test('sparse child retains inherited false and measures without copying parent fields',()=>{
  const api=fixture();const own={fragile:false,packed_dimensions_cm:{height:'0.3'}};
  const editor=new api.Editor(new Node('div'),{value:own,shared:{fragile:true,rollable:true,packed_dimensions_cm:{length:'42',width:'29.7'}}});
  assert.deepEqual(plain(editor.get()),own);
});
test('explicit false and package unassignment survive roundtrip',()=>{
  const api=fixture();const editor=new api.Editor(new Node('div'),{value:{fragile:false,selected_package_id:null},shared:{selected_package_id:'parent-id'}});
  assert.deepEqual(plain(editor.get()),{fragile:false,selected_package_id:null});
  assert.match(editor.assignmentStatus.textContent,/Explicitly no package/);
});
test('editing only packed height creates one sparse field, never a price or SKU',()=>{
  const api=fixture();let changes=0;const editor=new api.Editor(new Node('div'),{shared:{packed_dimensions_cm:{length:'42',height:'0.2'}},onChange:()=>changes++});
  const row=editor.controls.find(r=>r.path==='packed_dimensions_cm.height');row.inherit.value='own';row.input.value='0.4';
  row.inherit.events.change[0]();assert.equal(changes,1);
  assert.deepEqual(plain(editor.get()),{packed_dimensions_cm:{height:'0.4'}});
});
test('reset field restores inheritance and keeps independent authoring',()=>{
  const api=fixture();const editor=new api.Editor(new Node('div'),{value:{fragile:false,override_reason:'Retain me'}});
  editor.controls.find(r=>r.path==='fragile').inherit.value='inherit';
  assert.deepEqual(plain(editor.get()),{override_reason:'Retain me'});
});
test('render only requests local choices, not Shopify or product writes',async()=>{
  const api=fixture();new api.Editor(new Node('div'),{});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(api.calls.length,1);assert.equal(api.calls[0].url,'/packaging/choices');assert.equal(api.calls[0].options.method,'GET');
});
test('inline creation is isolated and CSRF protected in one shared component',()=>{
  assert.match(script,/document\.body\.append\(dialog\)/);assert.match(script,/'X-CSRFToken':csrf\(\)/);
  assert.match(script,/data-package-cancel/);assert.match(script,/await onSaved\(saved\)/);
  assert.match(script,/inlinePackage\(async\(\)=>\{await refresh\(\);this.queueRecommendation/);
  assert.doesNotMatch(script,/innerHTML|shopify\.com|orders\/.*fulfill/);
});
test('finished dimensions retain units and whole-object inheritance independently',()=>{
  const api=fixture();const own={length:'6',width:'6',unit:'in',label:'folded'};
  const editor=new api.FinishedEditor(new Node('div'),{value:own,shared:{length:'42',unit:'cm'}});
  assert.deepEqual(plain(editor.get()),own);assert.match(editor.summary.textContent,/6 × 6 in/);
  editor.source.value='inherit';assert.equal(editor.get(),undefined);
});
test('no package is a valid default and recommendation has no automatic acceptance',()=>{
  const api=fixture();const editor=new api.Editor(new Node('div'),{});assert.equal(editor.get(),undefined);
  assert.match(editor.assignmentStatus.textContent,/No package selected/);
  assert.doesNotMatch(script,/simulator|shipping\/rates|multi.parcel/);
});
