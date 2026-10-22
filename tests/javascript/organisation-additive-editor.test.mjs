import assert from 'node:assert/strict';
import test from 'node:test';
import {editorContext,plain,source,extract} from './organisation-test-context.mjs';
import vm from 'node:vm';

test('normal registry assignments save the same top-level fields as collection JSON, only local values',()=>{
  const c=editorContext();
  c.eval("addTaxonomyChoice('attributes','term-3-5-days','attr-lead-time');addTaxonomyChoice('categories','birthday');addTaxonomyChoice('storefront_collections','spring');addTaxonomyChoice('tags','anniversary')");
  assert.deepEqual(plain(c.ctx.authored),{attributes:{'Lead Time':['3–5 Days']},categories:['Cards > Birthday'],storefront_collections:['Spring'],tags:['Anniversary']});
  assert.deepEqual(plain(c.eval('attributeProjection(attributeValues()).map(row=>row.name)')),['Material','Lead Time']);
  assert.equal(c.ctx.authored.taxonomy_additions,undefined);
});

test('existing Recipient plus compatibility Occasion combine with inherited information without migration',()=>{
  const recipients=['For Wife','For Husband','For Girlfriend','For Boyfriend','For Partner','For Couple'];
  const c=editorContext({authored:{attributes:{Recipient:recipients},sale_price:'1.50',packaging:{packed_dimensions:{height:'3',unit:'mm'}},
    taxonomy_additions:{attributes:{'attr-lead-time':['term-3-5-days']},tags:['anniversary']}}});
  const before=plain(c.ctx.authored);
  assert.deepEqual(plain(c.eval('attributeProjection(attributeValues()).map(row=>row.name)')),['Material','Recipient','Lead Time']);
  assert.deepEqual(plain(c.ctx.authored),before); // viewing never rewrites authored JSON
  c.eval("addTaxonomyChoice('attributes','term-paper','attr-material')");
  assert.deepEqual(plain(c.ctx.authored.attributes),{Recipient:recipients,'Lead Time':['3–5 Days'],Material:['Paper']});
  assert.deepEqual(plain(c.ctx.authored.taxonomy_additions),{tags:['anniversary']});
  assert.deepEqual(plain(c.ctx.authored.packaging),before.packaging);
  assert.equal(c.ctx.authored.sale_price,'1.50');
});

test('unresolved compatibility entries block a touched edit without dropping source data',()=>{
  const c=editorContext({authored:{taxonomy_additions:{attributes:{unknown:['lost-term']}}}});
  let feedback;c.ctx.showFeedback=(...args)=>feedback=args;
  const before=plain(c.ctx.authored);
  c.eval("addTaxonomyChoice('attributes','term-paper','attr-material')");
  assert.deepEqual(plain(c.ctx.authored),before);assert.match(feedback[1],/unresolved/);
});

test('informational additions pin existing legacy variable drivers, not new axes',()=>{
  const original={attributes:{Size:['A5','A4']},price:'7',weight:50,variation_modifiers:{'Size=A4':{price:9,weight:60}}};
  const c=editorContext({shared:true,simple:false,authored:original,collection:original});
  c.eval("addTaxonomyChoice('attributes','term-3-5-days','attr-lead-time')");
  assert.deepEqual(plain(c.ctx.driverNames),['Size']);
  assert.equal(c.ctx.explicitVariation,true);
  assert.deepEqual(plain(c.ctx.authored.variation_modifiers),original.variation_modifiers);
  assert.equal(c.ctx.authored.price,'7');assert.equal(c.ctx.authored.weight,50);
});

test('normal Add Category/Collection opens registry without replacement or source mutations',()=>{
  const actions=[];const c=vm.createContext({guided:{querySelector:selector=>({closest:()=>({}),focus:()=>actions.push(selector),scrollIntoView:()=>{}})}});
  vm.runInContext(extract('addOrganisationValue','clearCompatibleAdditions'),c);
  vm.runInContext('addOrganisationValue("categories");addOrganisationValue("storefront_collections")',c);
  assert.deepEqual(actions,['[data-registry-category]','[data-registry-range]']);
});

test('ordinary unregistered tags save only the local value and deduplicate inherited tags',()=>{
  const c=editorContext({collection:{attributes:{},tags:['Personalised','Pixel Art']}});
  const input={value:'Anniversary'};c.ctx.guided={querySelector:()=>input};
  c.eval('addOrganisationValue("tags")');
  assert.deepEqual(plain(c.ctx.authored),{tags:['Anniversary']});
  input.value='  personalised  ';c.eval('addOrganisationValue("tags")');
  assert.deepEqual(plain(c.ctx.authored),{tags:['Anniversary']});
});

test('inherited variation drivers stay checked and are not reported as missing local attributes',()=>{
  const names=['Size','Material'];const status={},adopt={};
  const rows=names.map(name=>{
    const input={value:name},check={},badge={};
    return {_attributeBase:{baseValues:null,driver:name==='Size'},check,badge,
      querySelector:selector=>selector==='[data-attribute-name]'?input:selector==='[data-use-variation]'?check:badge};
  });
  const c=vm.createContext({resolvedVariation:true,explicitVariation:false,driverNames:['Size'],simpleProduct:()=>false,
    guided:{querySelectorAll:selector=>selector==='[data-attribute-row]'?rows:names.map(value=>({value})),
      querySelector:selector=>selector==='[data-adopt-variation]'?adopt:status}});
  vm.runInContext(extract('renderVariationState','attributeValues'),c);
  vm.runInContext('renderVariationState()',c);
  assert.equal(rows[0].check.checked,true);assert.equal(rows[0].check.disabled,true);
  assert.equal(rows[0].badge.textContent,'Variation-driving');
  assert.equal(rows[1].check.checked,false);
  assert.doesNotMatch(status.textContent,/Unassigned drivers/);
});
