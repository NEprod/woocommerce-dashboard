import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const extract=(name,next)=>source.slice(source.indexOf(`  function ${name}(`),source.indexOf(`  function ${next}(`));

test('Product Type options contain only parent categories and preserve an unresolved saved key',()=>{
  const select={value:'',options:[],replaceChildren(option){this.options=[option];},add(option){this.options.push(option);}};
  const status={};
  const context=vm.createContext({guided:{querySelector:s=>s==='[data-parent-product-type]'?select:status},
    registry:{categories:[{key:'cards',name:'Cards',parent:null},{key:'birthday',name:'Birthday',parent:'cards'},
      {key:'old',name:'Retired',parent:null,state:'deprecated'}]},
    Option:function(text,value){this.text=text;this.value=value;},isShared:true,authored:{},boot:{},valueFor:()=>''});
  vm.runInContext(extract('renderProductType','renderAdditions'),context);
  vm.runInContext('renderProductType()',context);
  assert.deepEqual(select.options.map(r=>r.value),['','cards']);
  context.valueFor=()=> 'deleted-parent';
  vm.runInContext('renderProductType()',context);
  assert.equal(select.value,'deleted-parent');
  assert.match(select.options.at(-1).text,/Unavailable parent/);
  vm.runInContext("renderProductType('another-missing-key')",context);
  assert.equal(select.value,'another-missing-key');  // Advanced JSON never loses its saved identity
});

test('term and collection additions store only stable local keys, deduplicate and return to pristine source',()=>{
  let changed=0;
  const context=vm.createContext({authored:{description:'Keep',packaging:{selected_package_id:'package-1'}},
    structuredClone,renderAdditions:()=>{},setDirty:()=>changed++});
  vm.runInContext(extract('addTaxonomyChoice','removeTaxonomyChoice')+extract('removeTaxonomyChoice','setDirty'),context);
  vm.runInContext("addTaxonomyChoice('attributes','term-mum','attr-recipient');addTaxonomyChoice('attributes','term-mum','attr-recipient');addTaxonomyChoice('storefront_collections','range-spring')",context);
  const value=JSON.parse(JSON.stringify(context.authored));
  assert.deepEqual(value.taxonomy_additions,{attributes:{'attr-recipient':['term-mum']},storefront_collections:['range-spring']});
  assert.equal(value.attributes,undefined);
  assert.equal(value.storefront_collections,undefined);
  vm.runInContext("removeTaxonomyChoice('attributes','term-mum','attr-recipient');removeTaxonomyChoice('storefront_collections','range-spring')",context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.authored)),{description:'Keep',packaging:{selected_package_id:'package-1'}});
  assert.equal(changed,5);
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
