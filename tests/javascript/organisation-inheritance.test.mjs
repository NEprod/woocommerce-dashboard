import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {editorContext,plain} from './organisation-test-context.mjs';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const extract=(name,next)=>source.slice(source.indexOf(`  function ${name}(`),source.indexOf(`  function ${next}(`));

test('Product Type options contain only parent categories and preserve an unresolved saved key',()=>{
  const select={value:'',options:[],replaceChildren(option){this.options=[option];},add(option){this.options.push(option);}};
  const status={};
  const context=vm.createContext({guided:{querySelector:s=>s==='[data-parent-product-type]'?select:status},
    registry:{categories:[{key:'cards',name:'Cards',parent:null},{key:'birthday',name:'Birthday',parent:'cards'},
      {key:'old',name:'Retired',parent:null,state:'deprecated'}]},
    Option:function(text,value){this.text=text;this.value=value;},isShared:true,authored:{},boot:{},valueFor:()=>''});
  vm.runInContext(extract('renderProductType','renderOrganisation'),context);
  vm.runInContext('renderProductType()',context);
  assert.deepEqual(select.options.map(r=>r.value),['','cards']);
  context.valueFor=()=> 'deleted-parent';
  vm.runInContext('renderProductType()',context);
  assert.equal(select.value,'deleted-parent');
  assert.match(select.options.at(-1).text,/Unavailable parent/);
  vm.runInContext("renderProductType('another-missing-key')",context);
  assert.equal(select.value,'another-missing-key');  // Advanced JSON never loses its saved identity
});

test('normal information writes sparse top-level fields, deduplicates and preserves unrelated source',()=>{
  const original={description:'Keep',packaging:{selected_package_id:'package-1'}};
  const c=editorContext({authored:original});
  c.eval("addTaxonomyChoice('attributes','term-3-5-days','attr-lead-time');addTaxonomyChoice('attributes','term-3-5-days','attr-lead-time');addTaxonomyChoice('storefront_collections','spring')");
  assert.deepEqual(plain(c.ctx.authored),{...original,attributes:{'Lead Time':['3–5 Days']},storefront_collections:['Spring']});
  c.eval("removeInformationTerm({attribute:'attr-lead-time',name:'Lead Time',key:'term-3-5-days',value:'3–5 Days',raw:true})");
  assert.equal(c.ctx.authored.attributes,undefined);
  assert.deepEqual(plain(c.ctx.authored.packaging),original.packaging);
  assert.equal(c.ctx.authored.description,original.description);
});

test('normal product collection assignment does not enable or overwrite legacy replacement',()=>{
  let click,received;
  const context=vm.createContext({isShared:false,registry:{storefront_collections:[{key:'range-spring',value:'Spring'}]},
    guided:{querySelector:s=>s==='[data-assign-range]'?{addEventListener:(_,fn)=>click=fn}:{value:'range-spring'}},
    addTaxonomyChoice:(...args)=>received=args,
    overrideEnabled:()=>{throw Error('must not require legacy replacement');}});
  const start=source.indexOf('  guided.querySelector("[data-assign-range]").addEventListener');
  const end=source.indexOf('  guided.querySelector("[data-registry-attribute]")',start);
  vm.runInContext(source.slice(start,end),context);click();
  assert.deepEqual(received,['storefront_collections','range-spring']);
});
