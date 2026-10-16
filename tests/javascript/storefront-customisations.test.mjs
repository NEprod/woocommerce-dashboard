import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const path = '../../shopify/extensions/tlc-customisations/src/tlc-customer-fields.js';
const source = fs.readFileSync(new URL(path,import.meta.url),'utf8');
const runtime = fs.readFileSync(new URL('../../shopify/extensions/tlc-customisations/assets/tlc-customer-fields.js',import.meta.url),'utf8');
const context = vm.createContext({console,URL});vm.runInContext(runtime,context);
const api = context.TLCStorefrontCustomisations;
const plain = value => JSON.parse(JSON.stringify(value));
const configuration = fields => ({schema_version:1,enabled:true,revision:'a'.repeat(64),fields});
const name = {id:'name',type:'text',label:'Name',required:true,max_length:30};

for(const [field,answer,valid] of [
  [name,'Ellie',true],[name,'',false],[name,'x'.repeat(31),false],
  [{...name,type:'textarea'},'Happy\nBirthday',true],
  [{id:'age',label:'Age',type:'number',minimum:1,maximum:100,whole_number:true},4,true],
  [{id:'age',label:'Age',type:'number',whole_number:true},1.5,false],
  [{id:'age',label:'Age',type:'number'},Infinity,false],
  [{id:'choice',label:'Choice',type:'dropdown',choices:[{id:'a',label:'A'}]},'a',true],
  [{id:'choice',label:'Choice',type:'radio',choices:[{id:'a',label:'A'}]},'b',false],
  [{id:'choice',label:'Choice',type:'checkbox_group',choices:[{id:'a',label:'A'}],max_selections:1},['a'],true],
  [{id:'choice',label:'Choice',type:'checkbox_group',choices:[{id:'a',label:'A'}]},['a','a'],false],
  [{id:'consent',label:'Confirm',type:'checkbox',required:true},false,false],
  [{id:'consent',label:'Confirm',type:'checkbox',required:true},true,true],
  [{id:'date',label:'Date',type:'date'},'2026-02-30',false],
  [{id:'date',label:'Date',type:'date',min_date:'2026-01-01',max_date:'2026-12-31'},'2026-10-16',true],
  [{id:'colour',label:'Colour',type:'colour',choices:[{id:'red',label:'Red',value:'#ff0000'}]},'#ff0000',true],
  [{id:'colour',label:'Colour',type:'colour',choices:[{id:'red',label:'Red',value:'#ff0000'}]},'#000000',false],
]) test(`storefront validates ${field.type} ${String(answer)}`,()=>assert.equal(!api.error(field,answer),valid));

test('same variant with different personalisations retains different cart identity',()=>{
  const config=configuration([name]);
  const first=plain(api.properties(config,100,{name:'Ellie'}));
  const second=plain(api.properties(config,100,{name:'Jack'}));
  assert.notDeepEqual(first,second);
  const order=JSON.parse(first._tlc_personalisation);
  assert.equal(order.product_id,'100');assert.equal(order.revision,config.revision);
  assert.deepEqual(order.fields,[{id:'name',type:'text',label:'Name',value:'Ellie',display:'Ellie'}]);
});
test('several fields and stable option identities survive cart and simulated order customAttributes',()=>{
  const config=configuration([name,{id:'message',type:'textarea',label:'Message'},
    {id:'wrap',type:'radio',label:'Wrap',choices:[{id:'blue',label:'Blue wrap'}]}]);
  const cart=plain(api.properties(config,100,{name:'Ellie',message:'Love from\nJack',wrap:'blue'}));
  const order={customAttributes:Object.entries(cart).map(([key,value])=>({key,value}))};
  const retained=JSON.parse(order.customAttributes.find(a=>a.key==='_tlc_personalisation').value);
  assert.equal(retained.fields[2].id,'wrap');assert.equal(retained.fields[2].value,'blue');
  assert.equal(retained.fields[2].display,'Blue wrap');assert.equal(retained.fields[1].value,'Love from\nJack');
  // Later definitions/labels do not change the historical snapshot.
  config.fields[2].label='New label';config.revision='b'.repeat(64);
  assert.equal(retained.fields[2].label,'Wrap');assert.equal(retained.revision,'a'.repeat(64));
});
test('unsafe HTML is inert in visible properties and never inserted as HTML',()=>{
  const answer='<img src=x onerror=alert(1)> /uploads/evil';
  const result=plain(api.properties(configuration([name]),100,{name:answer.slice(0,20)}));
  assert.doesNotMatch(Object.values(result)[0],/[<>]/);
  assert.match(result._tlc_personalisation,/onerror/);
  assert.doesNotMatch(source,/\.innerHTML\s*=|insertAdjacentHTML|eval\(/);
  assert.match(source,/textContent/);
});
test('disabled definitions add no properties; unsupported required files fail closed',()=>{
  assert.deepEqual(plain(api.properties({...configuration([]),enabled:false},100,{})),{});
  assert.throws(()=>api.properties(configuration([{id:'photo',label:'Photo',type:'file_upload',required:true}]),100,{}),/unsupported/);
  assert.throws(()=>api.properties(configuration([name,name]),100,{}),/unsupported/);
});
test('renderer preserves native theme variant/cart and supports formdata lifecycle without a proxy',()=>{
  assert.match(source,/form\.addEventListener\('submit',this.submit,true\)/);
  assert.match(source,/formdata/);assert.match(source,/setAttribute\('form',form.id\)/);
  assert.match(source,/disconnectedCallback\(\)/);assert.match(source,/MutationObserver/);
  assert.doesNotMatch(source,/fetch\(|XMLHttpRequest|\/cart\/change|\.value\s*=.*variant|type=['"]file/);
});
