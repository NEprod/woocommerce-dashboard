import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const extract=(name,next)=>source.slice(source.indexOf(`  function ${name}(`),source.indexOf(`  function ${next}(`));
const vocabulary={attributes:[
  {key:'attr-material',value:'Material',aliases:['Substrate'],terms:[{key:'term-card',value:'Card',aliases:['Cardstock']},{key:'term-paper',value:'Paper'}]},
  {key:'attr-lead-time',value:'Lead Time',terms:[{key:'term-3-5-days',value:'3–5 Days'}]},
]};
function context(shared=false){
  let rendered;
  const ctx=vm.createContext({structuredClone,registry:vocabulary,isShared:shared,
    authored:{},boot:{collection:{attributes:{Material:['Card']}}},
    overrideEnabled:()=>Object.hasOwn(ctx.authored,'attributes'),
    attributeValues:()=>({Material:['Card']}),renderAttributes:v=>{rendered=v;},renderAdditions:()=>{},setDirty:()=>{},
    valueFor:()=>({Material:['Card']})});
  vm.runInContext(extract('registryMatch','recognition')+extract('attributeProjection','renderAttributes')+
    extract('addTaxonomyChoice','removeTaxonomyChoice')+extract('removeTaxonomyChoice','setDirty'),ctx);
  return {ctx,rendered:()=>rendered};
}
const plain=v=>JSON.parse(JSON.stringify(v));

test('registry assignment immediately rerenders normal cards, without legacy replacement',()=>{
  const {ctx,rendered}=context();
  vm.runInContext("addTaxonomyChoice('attributes','term-3-5-days','attr-lead-time')",ctx);
  assert.ok(rendered(),'assignment must update the actual card list immediately');
  assert.deepEqual(plain(ctx.authored),{taxonomy_additions:{attributes:{'attr-lead-time':['term-3-5-days']}}});
});

test('effective cards include inherited, shared additions and sparse product additions in stable order',()=>{
  const {ctx}=context();
  ctx.boot.collection.taxonomy_additions={attributes:{'attr-material':['term-paper']}};
  ctx.authored.taxonomy_additions={attributes:{'attr-lead-time':['term-3-5-days']}};
  const rows=plain(vm.runInContext('attributeProjection({Material:["Card"]})',ctx));
  assert.deepEqual(rows.map(r=>[r.name,r.values]),[['Material',['Card','Paper']],['Lead Time',['3–5 Days']]]);
  assert.equal(rows[0].terms[0].scope,'Inherited');
  assert.equal(rows[1].terms[0].scope,'Product addition');
  assert.deepEqual(rows[0].baseValues,['Card']);
  assert.equal(rows[1].baseValues,null); // effective-only rows must never become a legacy replacement
});

test('existing and inherited aliases deduplicate by stable term identity without a false local change',()=>{
  const {ctx}=context();ctx.attributeValues=()=>({Substrate:['Cardstock']});
  vm.runInContext("addTaxonomyChoice('attributes','term-card','attr-material')",ctx);
  assert.deepEqual(plain(ctx.authored),{});
  const rows=plain(vm.runInContext('attributeProjection({Substrate:["Cardstock"]})',ctx));
  assert.equal(rows.length,1);assert.deepEqual(rows[0].values,['Cardstock']);
});

test('shared collection additions show as ordinary cards and legacy child replacement suppresses parent additions',()=>{
  const {ctx}=context(true);
  ctx.authored.taxonomy_additions={attributes:{'attr-lead-time':['term-3-5-days']}};
  assert.equal(vm.runInContext('attributeProjection({Material:["Card"]})[1].terms[0].scope',ctx),'Collection addition');
  ctx.isShared=false;ctx.authored={attributes:{}};ctx.boot.collection.taxonomy_additions={attributes:{'attr-lead-time':['term-3-5-days']}};
  assert.deepEqual(plain(vm.runInContext('attributeProjection({})',ctx)),[]);
});

test('adding a Material term updates one effective card and removal preserves its inherited base',()=>{
  const {ctx}=context();
  vm.runInContext("addTaxonomyChoice('attributes','term-paper','attr-material')",ctx);
  assert.deepEqual(plain(vm.runInContext('attributeProjection(attributeValues()).map(r=>[r.name,r.values])',ctx)),[['Material',['Card','Paper']]]);
  vm.runInContext("removeTaxonomyChoice('attributes','term-paper','attr-material')",ctx);
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
