import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../app/static/assets/js/customisation-designer.js',import.meta.url),'utf8');
const context=vm.createContext({structuredClone,console});vm.runInContext(source,context);
const api=context.TLCCustomisations;
const plain=value=>JSON.parse(JSON.stringify(value));
const shared={proof_policy:'optional',fields:{dog_name:{type:'text',label:"Dog's Name",required:true,max_length:30,order:0,future:{keep:[]}}}};

test('collection inheritance and distinct additions retain IDs, constraints and opaque keys',()=>{
  const own={fields:{date:{type:'date',label:'Memorial Date',order:0}}};
  const result=api.resolve({},shared,own);
  assert.equal(result.fields.dog_name.required,true);assert.equal(result.fields.dog_name.max_length,30);
  assert.deepEqual(plain(result.fields.dog_name.future),{keep:[]});assert.equal(shared.fields.dog_name.max_length,30);
  assert.deepEqual(plain(api.order(result).map(([id])=>id)),['dog_name','date']);
});
test('editing label preserves field identity and sparse unknown values',()=>{
  const result=api.changeField({fields:{dog_name:{future:{opaque:[]}}}},{},'dog_name',{label:'Pet name',max_length:30});
  assert.deepEqual(plain(result),{fields:{dog_name:{future:{opaque:[]},label:'Pet name',max_length:30}}});
});
test('only local fields can be removed; inherited fields cannot be patched or excluded',()=>{
  assert.throws(()=>api.removeLocal({},shared,'dog_name'),/read-only/);
  assert.throws(()=>api.changeField({},shared,'dog_name',{required:false}),/read-only/);
  assert.equal(api.resolve({},shared,{}).fields.dog_name.label,"Dog's Name");
  assert.deepEqual(plain(api.removeLocal({fields:{local:{type:'text',label:'Local'}}},shared,'local')),{fields:{}});
});
test('reordering local fields retains stable IDs and leaves inherited values untouched',()=>{
  const own={fields:{date:{type:'date',label:'Date',order:0},message:{type:'text',label:'Message',order:1}}};
  const effective=api.resolve({},shared,own),next=api.reorder(own,effective,'message',-1);
  assert.deepEqual(plain(api.order(api.resolve({},shared,next)).map(([id])=>id)),['dog_name','message','date']);
  assert.equal(Object.hasOwn(next.fields,'dog_name'),false);
  assert.throws(()=>api.reorder(own,effective,'dog_name',1),/Inherited/);
});
test('pinned template fields reject shared patches and product exclusions',()=>{
  assert.throws(()=>api.resolve(shared,{fields:{dog_name:{max_length:25}}},{}),/Duplicate/);
  assert.throws(()=>api.resolve(shared,{}, {fields:{dog_name:{remove:true}}}),/dog_name.remove is obsolete/);
});
test('canonical editor has no template exclusion, inherited reset or replacement controls',()=>{
  const html=fs.readFileSync(new URL('../../app/templates/includes/_customisation_designer.html',import.meta.url),'utf8');
  assert.doesNotMatch(source,/data-custom-(no-template|inherit-template|reset)|patch\.remove|Override Inherited Field|function exclude\(/);
  assert.doesNotMatch(html,/data-custom-(no-template|inherit-template|reset)/);
  assert.throws(()=>api.resolve({}, {}, {template:{id:'t_child',revision:1}},true),/obsolete/);
  assert.throws(()=>api.resolve({}, {}, {template:null}),/template:null is obsolete/);
  assert.throws(()=>api.resolve({}, {}, {fields:{field:{remove:false,type:'text',label:'Name'}}}),/field.remove is obsolete/);
  assert.equal(api.exclude,undefined);
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
