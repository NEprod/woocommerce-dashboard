import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const script=fs.readFileSync(new URL('../../app/static/assets/js/packaging.js',import.meta.url),'utf8');
const gid='gid://shopify/ShippingPackageV2/116237992319';
function fixture({saved=null,failure=false}={}){
  const control=(value='')=>({value,dataset:{},events:{},addEventListener(k,v){this.events[k]=v;}});
  const id=control(saved || '');id.name='shopify_package_id';id.type='text';id.tagName='INPUT';
  const name=control('Unsaved package name');name.name='name';name.type='text';name.tagName='INPUT';
  const check={checked:false},feedback={textContent:''},idFeedback={textContent:''},status={textContent:''};
  const submit=control(),set=control(),unlink=control();
  const form={dataset:{saveUrl:'/packaging/local/edit'},events:{},
    addEventListener(k,v){this.events[k]=v;},
    querySelectorAll(s){return s==='[name]'?[name,id]:[];},
    querySelector(s){return s==='button:not([type])'?submit:s==='[data-package-feedback]'?feedback:null;}};
  const idForm={dataset:{url:'/packaging/local/shopify-id'},
    querySelectorAll(){return [set,unlink];},
    querySelector(s){return {'[name="shopify_package_id"]':id,'[data-confirm-unlink]':check,
      '[data-package-id-feedback]':idFeedback,'[data-package-id-status]':status,'[data-save-package-id]':set,'[data-unlink-package]':unlink}[s] || null;}};
  const requests=[],navigations=[];
  const context=vm.createContext({structuredClone,window:{location:{assign:url=>navigations.push(url)}},
    document:{querySelector:s=>s==='[data-package-form]'?form:s==='[data-package-id-form]'?idForm:s==='meta[name="csrf-token"]'?{content:'fixture-csrf'}:null,
      getElementById:key=>({textContent:JSON.stringify(key==='package-bootstrap'?{id:'local',name:'Original',version:2}:{id:saved,expected:saved?[saved]:[]})})},
    fetch:async(url,options)=>{const body=JSON.parse(options.body);requests.push({url,body,options});return {ok:!failure,json:async()=>failure?{error:'Synthetic validation failure'}:
      url.endsWith('shopify-id')?{package_link:{id:body.unlink?null:body.id,expected:body.unlink?[]:[body.id],status:'Entered manually — not remotely verified'},package_version:3}:{url:'/packaging/local/edit'}};}});
  vm.runInContext(script,context);
  return {id,name,check,feedback,idFeedback,set,unlink,form,requests,navigations};
}
test('normal Save locally submits genuine V2 ID and measurements together',async()=>{
  const f=fixture();f.name.value='Unsaved package name';f.id.value=gid;
  await f.form.events.submit({preventDefault(){}});
  assert.equal(f.requests.length,1);assert.equal(f.requests[0].url,'/packaging/local/edit');
  assert.deepEqual(f.requests[0].body.shopify_link,{id:gid,expected:[],unlink:false,confirm:false});
  assert.equal(f.requests[0].body.name,'Unsaved package name');
  assert.equal(f.requests[0].body.shopify_package_id,undefined);
  assert.equal(f.requests[0].options.headers['X-CSRFToken'],'fixture-csrf');
  assert.equal(f.navigations.length,1);
});
test('reopening/population never blanks the stored ID',()=>{
  const f=fixture({saved:gid});assert.equal(f.id.value,gid);
});
test('ID-only save preserves unsaved edits and refreshes subsequent save version',async()=>{
  const f=fixture();f.name.value='Keep unsaved name';f.id.value=gid;
  await f.set.events.click();assert.equal(f.name.value,'Keep unsaved name');
  assert.equal(f.requests[0].body.id,gid);assert.equal(f.form.dataset.version,'3');
  assert.equal(f.navigations.length,0);
  await f.form.events.submit({preventDefault(){}});
  assert.equal(f.requests[1].body.version,3);
  assert.deepEqual(f.requests[1].body.shopify_link.expected,[gid]);
});
test('validation errors retain the ID and edits without navigating',async()=>{
  const f=fixture({failure:true});f.id.value=gid;f.name.value='Keep unsaved name';
  await f.form.events.submit({preventDefault(){}});
  assert.equal(f.id.value,gid);assert.equal(f.name.value,'Keep unsaved name');
  assert.equal(f.feedback.textContent,'Synthetic validation failure');assert.equal(f.navigations.length,0);
});
test('unlink still requires explicit confirmation and keeps expected-ID guard',async()=>{
  const f=fixture({saved:gid});await f.unlink.events.click();assert.equal(f.requests.length,0);
  f.check.checked=true;await f.unlink.events.click();
  assert.deepEqual(f.requests[0].body,{id:gid,expected:[gid],unlink:true,confirm:true});
  assert.equal(f.id.value,'');assert.equal(f.check.checked,false);
});
