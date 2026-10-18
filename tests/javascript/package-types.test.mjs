import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const script=fs.readFileSync(new URL('../../app/static/assets/js/packaging.js',import.meta.url),'utf8');
function fixture(data={},approve=true){
  const groups=[],controls=[],requests=[];
  const node=(name,value='',types=null)=>{const wrapper=types?{dataset:{packageTypes:types},hidden:false}:null;if(wrapper)groups.push(wrapper);
    const n={name,value,type:'number',tagName:'INPUT',dataset:{},events:{},addEventListener(k,v){this.events[k]=v;},closest(){return wrapper;}};controls.push(n);return n;};
  const type=node('type','BOX');type.tagName='SELECT';const unit=node('dimension_unit','cm');unit.tagName='SELECT';unit.dataset.packageUnit='length';
  for(const group of ['external_cm','internal_cm']){
    node(group+'.length');node(group+'.width','','BOX ENVELOPE SOFT_PACK');
    node(group+'.height','',group==='external_cm'?'BOX SOFT_PACK':'BOX ENVELOPE SOFT_PACK');}
  node('external_diameter_cm','','TUBE');node('internal_diameter_cm','','TUBE');
  const transfer={events:{},addEventListener(k,v){this.events[k]=v;}},review={events:{},addEventListener(k,v){this.events[k]=v;}},warning={textContent:''},legacy={hidden:true};
  const form={querySelector(s){if(s.startsWith('[name='))return controls.find(n=>s===`[name="${n.name}"]`) || null;
    return {'[data-transfer-package-thickness]':transfer,'[data-confirm-package-thickness]':review,'[data-package-thickness-warning]':warning,'[data-package-legacy-thickness]':legacy}[s] || null;},
    querySelectorAll(s){return s==='[name]'?controls:s==='[data-package-types]'?groups:s==='[data-package-unit]'?[unit]:s==='input[name]'?controls.filter(n=>n.tagName==='INPUT'):[];}};
  const context=vm.createContext({structuredClone,confirm:text=>{requests.push(text);return approve;},window:{},document:{querySelector:()=>null}});
  vm.runInContext(script,context);const api=context.window.TLCPackaging;api.populatePackage(form,data);
  return {form,api,type,unit,transfer,review,warning,legacy,requests,get:name=>controls.find(n=>n.name===name)};
}
test('type switches hide irrelevant fields without deleting values; hidden axes are not submitted',()=>{
  const f=fixture({type:'BOX',external_cm:{length:'24',width:'18',height:'3'},internal_cm:{height:'2'},external_diameter_cm:'5'});
  f.type.value='ENVELOPE';f.type.events.change();
  assert.equal(f.get('external_cm.height').closest().hidden,true);
  assert.equal(f.api.packageData(f.form).external_cm.height,undefined);
  f.type.value='TUBE';f.type.events.change();
  assert.equal(f.get('external_cm.width').closest().hidden,true);
  assert.equal(f.get('external_diameter_cm').closest().hidden,false);
  assert.equal(f.get('internal_cm.height').closest().hidden,true);
  f.type.value='BOX';f.type.events.change();
  assert.equal(f.get('external_cm.height').value,'3');assert.equal(f.get('external_cm.width').value,'18');
  assert.equal(f.get('internal_cm.height').value,'2');assert.equal(f.get('external_diameter_cm').value,'5');
});
test('legacy thickness is shown for review but never automatically fills internal height',()=>{
  const f=fixture({type:'ENVELOPE',max_contents_thickness_mm:'3',internal_cm:{height:null}});
  assert.equal(f.get('internal_cm.height').value,'');assert.equal(f.legacy.hidden,false);
  assert.match(f.warning.textContent,/not applied automatically/);
  assert.equal(f.api.packageSaveData(f.form).confirm_envelope_thickness_change,undefined);
  assert.equal(f.requests.length,0);
});
test('explicit transfer converts 3 mm to 0.3 cm, then 3 mm in changed display units',()=>{
  const f=fixture({type:'ENVELOPE',max_contents_thickness_mm:'3',internal_cm:{height:null}});
  f.transfer.events.click();assert.equal(f.get('internal_cm.height').value,'0.3');assert.equal(f.requests.length,1);
  f.unit.value='mm';f.unit.events.change();assert.equal(f.get('internal_cm.height').value,'3');
  const payload=f.api.packageSaveData(f.form);assert.equal(payload.confirm_envelope_thickness_change,true);
  assert.equal(payload.internal_cm.height,'3');assert.equal(payload.dimension_unit,'mm');assert.equal(f.requests.length,1);
});
test('cancelled transfer keeps existing edits and leaves historical values untouched',()=>{
  const f=fixture({type:'ENVELOPE',max_contents_thickness_mm:'3',internal_cm:{height:'.5'}},false);
  f.transfer.events.click();assert.equal(f.get('internal_cm.height').value,'.5');assert.equal(f.form.tlcPackageOriginal.max_contents_thickness_mm,'3');
  f.get('internal_cm.height').value='.4';assert.throws(()=>f.api.packageSaveData(f.form),/cancelled/);
});
test('merchant can explicitly confirm current height instead of accepting historical thickness',()=>{
  const f=fixture({type:'ENVELOPE',max_contents_thickness_mm:'3',internal_cm:{height:'.5'}});
  f.review.events.click();const payload=f.api.packageSaveData(f.form);
  assert.equal(payload.internal_cm.height,'.5');assert.equal(payload.confirm_envelope_thickness_change,true);
  assert.equal(f.requests.length,1);
});
