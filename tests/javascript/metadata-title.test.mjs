import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const values=source.slice(source.indexOf('  function valueFor('),source.indexOf('  function overrideEnabled('));
test('missing child title stays blank rather than copying shared or computed titles',()=>{
  const context=vm.createContext({authored:{},isShared:false,boot:{collection:{title:'Collection title',description:'Inherited description'},resolved:{title:'Folder | Collection title'}}});
  vm.runInContext(values,context);
  assert.equal(vm.runInContext("valueFor('title')",context),'');
  assert.equal(vm.runInContext("valueFor('description')",context),'Inherited description');
  context.authored={title:'Authored child'};
  assert.equal(vm.runInContext("valueFor('title')",context),'Authored child');
});
test('customisation-only roundtrip cannot introduce an absent title',()=>{
  const authored={customisation:{template:{id:'pinned',revision:1}},future:{keep:[]}};
  const context=vm.createContext({structuredClone,authored,initialValues:{},guidedValues:()=>({customisation:{...authored.customisation,fields:{name:{max_length:20}}}})});
  vm.runInContext(source.slice(source.indexOf('  function guidedDocument('),source.indexOf('  function documentPatch(')),context);
  const result=JSON.parse(vm.runInContext('JSON.stringify(guidedDocument())',context));
  assert.equal('title' in result,false);assert.deepEqual(result.customisation.template,authored.customisation.template);
});

const code=source.slice(source.indexOf('  function showTitlePreview('),source.indexOf('  function updateCodePresentation('));
function preview({shared=false}={}) {
  const nodes={'[data-title-preview]':{textContent:'Saved canonical title'},'[data-title-preview-status]':{textContent:''}};
  const callbacks=[],calls=[];
  const context=vm.createContext({root:{querySelector:key=>nodes[key]},activeMode:'guided',boot:{kind:shared?'shared':'override',product_id:1,validate_url:'/api/metadata/validate'},
    window:{clearTimeout(){},setTimeout:fn=>{callbacks.push(fn);return callbacks.length;}},
    guidedDocument:()=>({title:'Unsaved component'}),
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({valid:true,title_preview:{title:'Server canonical result'}})};}});
  vm.runInContext(code,context);
  return {context,nodes,callbacks,calls,queue:()=>vm.runInContext('queueTitlePreview()',context)};
}
for(const shared of [false,true])test(`draft ${shared?'shared':'product'} preview uses server resolver without a save/Shopify request`,async()=>{
  const p=preview({shared});p.queue();assert.equal(p.calls.length,0);await p.callbacks[0]();
  assert.equal(p.calls[0].url,'/api/metadata/validate');
  assert.deepEqual(JSON.parse(p.calls[0].options.body),{kind:shared?'shared':'override',data:{title:'Unsaved component'},product_id:1});
  assert.equal(p.nodes['[data-title-preview]'].textContent,'Server canonical result');
  assert.match(p.nodes['[data-title-preview-status]'].textContent,/Local draft preview only/);
});
test('older preview response cannot overwrite newer draft title evidence',async()=>{
  const p=preview();const replies=[];
  p.context.fetch=()=>new Promise(resolve=>replies.push(resolve));
  p.queue();const old=p.callbacks[0]();p.queue();const fresh=p.callbacks[1]();
  replies[1]({ok:true,json:async()=>({valid:true,title_preview:{title:'New canonical title'}})});await fresh;
  replies[0]({ok:true,json:async()=>({valid:true,title_preview:{title:'Old canonical title'}})});await old;
  assert.equal(p.nodes['[data-title-preview]'].textContent,'New canonical title');
});
test('failed validation preserves the draft and identifies unavailable preview',async()=>{
  const p=preview();p.context.fetch=async()=>({ok:false,json:async()=>({valid:false})});p.queue();await p.callbacks[0]();
  assert.equal(p.nodes['[data-title-preview]'].textContent,'Saved canonical title');
  assert.match(p.nodes['[data-title-preview-status]'].textContent,/Preview unavailable/);
});
