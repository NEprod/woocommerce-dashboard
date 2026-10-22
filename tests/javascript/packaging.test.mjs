import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Node {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.events={};this.value='';this.options=[];this.attributes={};}
  append(...nodes){this.children.push(...nodes);} appendChild(n){this.append(n);}
  replaceChildren(...nodes){this.children=[...nodes];} add(n){this.options.push(n);}
  addEventListener(event,callback){(this.events[event]||=[]).push(callback);}
  setAttribute(k,v){this.attributes[k]=v;} querySelector(){return null;} querySelectorAll(){return [];}
}
const script=fs.readFileSync(new URL('../../app/static/assets/js/packaging.js',import.meta.url),'utf8');
function fixture(answer={packages:[]}){
  const calls=[];const context=vm.createContext({structuredClone,setTimeout,clearTimeout,Option:function(label,value){this.label=label;this.value=value;},
    window:{},document:{querySelector:s=>s==='meta[name="csrf-token"]'?{content:'fixture-csrf'}:null,createElement:tag=>new Node(tag)},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>answer};}});
  vm.runInContext(script,context);return {...context.window.TLCPackaging,calls};
}
const plain=v=>JSON.parse(JSON.stringify(v));
test('sparse child retains inherited false and measures without copying parent fields',()=>{
  const api=fixture();const own={fragile:false,packed_dimensions_cm:{height:'0.3'}};
  const editor=new api.Editor(new Node('div'),{value:own,shared:{fragile:true,rollable:true,packed_dimensions_cm:{length:'42',width:'29.7'}}});
  assert.deepEqual(plain(editor.get()),own);
});
test('saved product override package survives real select option loading',async()=>{
  const api=fixture({packages:[{id:'saved-package',name:'Saved package'}]});
  // HTMLSelectElement.value clears when no matching option has been added yet.
  const root=new Node('div');
  const create=api.Editor;
  const oldAdd=Node.prototype.add;
  // Emulate this browser rule, missing from the original lightweight DOM.
  Object.defineProperty(Node.prototype,'value',{configurable:true,get(){return this._value || '';},set(v){
    this._value=this.tagName==='SELECT' && !(this.options || []).some(o=>o.value===v)?'':v;
  }});
  try{
    const editor=new create(root,{value:{selected_package_id:'saved-package',assignment_kind:'manual'}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(editor.get().selected_package_id,'saved-package');
  }finally{delete Node.prototype.value;Node.prototype.add=oldAdd;}
});
test('explicit false and package unassignment survive roundtrip',()=>{
  const api=fixture();const editor=new api.Editor(new Node('div'),{value:{fragile:false,selected_package_id:null},shared:{selected_package_id:'parent-id'}});
  assert.deepEqual(plain(editor.get()),{fragile:false,selected_package_id:null});
  assert.match(editor.assignmentStatus.textContent,/Explicitly no package/);
});
test('editing only packed height creates one sparse field, never a price or SKU',()=>{
  const api=fixture();let changes=0;const editor=new api.Editor(new Node('div'),{shared:{packed_dimensions_cm:{length:'42',height:'0.2'}},onChange:()=>changes++});
  const row=editor.controls.find(r=>r.axis==='height');row.input.value='0.4';
  row.input.events.input[0]();assert.equal(changes,1);
  assert.deepEqual(plain(editor.get()),{packed_dimensions:{height:'0.4',unit:'cm'}});
});
test('obsolete controls are absent but historical authoring is preserved',()=>{
  const api=fixture();const root=new Node('div'),own={fragile:false,override_reason:'Retain me',weight_g:150,measured:false};
  const editor=new api.Editor(root,{value:own});
  assert.deepEqual(plain(editor.get()),own);
  assert.deepEqual(Array.from(editor.controls,r=>r.axis),['length','width','height']);
  assert.equal(editor.assignmentCheckbox,undefined);
});
test('render only requests local choices, not Shopify or product writes',async()=>{
  const api=fixture();new api.Editor(new Node('div'),{});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(api.calls.length,1);assert.equal(api.calls[0].url,'/packaging/choices');assert.equal(api.calls[0].options.method,'GET');
});
test('inline creation is isolated and CSRF protected in one shared component',()=>{
  assert.match(script,/document\.body\.append\(dialog\)/);assert.match(script,/'X-CSRFToken':csrf\(\)/);
  assert.match(script,/data-package-cancel/);assert.match(script,/await onSaved\(saved\)/);
  assert.match(script,/inlinePackage\(async\(\)=>\{await this.refreshChoices\(\);this.queueRecommendation/);
  assert.doesNotMatch(script,/innerHTML|shopify\.com|orders\/.*fulfill/);
});
test('finished dimensions retain units and whole-object inheritance independently',()=>{
  const api=fixture();const own={length:'6',width:'6',unit:'in',label:'folded'};
  const editor=new api.FinishedEditor(new Node('div'),{value:own,shared:{length:'42',unit:'cm'}});
  assert.deepEqual(plain(editor.get()),own);assert.match(editor.summary.textContent,/6 × 6 in/);
  editor.source.value='inherit';assert.equal(editor.get(),undefined);
});
test('no package is a valid default and recommendation has no automatic acceptance',()=>{
  const api=fixture();const editor=new api.Editor(new Node('div'),{});assert.equal(editor.get(),undefined);
  assert.match(editor.assignmentStatus.textContent,/No package selected/);
  assert.doesNotMatch(script,/simulator|shipping\/rates|multi.parcel/);
});

test('one-time copy keeps unsaved title, finished values, price and weight unchanged',()=>{
  const api=fixture(),doc={title:'Unsaved title',price:'8.50',weight:'150',dimensions:{length:420,width:297,height:1},future:{keep:true}};
  const before=structuredClone(doc),copy=api.applyDimensionCopy(doc,[{key:'',copy:{length:420,width:297,height:1,unit:'mm'}}]);
  assert.deepEqual(doc,before);assert.deepEqual(plain(copy.dimensions),doc.dimensions);
  assert.equal(copy.title,doc.title);assert.equal(copy.price,doc.price);assert.equal(copy.weight,doc.weight);
  assert.equal(copy.packaging.measured,undefined);assert.equal(copy.packaging.selected_package_id,undefined);
  copy.dimensions.length=450;assert.equal(copy.packaging.packed_dimensions.length,420);
});
test('multi-size copy preserves existing modifier identities and exceptions',()=>{
  const api=fixture(),doc={variation_modifiers:{'Size=A3':{price:'8.50',weight:'150',dimensions:{length:420}},'Colour=Blue|Size=A3':{packaging:{fragile:true}}}};
  const copy=api.applyDimensionCopy(doc,[{key:'Size=A3',copy:{length:420,width:297,height:1,unit:'mm'}}]);
  assert.equal(copy.variation_modifiers['Size=A3'].price,'8.50');assert.equal(copy.variation_modifiers['Size=A3'].weight,'150');
  assert.deepEqual(plain(copy.variation_modifiers['Colour=Blue|Size=A3']),doc.variation_modifiers['Colour=Blue|Size=A3']);
});
test('overwrite requires explicit confirmation including inherited packed sizes',()=>{
  const api=fixture(),config={key:'',copy:{length:420,unit:'mm'},packed:{packed_dimensions_cm:{length:44}}};
  assert.throws(()=>api.applyDimensionCopy({},[config]),/Confirm overwrite/);
  assert.equal(api.applyDimensionCopy({},[config],{overwrite:true}).packaging.packed_dimensions.length,420);
});
test('unit-explicit packed data is roundtrip safe and converts display without finished edits',()=>{
  const api=fixture(),own={packed_dimensions:{length:6,width:4,height:0.1,unit:'in'},measured:true};
  const editor=new api.Editor(new Node('div'),{value:own});
  assert.deepEqual(plain(editor.get()),own);
  editor.unit.value='mm';editor.unit.events.change[0]();
  const value=plain(editor.get());assert.equal(value.packed_dimensions.unit,'mm');assert.equal(value.packed_dimensions.length,'152.4');
  assert.equal(value.packed_dimensions_cm,undefined);assert.equal(value.dimensions,undefined);
});
test('single choice explicitly rejects inherited package without deleting size',()=>{
  const api=fixture(),editor=new api.Editor(new Node('div'),{shared:{selected_package_id:'parent-id',packed_dimensions_cm:{length:'20'}}});
  editor.selectPackage(null);
  assert.equal(editor.get().selected_package_id,null);assert.equal(editor.shared.selected_package_id,'parent-id');
});

const descendants=node=>[node,...node.children.flatMap(n=>n instanceof Node?descendants(n):[])];
test('recommendation, explicit acceptance, alternative and decline stay distinct',async()=>{
  const api=fixture({packages:[{id:'first',name:'First'},{id:'second',name:'Second'}],recommendation:{package_id:'first',name:'First',fit:'Measured',gross_weight_g:'90'},alternatives:[{package_id:'second',name:'Second',gross_weight_g:'100'}]});
  const root=new Node('div'),editor=new api.Editor(root,{});
  await descendants(root).find(n=>n.textContent==='Recalculate recommendation').events.click[0]();
  assert.equal(editor.get(),undefined);
  descendants(root).find(n=>n.textContent==='Use recommended package').events.click[0]();
  assert.equal(editor.get().selected_package_id,'first');assert.equal(editor.get().assignment_kind,'accepted');
  descendants(root).find(n=>n.textContent==='Use alternative: Second').events.click[0]();
  assert.equal(editor.get().selected_package_id,'second');assert.equal(editor.get().assignment_kind,'manual');
  editor.packageSelect.value='';editor.packageSelect.events.change[0]();
  assert.equal(editor.get().selected_package_id,null);
});
test('size configurations keep separate solo-package selections',()=>{
  const api=fixture(),a=new api.Editor(new Node('div'),{}),b=new api.Editor(new Node('div'),{});
  a.selectPackage('small','accepted');b.selectPackage('large','manual');
  assert.equal(a.get().selected_package_id,'small');assert.equal(b.get().selected_package_id,'large');
  a.selectPackage(null);assert.equal(b.get().selected_package_id,'large');
});

for(const [file,fn,end] of [['metadata-editor.js','modifierValues','populate'],['prepared-metadata.js','modifiersValue','populate']]){
  test(file+' packed-only edits preserve numeric authored price, weight and finished modifier types',()=>{
    const source=fs.readFileSync(new URL('../../app/static/assets/js/'+file,import.meta.url),'utf8');
    const original={price:8.5,weight:150,dimensions:{length:420,width:297,height:1}};
    const values={expression:'Size=A3',price:'8.5',sale_price:'',weight:'150',length:'420',width:'297',height:'1'};
    const row={_originalModifier:original,_packagingEditor:{get:()=>({selected_package_id:null})},querySelector:s=>({value:values[s.match(/data-modifier-field="([^"]+)"/)[1]]})};
    const context=vm.createContext({structuredClone,isShared:true,guided:{querySelectorAll:()=>[row]}});
    vm.runInContext(source.slice(source.indexOf('  function '+fn+'('),source.indexOf('  function '+end+'(')),context);
    const value=plain(vm.runInContext(fn+'()',context))['Size=A3'];
    assert.equal(value.price,8.5);assert.equal(value.weight,150);assert.deepEqual(value.dimensions,original.dimensions);
    assert.deepEqual(value.packaging,{selected_package_id:null});
  });
}

test('finished placeholders never enter JSON; explicit editing and reset are independent of package',()=>{
  const api=fixture(),editor=new api.Editor(new Node('div'),{getDocument:()=>({dimensions:{length:155,width:155,height:3}})});
  editor.refreshFallback();assert.equal(editor.controls[0].input.placeholder,'155 (provisional)');
  assert.equal(editor.get(),undefined);
  editor.selectPackage('mailer');assert.equal(editor.get().packed_dimensions,undefined);
  const height=editor.controls.find(r=>r.axis==='height');height.input.value='4';height.input.events.input[0]();
  assert.deepEqual(plain(editor.get().packed_dimensions),{height:'4',unit:'mm'});
  editor.source.value='inherit';editor.source.events.change[0]();
  assert.deepEqual(plain(editor.get()),{selected_package_id:'mailer',assignment_kind:'manual'});
});

test('inherited packed display, explicit none and restore inheritance remain sparse',()=>{
  const api=fixture(),parent={packed_dimensions:{length:16,width:16,height:.3,unit:'cm'},selected_package_id:'parent'};
  const editor=new api.Editor(new Node('div'),{shared:parent});editor.refreshFallback();
  assert.equal(editor.controls[0].input.placeholder,'16 (inherited)');assert.equal(editor.get(),undefined);
  editor.selectPackage(null);assert.deepEqual(plain(editor.get()),{selected_package_id:null});
  editor.selectPackage(undefined);assert.equal(editor.get(),undefined);assert.equal(parent.selected_package_id,'parent');
});

test('actual Product Override serializer marks package choice dirty and reversion clean',()=>{
  const api=fixture(),editor=new api.Editor(new Node('div'),{});
  const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
  const extract=(fn,end)=>source.slice(source.indexOf('  function '+fn+'('),source.indexOf('  function '+end+'('));
  const original={title:'Anniversary Level Complete',future:{keep:true}};
  const context=vm.createContext({authored:original,originalDocument:structuredClone(original),structuredClone,
    initialValues:{},window:{},knownFields:new Set(['packaging']),customisationDesigner:null,packagingDesigner:editor,finishedDesigner:null,
    guided:{querySelectorAll:()=>[]},overrideEnabled:()=>false,isShared:false,explicitVariation:false,
    attributeValues:()=>({}),modifierValues:()=>({}),listValues:()=>[],channelValues:()=>({}),activeMode:'guided',saveStates:[]});
  vm.runInContext(extract('guidedValues','guidedDocument')+extract('guidedDocument','documentPatch')+extract('documentPatch','clearErrors')+extract('setDirty','setBusy'),context);
  context.initialValues=structuredClone(vm.runInContext('guidedValues()',context));
  editor.selectPackage('c5-mailer');
  vm.runInContext('setDirty(true)',context);assert.equal(context.dirty,true);
  const document=plain(vm.runInContext('guidedDocument()',context));
  assert.deepEqual(document,{...original,packaging:{selected_package_id:'c5-mailer',assignment_kind:'manual'}});
  context.after=document;
  assert.deepEqual(plain(vm.runInContext('documentPatch(originalDocument,after)',context)),{data:{packaging:document.packaging},remove_paths:[]});
  editor.selectPackage(undefined);vm.runInContext('setDirty(true)',context);assert.equal(context.dirty,false);
  assert.deepEqual(plain(vm.runInContext('guidedDocument()',context)),original);
});

test('Catalogue Intake serializer preserves explicit null package rather than pruning it',()=>{
  const source=fs.readFileSync(new URL('../../app/static/assets/js/prepared-metadata.js',import.meta.url),'utf8');
  const context=vm.createContext({});vm.runInContext(source.slice(source.indexOf('  function prune('),source.indexOf('  function guidedDocument(')),context);
  assert.deepEqual(plain(vm.runInContext('prune({packaging:{selected_package_id:null},title:"Draft"})',context)),{packaging:{selected_package_id:null},title:'Draft'});
});

test('inherited size exception bypasses only the commercial save gate, not sparse ownership',()=>{
  const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
  const api=fixture(),editor=new api.Editor(new Node('div'),{shared:{selected_package_id:'parent'}});
  const original={price:8.5,weight:150,dimensions:{length:420,width:297,height:1}};
  const fields={expression:'Size=A3',price:'8.5',sale_price:'',weight:'150',length:'420',width:'297',height:'1'};
  const row={_originalModifier:original,_packagingEditor:editor,querySelector:s=>({value:fields[s.match(/data-modifier-field="([^"]+)"/)[1]]})};
  const context=vm.createContext({authored:{},structuredClone,overrideEnabled:()=>false,guided:{querySelectorAll:()=>[row]}});
  vm.runInContext(source.slice(source.indexOf('  function modifierValues('),source.indexOf('  function populate(')),context);
  assert.deepEqual(plain(vm.runInContext('modifierValues(true)',context)),{});
  editor.selectPackage('mailer');
  assert.deepEqual(plain(vm.runInContext('modifierValues(true)',context)),{'Size=A3':{packaging:{selected_package_id:'mailer',assignment_kind:'manual'}}});
  editor.selectPackage(null);
  assert.deepEqual(plain(vm.runInContext('modifierValues(true)',context)),{'Size=A3':{packaging:{selected_package_id:null}}});
  editor.selectPackage(undefined);assert.deepEqual(plain(vm.runInContext('modifierValues(true)',context)),{});
});

test('source resolution preserves legacy modifier replacement and shipping-only inheritance',()=>{
  const api=fixture(),parent={variation_modifiers:{'Size=A3':{price:8.5,weight:150},'Size=A4':{price:5}}};
  const shipping={variation_modifiers:{'Size=A3':{packaging:{selected_package_id:null}}}};
  assert.equal(api.sourceDocument(parent,shipping).variation_modifiers['Size=A3'].price,8.5);
  const legacy={variation_modifiers:{'Size=A4':{price:9}}};
  assert.deepEqual(plain(api.sourceDocument(parent,legacy).variation_modifiers),legacy.variation_modifiers);
  assert.deepEqual(plain(api.sourceDocument(parent,{variation_modifiers:{}}).variation_modifiers),{});
  parent.variation_modifiers['Size=A3'].packaging={selected_package_id:'inherited'};
  const mixed=api.sourceDocument(parent,legacy).variation_modifiers;
  assert.equal(mixed['Size=A3'].price,undefined);
  assert.equal(mixed['Size=A3'].packaging.selected_package_id,'inherited');
});

test('late package choices retain a newer manual choice and never trigger a dirty callback',async()=>{
  const api=fixture({packages:[{id:'first',name:'First'},{id:'second',name:'Second'}]});let changes=0;
  const editor=new api.Editor(new Node('div'),{value:{selected_package_id:'first'},onChange:()=>changes++});
  editor.selectPackage('second');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(editor.packageSelect.value,'second');assert.equal(editor.get().selected_package_id,'second');assert.equal(changes,1);
});

test('provisional variant dimensions use existing commercial modifier precedence',()=>{
  const api=fixture(),doc={dimensions:{length:100,width:50,height:2},variation_modifiers:{'Size=A3':{price:9,dimensions:{length:420,width:297,height:1}},'Colour=Blue|Size=A3':{price:10}}};
  const editor=new api.Editor(new Node('div'),{getDocument:()=>doc,getAttributes:()=>({Size:'A3',Colour:'Blue'})});
  editor.refreshFallback();assert.equal(editor.controls[0].input.placeholder,'100 (provisional)');
  assert.equal(editor.get(),undefined);
});
