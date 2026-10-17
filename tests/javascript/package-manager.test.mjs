import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const script=fs.readFileSync(new URL('../../app/static/assets/js/packaging.js',import.meta.url),'utf8');
const reconcile=fs.readFileSync(new URL('../../app/static/assets/js/package-reconciliation.js',import.meta.url),'utf8');
function formFixture(){
  const input=(name,value='',type='number')=>({name,value,type,tagName:'INPUT',dataset:{},events:{},addEventListener(event,run){this.events[event]=run;}});
  const size=input('dimension_unit','cm');size.tagName='SELECT';size.dataset.packageUnit='length';
  const weight=input('weight_unit','g');weight.tagName='SELECT';weight.dataset.packageUnit='weight';
  const controls=[input('name',''),size,weight,input('external_cm.length'),input('internal_cm.length'),input('tare_g'),input('max_contents_g'),input('max_contents_thickness_mm'),input('cost_pence')];
  const lengthLabel={textContent:'cm'},weightLabel={textContent:'g'};
  const form={querySelectorAll(selector){return selector==='[name]'?controls:selector==='[data-package-unit]'?[size,weight]:selector==='input[name]'?controls.filter(n=>n.tagName==='INPUT'):selector==='[data-length-unit]'?[lengthLabel]:selector==='[data-weight-unit]'?[weightLabel]:[];}};
  const context=vm.createContext({structuredClone,window:{},document:{querySelector:()=>null,createElement:()=>({})}});
  vm.runInContext(script,context);
  context.window.TLCPackaging.populatePackage(form,{name:'Fixture',external_cm:{length:'18'},internal_cm:{length:'17'},tare_g:'40',max_contents_g:'1000',max_contents_thickness_mm:'3',cost_pence:20});
  return {api:context.window.TLCPackaging,form,controls,size,weight,lengthLabel,weightLabel,get:name=>controls.find(n=>n.name===name)};
}
test('package unit controls convert dimensions and weights but never money or C5 thickness rule',()=>{
  const f=formFixture();f.size.value='mm';f.size.events.change();f.weight.value='kg';f.weight.events.change();
  assert.equal(f.get('external_cm.length').value,'180');assert.equal(f.get('internal_cm.length').value,'170');
  assert.equal(f.get('tare_g').value,'0.04');assert.equal(f.get('max_contents_g').value,'1');
  assert.equal(String(f.get('max_contents_thickness_mm').value),'3');assert.equal(String(f.get('cost_pence').value),'20');
  assert.equal(f.lengthLabel.textContent,'mm');assert.equal(f.weightLabel.textContent,'kg');
  const payload=f.api.packageData(f.form);assert.equal(payload.dimension_unit,'mm');assert.equal(payload.weight_unit,'kg');
});
test('unit conversions retain unknown values and support reverse display',()=>{
  const f=formFixture();f.get('internal_cm.length').value='';f.size.value='mm';f.size.events.change();f.size.value='cm';f.size.events.change();
  assert.equal(f.get('external_cm.length').value,'18');assert.equal(f.get('internal_cm.length').value,'');
});
test('reconciliation never inserts untrusted HTML or requests product/shipping mutations',()=>{
  assert.doesNotMatch(reconcile,/innerHTML|productUpdate|shippingPackageUpdate|carrierService|localStorage/);
  assert.match(reconcile,/textContent/);assert.match(reconcile,/retain_link/);assert.match(reconcile,/approve:true/);
  assert.match(reconcile,/unambiguous/);assert.match(reconcile,/candidate\.identity_match\|\|candidate\.compatible/);
  assert.match(reconcile,/token=null;review.hidden=true/);assert.match(reconcile,/checked.disabled=!record.remote.id/);
});
test('only explicit local confirmation submits a signed review; read/compare never confirms',()=>{
  assert.match(reconcile,/api\('\/packaging\/reconcile\/compare'/);
  assert.match(reconcile,/api\('\/packaging\/reconcile\/preview'/);
  assert.match(reconcile,/api\('\/packaging\/reconcile\/confirm',\{token,approve:true\}/);
  assert.match(reconcile,/event.currentTarget.disabled=true/);
});
