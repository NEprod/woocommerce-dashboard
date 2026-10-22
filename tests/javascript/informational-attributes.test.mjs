import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {editorContext} from './organisation-test-context.mjs';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const extract=(name,next)=>source.slice(source.indexOf(`  function ${name}(`),source.indexOf(`  function ${next}(`));
const vocabulary={attributes:[
  {key:'attr-material',value:'Material',aliases:['Substrate'],terms:[{key:'term-card',value:'Card',aliases:['Cardstock']},{key:'term-paper',value:'Paper'}]},
  {key:'attr-lead-time',value:'Lead Time',terms:[{key:'term-3-5-days',value:'3–5 Days'}]},
]};
function context(shared=false){
  const c=editorContext({shared});
  return {ctx:c.ctx,rendered:c.view};
}
const plain=v=>JSON.parse(JSON.stringify(v));

test('registry assignment immediately rerenders normal cards, without legacy replacement',()=>{
  const {ctx,rendered}=context();
  vm.runInContext("addTaxonomyChoice('attributes','term-3-5-days','attr-lead-time')",ctx);
  assert.ok(rendered(),'assignment must update the actual card list immediately');
  assert.deepEqual(plain(ctx.authored),{attributes:{'Lead Time':['3–5 Days']}});
});

test('effective cards include inherited, shared additions and sparse product additions in stable order',()=>{
  const {ctx}=context();
  ctx.boot.collection.taxonomy_additions={attributes:{'attr-material':['term-paper']}};
  ctx.authored.taxonomy_additions={attributes:{'attr-lead-time':['term-3-5-days']}};
  const rows=plain(vm.runInContext('attributeProjection({Material:["Card"]})',ctx));
  assert.deepEqual(rows.map(r=>[r.name,r.values]),[['Material',['Card','Paper']],['Lead Time',['3–5 Days']]]);
  assert.equal(rows[0].terms[0].scope,'Inherited');
  assert.equal(rows[1].terms[0].scope,'Product addition');
  assert.equal(rows[0].baseValues,null); // inherited values are never authored in the child
  assert.equal(rows[1].baseValues,null); // effective-only rows must never become a legacy replacement
});

test('existing and inherited aliases deduplicate by stable term identity without a false local change',()=>{
  const {ctx}=context();ctx.boot.collection.attributes={Substrate:['Cardstock']};
  vm.runInContext("addTaxonomyChoice('attributes','term-card','attr-material')",ctx);
  assert.deepEqual(plain(ctx.authored),{});
  const rows=plain(vm.runInContext('attributeProjection({Substrate:["Cardstock"]})',ctx));
  assert.equal(rows.length,1);assert.deepEqual(rows[0].values,['Cardstock']);
});

test('shared additions remain visible alongside a child top-level attributes object',()=>{
  const {ctx}=context(true);
  ctx.authored.taxonomy_additions={attributes:{'attr-lead-time':['term-3-5-days']}};
  assert.equal(vm.runInContext('attributeProjection({Material:["Card"]})[1].terms[0].scope',ctx),'Collection addition');
  ctx.isShared=false;ctx.authored={attributes:{}};ctx.boot.collection.taxonomy_additions={attributes:{'attr-lead-time':['term-3-5-days']}};
  assert.deepEqual(plain(vm.runInContext('attributeProjection({}).map(row=>row.name)',ctx)),['Material','Lead Time']);
});

test('adding a Material term updates one effective card and removal preserves its inherited base',()=>{
  const {ctx}=context();
  vm.runInContext("addTaxonomyChoice('attributes','term-paper','attr-material')",ctx);
  assert.deepEqual(plain(vm.runInContext('attributeProjection(attributeValues()).map(r=>[r.name,r.values])',ctx)),[['Material',['Card','Paper']]]);
  vm.runInContext("removeInformationTerm({attribute:'attr-material',name:'Material',key:'term-paper',value:'Paper',raw:true})",ctx);
  assert.deepEqual(plain(ctx.authored),{});
  assert.deepEqual(plain(vm.runInContext('attributeProjection(attributeValues()).map(r=>[r.name,r.values])',ctx)),[['Material',['Card']]]);
});

test('normal card serialization never copies projected terms or new groups into the legacy attributes object',()=>{
  const row=(name,values,base)=>({_attributeBase:{baseValues:base},querySelectorAll:()=>[
    {value:name},{value:values.join(', '),dataset:{originalText:values.join(', '),terms:JSON.stringify(values)}}]});
  const ctx=vm.createContext({guided:{querySelectorAll:()=>[
    row('Material',['Card','Paper'],['Card']),row('Lead Time',['3–5 Days'],null)
  ]}});
  vm.runInContext(extract('attributeValues','renderModifiers'),ctx);
  assert.deepEqual(plain(vm.runInContext('attributeValues()',ctx)),{Material:['Card']});
});

function replacementContext(approve=true){
  let view={Material:['Unsaved replacement term']};let handler;let prompts=0;
  const toggle={checked:true,matches:s=>s==='[data-override-toggle]' || s==='[data-override-toggle="attributes"]',dataset:{overrideToggle:'attributes'}};
  const ctx=vm.createContext({structuredClone,isShared:false,attributeReplacementActive:true,attributeReplacementDraft:null,
    authored:{attributes:{Material:['Saved replacement term']},taxonomy_additions:{attributes:{'attr-lead-time':['term-3-5-days']}}},
    boot:{collection:{attributes:{Material:['Card'],Personalisation:['Personalised']}}},
    confirmAttributeReplacement:async()=>{prompts++;return approve;},
    attributeValues:()=>structuredClone(view),renderAttributes:v=>{view=structuredClone(v);},
    simpleProduct:()=>false,resolvedVariation:true,driverNames:['Material'],
    setControlState:()=>{},setDirty:()=>{},updateFinishedPreview:()=>{},queueTitlePreview:()=>{},
    guided:{addEventListener:(_,fn)=>handler=fn}});
  const helper=source.slice(source.indexOf('  async function changeAttributeReplacement('),source.indexOf('  function attributeProjection('));
  const start=source.indexOf('  guided.addEventListener("change",');
  const end=source.indexOf('  guided.addEventListener("click",',start);
  vm.runInContext(helper+source.slice(start,end),ctx);
  return {ctx,toggle,change:checked=>{toggle.checked=checked;return handler({target:toggle});},view:()=>view,prompts:()=>prompts};
}

test('variation override to inheritance and back preserves unsaved option drafts and information',async()=>{
  const c=replacementContext();const before=plain(c.ctx.authored);
  await c.change(false);assert.deepEqual(c.view(),{}); // the projection restores inherited options separately
  await c.change(true);assert.deepEqual(c.view(),{Material:['Unsaved replacement term']});
  assert.deepEqual(plain(c.ctx.authored),before);
  assert.equal(c.prompts(),2);
});

test('cancelling a replacement mode switch leaves the checkbox and edited cards unchanged',async()=>{
  const c=replacementContext(false);await c.change(false);
  assert.equal(c.toggle.checked,true);
  assert.deepEqual(c.view(),{Material:['Unsaved replacement term']});
});

test('pending mode approval cannot change replacement ownership or serialize inherited values',async()=>{
  const c=replacementContext();let answer;
  c.ctx.confirmAttributeReplacement=()=>new Promise(resolve=>{answer=resolve;});
  const before=plain(c.ctx.authored);const pending=c.change(false);
  assert.equal(c.toggle.checked,true);
  assert.deepEqual(c.view(),{Material:['Unsaved replacement term']});
  assert.deepEqual(plain(c.ctx.authored),before);
  answer(false);await pending;assert.equal(c.toggle.checked,true);
});

test('removing a redundant local identity keeps its inherited term',()=>{
  const {ctx}=context();ctx.authored.taxonomy_additions={attributes:{'attr-material':['term-card']}};
  vm.runInContext("removeTaxonomyChoice('attributes','term-card','attr-material')",ctx);
  assert.deepEqual(plain(ctx.authored),{});
  assert.deepEqual(plain(vm.runInContext('attributeProjection(attributeValues()).map(r=>r.values)',ctx)),[['Card']]);
});

test('in-page replacement confirmation requires an explicit answer and cleans up its handlers',async()=>{
  const element=()=>({handlers:new Map(),addEventListener(name,fn){this.handlers.set(name,fn);},removeEventListener(name){this.handlers.delete(name);}});
  const yes=element(),no=element(),message={};
  const dialog={...element(),open:false,showModal(){this.open=true;},close(){this.open=false;},
    querySelector:s=>s==='[data-attribute-replacement-message]'?message:s==='[data-attribute-replacement-approve]'?yes:no};
  const ctx=vm.createContext({guided:{querySelector:()=>dialog}});
  vm.runInContext(source.slice(source.indexOf('  function confirmAttributeReplacement('),source.indexOf('  async function changeAttributeReplacement(')),ctx);
  for (const approve of [false,true]) {
    const answer=vm.runInContext('confirmAttributeReplacement("Explicit replacement warning")',ctx);
    assert.equal(dialog.open,true);assert.equal(message.textContent,'Explicit replacement warning');
    (approve?yes:no).handlers.get('click')({preventDefault(){}});
    assert.equal(await answer,approve);assert.equal(dialog.open,false);
    assert.equal(yes.handlers.size+no.handlers.size+dialog.handlers.size,0);
  }
});
