import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../app/static/assets/js/customisation-designer.js',import.meta.url),'utf8');
const context=vm.createContext({structuredClone,console});vm.runInContext(source,context);
const api=context.TLCCustomisations;
const plain=value=>JSON.parse(JSON.stringify(value));
const shared={proof_policy:'optional',fields:{dog_name:{type:'text',label:"Dog's Name",required:true,max_length:30,order:0,future:{keep:[]}}}};

test('collection inheritance and sparse child patches retain IDs, constraints and opaque keys',()=>{
  const own={fields:{dog_name:{max_length:20},date:{type:'date',label:'Memorial Date',order:1}}};
  const result=api.resolve({},shared,own);
  assert.equal(result.fields.dog_name.required,true);assert.equal(result.fields.dog_name.max_length,20);
  assert.deepEqual(plain(result.fields.dog_name.future),{keep:[]});assert.equal(shared.fields.dog_name.max_length,30);
  assert.deepEqual(plain(api.order(result).map(([id])=>id)),['dog_name','date']);
});
test('editing label preserves field identity and sparse unknown values',()=>{
  const result=api.changeField({fields:{dog_name:{future:{opaque:[]}}}},shared,'dog_name',{label:'Pet name',max_length:30});
  assert.deepEqual(plain(result),{fields:{dog_name:{future:{opaque:[]},label:'Pet name'}}});
});
test('exclusion is per-product tombstone, resetting deletes only local patch',()=>{
  const excluded=api.exclude({},shared,'dog_name');assert.deepEqual(plain(excluded),{fields:{dog_name:{remove:true}}});
  assert.deepEqual(plain(api.resolve({},shared,excluded).fields),{});
  delete excluded.fields.dog_name;assert.equal(api.resolve({},shared,excluded).fields.dog_name.label,"Dog's Name");
  assert.deepEqual(plain(api.exclude({fields:{local:{type:'text'}}},shared,'local')),{fields:{}});
});
test('reordering retains stable IDs and fields, including inherited values',()=>{
  const effective=api.resolve({},shared,{fields:{date:{type:'date',label:'Date',order:1}}});
  const own=api.reorder({},effective,'date',-1);
  assert.deepEqual(plain(api.order(api.resolve({},effective,own)).map(([id])=>id)),['date','dog_name']);
  assert.deepEqual(plain(own),{fields:{date:{order:0},dog_name:{order:1}}});
});
test('pinned template base then authored shared then local patches; removal does not resurrect',()=>{
  const result=api.resolve(shared,{fields:{dog_name:{max_length:25}}},{fields:{dog_name:{remove:true}}});
  assert.deepEqual(plain(result.fields),{});assert.equal('template' in result,false);
});
for(const [type,field,value,valid]of [
  ['text',{required:true},'',false],['text',{max_length:3},'long',false],['text',{},'',true],
  ['multiline',{min_length:2},'ok',true],['number',{whole_number:true},1.2,false],['number',{maximum:10},4,true],
  ['choice',{choices:[{id:'a',label:'A'}]},'a',true],['radio',{choices:['A']},'B',false],
  ['checkbox',{required:true},false,false],['checkbox',{},false,true],
  ['checkbox_group',{choices:['a','b'],min_selections:1,max_selections:1},['a','b'],false],
  ['date',{},'2026-02-30',false],['date',{min_date:'2026-01-01'},'2026-10-16',true],
  ['colour',{choices:[{id:'red',label:'Red',value:'#ff0000'}]},'#ff0000',true],['colour',{},'red',false]
])test(`local preview validates ${type}: ${JSON.stringify(value)}`,()=>assert.equal(!api.validateAnswer({type,...field},value),valid));
test('safe DOM construction and preview do not submit customer answers or files',()=>{
  const preview=source.slice(source.indexOf('    preview()'));
  assert.match(preview,/event.preventDefault\(\)/);assert.match(preview,/no file input or network upload/);
  assert.doesNotMatch(preview,/fetch\(|FormData|type="file"|innerHTML/);
  assert.match(source,/showModal\(\)/);assert.match(source,/fieldDialog.close\(\)/);
});
test('normal editor integrates designer into its existing signed patch, not a separate save endpoint',()=>{
  const editor=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
  assert.match(editor,/customisationDesigner.get\(\)/);assert.match(editor,/customisationDesigner.value = structuredClone\(authored.customisation\)/);
  assert.match(editor,/revision: boot.revision/);assert.match(editor,/documentPatch\(originalDocument, documentValue\)/);
});
test('template management is CSRF protected and uses expected revision, no deletes or remote writes',()=>{
  const library=fs.readFileSync(new URL('../../app/static/assets/js/customisation-library.js',import.meta.url),'utf8');
  assert.match(library,/expected_revision:current\?\.revision/);assert.match(library,/X-CSRFToken/);
  assert.doesNotMatch(library,/method:\s*['"]DELETE|https:\/\/.*myshopify|FormData|publish\(/i);
});

// Browser-specific regression: TEXTAREA.type and SELECT.type are read-only.
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.options=[];this.dataset={};this.value='';this.checked=false;this.events={};
    if(['textarea','select'].includes(tag))Object.defineProperty(this,'type',{get:()=>tag});}
  append(...nodes){this.children.push(...nodes);} prepend(node){this.children.unshift(node);}
  replaceChildren(...nodes){this.children=[...nodes];} add(node){this.options.push(node);}
  addEventListener(event,callback){this.events[event]=callback;} setAttribute(){} reportValidity(){return true;}
}
const domContext=vm.createContext({structuredClone,document:{createElement:tag=>new Element(tag)},Option:function(label,value){this.label=label;this.value=value;}});
vm.runInContext(source,domContext);const domAPI=domContext.TLCCustomisations;
for(const type of ['text','multiline','number','choice','radio','checkbox','checkbox_group','date','colour','photo'])test(`realistic DOM controls build and preview ${type} without assigning read-only DOM properties`,()=>{
  const nodes=new Map();const root={querySelector:selector=>{if(!nodes.has(selector))nodes.set(selector,new Element('div'));return nodes.get(selector);}};
  const designer=Object.create(domAPI.Designer.prototype);Object.assign(designer,{root,editID:null,editOriginal:{},inputs:{},previewDialog:{showModal(){this.open=true;}},base:{},shared:{},value:{fields:{input:{type,label:'Test',choices:['A'],default:type==='checkbox'?false:undefined}}}});
  const field=designer.value.fields.input;if(!['choice','radio','checkbox_group'].includes(type))delete field.choices;
  designer.buildInputs(field);designer.preview();assert.equal(designer.previewDialog.open,true);
  assert.equal(designer.inputs.type.value,type);
  if(type==='checkbox')assert.equal('max_length' in designer.inputs,false);
  if(type==='photo'){assert.equal('max_file_size_bytes' in designer.inputs,true);assert.equal('default' in designer.inputs,false);}
});
