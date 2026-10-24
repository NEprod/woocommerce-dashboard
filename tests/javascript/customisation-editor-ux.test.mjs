import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// DOM-backed controller tests supplement the real Safari/Chromium CUA scenario.
class Node {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.events={};this.value='';this.checked=false;this.readOnly=false;this.options=[];this.className='';this.validity={badInput:false};
    if(['select','textarea'].includes(tag))Object.defineProperty(this,'type',{get:()=>tag});}
  append(...nodes){this.children.push(...nodes);} prepend(node){this.children.unshift(node);}
  replaceChildren(...nodes){this.children=[...nodes];} add(node){this.options.push(node);}
  addEventListener(event,callback){(this.events[event]||=[]).push(callback);}
  emit(event){for(const callback of this.events[event]||[])callback({target:this,preventDefault(){},stopPropagation(){}});}
  setAttribute(){} focus(){this.focused=true;} showModal(){this.open=true;} close(){this.open=false;this.emit('close');}
  querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...(c.querySelectorAll?.('*')||[])]);return selector==='input:checked'?all.filter(c=>c.tagName==='INPUT'&&c.checked):all;}
}
const source=fs.readFileSync(new URL('../../app/static/assets/js/customisation-designer.js',import.meta.url),'utf8');
const context=vm.createContext({structuredClone,document:{activeElement:null,createElement:tag=>new Node(tag)},Option:function(label,value){this.label=label;this.value=value;}});
vm.runInContext(source,context);const api=context.TLCCustomisations;
const plain=value=>JSON.parse(JSON.stringify(value));
const name={type:'text',label:'Name',required:true,enabled:true,max_length:30,help_text:'Check spelling',placeholder:'Your name',default:'Example',future:{opaque:[]}};
function fixture({field=name,shared={},own,base={}}={}) {
  const nodes=new Map();const root={querySelector:selector=>{if(!nodes.has(selector)){const node=new Node('div');if(selector==='[data-custom-proof]')node.options.push(new Node('option'));nodes.set(selector,node);}return nodes.get(selector);}};
  const designer=Object.create(api.Designer.prototype);
  Object.assign(designer,{root,shared:structuredClone(shared),base:structuredClone(base),value:structuredClone(own??{fields:{name_on_card:field}}),fieldDialog:new Node('dialog'),previewDialog:new Node('dialog')});
  let changed=0;designer.changed=()=>changed++;designer.edit('name_on_card');
  return {designer,nodes,changes:()=>changed};
}
function find(node,tag,text){return node.querySelectorAll('*').find(n=>n.tagName===tag&&(!text||n.textContent===text));}

test('existing modal loads identity, label, type, both booleans, help, constraints and default',()=>{
  const {designer:d}=fixture();assert.equal(d.inputs.id.value,'name_on_card');assert.equal(d.inputs.id.readOnly,true);
  assert.equal(d.inputs.label.value,'Name');assert.equal(d.inputs.type.value,'text');assert.equal(d.inputs.required.checked,true);assert.equal(d.inputs.enabled.checked,true);
  assert.equal(d.inputs.help_text.value,'Check spelling');assert.equal(d.inputs.max_length.value,30);assert.equal(d.defaultState.value,'Example');
  assert.equal(d.inputs.required.className,'customisation-boolean');assert.notEqual(d.inputs.required.className,'form-control');
});
for(const [enabled,required]of [[true,true],[true,false],[false,false],[false,true]])test(`independent booleans round-trip enabled=${enabled}, required=${required}`,()=>{
  const {designer:d}=fixture();d.inputs.enabled.checked=enabled;d.inputs.required.checked=required;d.applyField();
  const result=d.effective().fields.name_on_card;assert.equal(result.enabled,enabled);assert.equal(result.required,required);assert.equal(result.label,'Name');
  d.edit('name_on_card');assert.equal(d.inputs.enabled.checked,enabled);assert.equal(d.inputs.required.checked,required);
});
test('Cancel does not mutate local field, ID or pinned revision',()=>{
  const own={template:{id:'t_pinned',revision:1},fields:{name_on_card:name}};
  const {designer:d,changes}=fixture({own});const before=d.get();d.inputs.label.value='Not applied';d.inputs.required.checked=false;d.fieldDialog.close();
  assert.deepEqual(plain(d.get()),plain(before));assert.equal(changes(),0);
});
test('apply label-only edit keeps ID, constraints, unknown keys and template revision',()=>{
  const own={template:{id:'t_pinned',revision:1},fields:{name_on_card:name}};
  const {designer:d,changes}=fixture({own});d.inputs.label.value='Name to appear on your card';d.applyField();
  assert.equal(changes(),1);assert.deepEqual(plain(d.value.template),own.template);
  assert.deepEqual(plain(d.effective().fields.name_on_card),{...name,label:'Name to appear on your card'});
});
test('inherited max length override is sparse, without copying required/enabled/help',()=>{
  const shared={fields:{name_on_card:name}},own={template:{id:'t_pin',revision:1}};
  const {designer:d,nodes}=fixture({shared,own});
  assert.match(nodes.get('[data-custom-error]').textContent,/read-only/);
  assert.equal(d.inputs,undefined);
  assert.deepEqual(plain(d.get()),own);
  assert.equal(shared.fields.name_on_card.max_length,30);
});
test('absent boolean defaults are not authored by ordinary edits',()=>{
  const {designer:d}=fixture({field:{type:'text',label:'Name',max_length:30}});d.inputs.max_length.value='20';d.applyField();
  assert.equal('required' in d.value.fields.name_on_card,false);assert.equal('enabled' in d.value.fields.name_on_card,false);
});
test('applying an unchanged inherited field does not inject empty customisation overrides',()=>{
  const {designer:d,changes,nodes}=fixture({shared:{fields:{name_on_card:name}},own:{}});assert.match(nodes.get('[data-custom-error]').textContent,/read-only/);assert.deepEqual(plain(d.get()),{});assert.equal(changes(),0);
});
test('unresolved existing identity is blocked, not populated as a new empty field',()=>{
  const {designer:d,nodes}=fixture();d.edit('missing');assert.match(nodes.get('[data-custom-error]').textContent,/no longer available/);
  assert.equal(d.editID,'name_on_card');
});
test('new ID collision and existing ID tampering are rejected without draft mutation',()=>{
  const {designer:d,nodes,changes}=fixture();d.inputs.id.value='other';d.applyField();assert.equal(changes(),0);assert.match(nodes.get('[data-custom-dialog-error]').textContent,/remain stable/);
  d.edit();d.inputs.id.value='name_on_card';d.inputs.label.value='Other';d.applyField();assert.equal(changes(),0);assert.match(nodes.get('[data-custom-dialog-error]').textContent,/already exists/);
});
test('type change keeps a manually chosen new ID and label, removes incompatible constraints only',()=>{
  const {designer:d}=fixture();d.edit();d.inputs.id.value='customer_age';d.inputs.id.dataset.edited='true';d.inputs.label.value='Age';d.inputs.type.value='number';d.inputs.type.emit('change');
  assert.equal(d.inputs.id.value,'customer_age');assert.equal(d.inputs.label.value,'Age');assert.ok(d.inputs.minimum);assert.equal('max_length' in d.inputs,false);
});
for(const [type,value,tag]of [['text','Example','INPUT'],['multiline','Message','TEXTAREA'],['number',4,'INPUT'],['checkbox',false,'INPUT'],['choice','a','SELECT'],['radio','a','SELECT'],['date','2026-10-16','INPUT'],['colour','#ff0000','INPUT']])test(`typed ${type} default loads and roundtrips without JSON parsing`,()=>{
  const field={type,label:'Test',default:value,...(['choice','radio'].includes(type)?{choices:[{id:'a',label:'A'}]}:{})};
  const {designer:d}=fixture({field});assert.equal(find(d.defaultBox,tag).tagName,tag);assert.equal(d.readInputs().default,value);d.applyField();assert.equal(d.value.fields.name_on_card.default,value);
});
test('checkbox group defaults use ordinary checked options and preserve explicit empty list',()=>{
  const field={type:'checkbox_group',label:'Extras',choices:[{id:'a',label:'A'},{id:'b',label:'B'}],default:['a']};
  const {designer:d}=fixture({field});const checks=d.defaultBox.querySelectorAll('*').filter(n=>n.type==='checkbox');
  assert.equal(checks[0].checked,true);assert.equal(checks[1].checked,false);
  checks[0].checked=false;checks[0].emit('change');assert.deepEqual(plain(d.readInputs().default),[]);
});
for(const value of [null,''])test(`legacy empty numeric default ${JSON.stringify(value)} survives an unrelated label edit`,()=>{
  const {designer:d}=fixture({field:{type:'number',label:'Age',default:value}});d.inputs.label.value='Your age';d.applyField();
  assert.equal(d.value.fields.name_on_card.label,'Your age');assert.equal(d.value.fields.name_on_card.default,value);
});
test('option label/reorder changes retain IDs, opaque metadata and default selection',()=>{
  const field={type:'choice',label:'Style',choices:[{id:'a',label:'A',future:{keep:[]}},{id:'b',label:'B'}],default:'a'};
  const {designer:d}=fixture({field});const first=d.optionBox.children.find(n=>n.className==='customisation-option-row');
  const inputs=first.querySelectorAll('*').filter(n=>n.tagName==='INPUT');assert.equal(inputs[0].readOnly,true);
  inputs[1].value='Renamed';inputs[1].emit('input');find(first,'BUTTON','Move option down').emit('click');
  const values=d.readInputs();assert.deepEqual(plain(values.choices),[field.choices[1],{...field.choices[0],label:'Renamed'}]);assert.equal(values.default,'a');
});
test('removing a selected option retains the orphan default and blocks Apply until reviewed',()=>{
  const {designer:d,nodes,changes}=fixture({field:{type:'choice',label:'Style',choices:[{id:'a',label:'A'},{id:'b',label:'B'}],default:'a'}});
  const first=d.optionBox.children.find(n=>n.className==='customisation-option-row');find(first,'BUTTON','Remove option').emit('click');d.applyField();
  assert.equal(changes(),0);assert.match(nodes.get('[data-custom-dialog-error]').textContent,/Default selection must match/);
});
test('legacy string options and legacy empty defaults are unchanged on a label-only field edit',()=>{
  const field={type:'choice',label:'Style',choices:['Old Option A','Old Option B'],default:''};const {designer:d}=fixture({field});
  d.inputs.label.value='Design';d.applyField();assert.deepEqual(plain(d.value.fields.name_on_card),{...field,label:'Design'});
});
test('file configuration categories and extensions round-trip; no file default or upload input',()=>{
  const field={type:'photo',label:'Photo',allowed_media_categories:['document','image'],allowed_extensions:['.png','.jpg'],max_file_size_bytes:5000,max_files:2};
  const {designer:d}=fixture({field});assert.equal(d.defaultBox,null);assert.deepEqual(plain(d.readInputs()),field);
  d.inputs.max_file_size_bytes.value='0';d.applyField();assert.equal(d.value.fields.name_on_card.max_file_size_bytes,5000);
});
for(const [field,message]of [[{type:'text',label:''},'label is required'],[{type:'text',label:'Name',min_length:30,max_length:20},'Maximum length'],[{type:'choice',label:'Style',choices:[]},'at least one option'],[{type:'choice',label:'Style',choices:[{id:'a',label:'A'},{id:'a',label:'B'}]},'unique'],[{type:'date',label:'Date',min_date:'2026-02-30'},'valid date']])test(`friendly configuration error: ${message}`,()=>assert.match(api.configurationError(field),new RegExp(message)));
