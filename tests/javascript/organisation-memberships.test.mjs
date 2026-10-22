import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const extract=(name,next)=>source.slice(source.indexOf(`  function ${name}(`),source.indexOf(`  function ${next}(`));
const plain=value=>JSON.parse(JSON.stringify(value));
const registry={
  categories:[{key:'cards',value:'Cards',aliases:['Greeting cards']},{key:'gifts',value:'Gifts'},{key:'birthday',value:'Cards > Birthday'}],
  storefront_collections:[{key:'pixel',value:'Pixel',aliases:['Pixel Art']},{key:'spring',value:'Spring'}],tags:[]
};
function projectionContext(){
  const lists={categories:['Gifts'],storefront_collections:['Pixel']};
  const enabled=new Set(['categories']);
  const context=vm.createContext({registry,structuredClone,isShared:false,
    authored:{categories:['Gifts']},boot:{collection:{categories:['Greeting cards'],storefront_collections:['Pixel Art']}},
    overrideEnabled:kind=>enabled.has(kind),listValues:kind=>lists[kind] || [],
    renderAdditions:()=>{},setDirty:()=>{}});
  vm.runInContext(extract('registryMatch','recognition')+extract('assignmentProjection','addTaxonomyChoice')+
    extract('addTaxonomyChoice','removeTaxonomyChoice')+extract('removeTaxonomyChoice','setDirty'),context);
  return {context,lists,enabled,rows:kind=>plain(vm.runInContext(`assignmentProjection('${kind}')`,context))};
}

test('selecting an effective inherited category or collection does not author another assignment',()=>{
  const context=vm.createContext({authored:{},structuredClone,
    assignmentProjection:()=>[{key:'inherited'}],renderAdditions:()=>{},setDirty:()=>{}});
  vm.runInContext(extract('addTaxonomyChoice','removeTaxonomyChoice'),context);
  vm.runInContext("addTaxonomyChoice('categories','inherited');addTaxonomyChoice('storefront_collections','inherited')",context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.authored)),{});
});

test('effective projection includes inherited categories alongside old authored category additions',()=>{
  const c=projectionContext();const before=plain(c.context.authored);
  assert.deepEqual(c.rows('categories').map(row=>[row.key,row.scope]),[['cards','Inherited'],['gifts','Product addition']]);
  assert.deepEqual(plain(c.context.authored),before);
});

test('sparse categories and collections coexist with inherited assignments immediately',()=>{
  const c=projectionContext();
  vm.runInContext("addTaxonomyChoice('categories','birthday');addTaxonomyChoice('storefront_collections','spring')",c.context);
  assert.deepEqual(c.rows('categories').map(row=>row.key),['cards','gifts','birthday']);
  assert.deepEqual(c.rows('storefront_collections').map(row=>[row.key,row.scope]),[['pixel','Inherited'],['spring','Product addition']]);
  assert.deepEqual(plain(c.context.authored),{categories:['Gifts'],taxonomy_additions:{categories:['birthday'],storefront_collections:['spring']}});
  vm.runInContext("removeTaxonomyChoice('storefront_collections','spring')",c.context);
  assert.deepEqual(c.rows('storefront_collections').map(row=>row.key),['pixel']);
  assert.equal(c.context.authored.storefront_collections,undefined);
});

test('alias and stable identity deduplication prevent adding inherited assignments twice',()=>{
  const c=projectionContext();
  vm.runInContext("addTaxonomyChoice('categories','cards');addTaxonomyChoice('storefront_collections','pixel')",c.context);
  assert.deepEqual(plain(c.context.authored),{categories:['Gifts']});
});

test('redundant historic local additions can be removed without removing inherited identities',()=>{
  const c=projectionContext();c.context.authored.taxonomy_additions={categories:['cards'],storefront_collections:['pixel']};
  assert.deepEqual(c.rows('categories')[0].additions,['cards']);
  vm.runInContext("removeTaxonomyChoice('categories','cards');removeTaxonomyChoice('storefront_collections','pixel')",c.context);
  assert.deepEqual(c.rows('categories').map(row=>row.key),['cards','gifts']);
  assert.deepEqual(c.rows('storefront_collections').map(row=>row.key),['pixel']);
  assert.deepEqual(plain(c.context.authored),{categories:['Gifts']});
});

test('legacy collection replacement and explicit clear suppress both inherited layers, never local additions',()=>{
  const c=projectionContext();c.context.boot.collection.taxonomy_additions={storefront_collections:['spring']};
  c.enabled.add('storefront_collections');c.lists.storefront_collections=[];
  assert.deepEqual(c.rows('storefront_collections'),[]);
  vm.runInContext("addTaxonomyChoice('storefront_collections','spring')",c.context);
  assert.deepEqual(c.rows('storefront_collections').map(row=>[row.key,row.scope]),[['spring','Product addition']]);
  c.enabled.delete('storefront_collections');
  assert.deepEqual(c.rows('storefront_collections').map(row=>[row.key,row.scope]),[['pixel','Inherited'],['spring','Inherited']]);
});

test('shared and Single Variable editing projects authored values and collection additions without inheritance',()=>{
  const c=projectionContext();c.context.isShared=true;
  c.context.authored.taxonomy_additions={storefront_collections:['spring']};
  assert.deepEqual(c.rows('categories').map(row=>[row.key,row.scope]),[['gifts','Collection']]);
  assert.deepEqual(c.rows('storefront_collections').map(row=>[row.key,row.scope]),[['pixel','Collection'],['spring','Collection addition']]);
});

test('rendered effective memberships label provenance and disable already assigned picker options',()=>{
  const c=projectionContext();
  const element=()=>({dataset:{},children:[],append(...rows){this.children.push(...rows);},replaceChildren(){this.children=[];},
    setAttribute(name,value){this[name]=value;},addEventListener(name,fn){this[name]=fn;}});
  const organisation={...element(),dataset:{taxonomyAdditions:'organisation'}};
  const collections={...element(),dataset:{taxonomyAdditions:'collections'}};
  const categories={options:registry.categories.map(row=>({value:row.key}))};
  const ranges={options:registry.storefront_collections.map(row=>({value:row.key}))};
  c.context.document={createElement:element};
  c.context.guided={querySelectorAll:()=>[organisation,collections],querySelector:selector=>selector==='[data-registry-category]'?categories:ranges};
  vm.runInContext(extract('renderAdditions','assignmentProjection'),c.context);
  vm.runInContext("addTaxonomyChoice('storefront_collections','spring')",c.context);
  assert.match(organisation.children[0].textContent,/Inherited.*Greeting cards/);
  assert.match(collections.children[1].textContent,/Product addition.*Spring/);
  assert.equal(collections.children[0].children.length,0);
  assert.equal(categories.options[0].disabled,true);
  assert.match(categories.options[0].textContent,/Already assigned \(Inherited\)/);
  collections.children[1].children[0].click();
  assert.equal(collections.children.length,1);
  assert.equal(ranges.options[1].disabled,false);
});
