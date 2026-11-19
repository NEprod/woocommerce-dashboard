import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
const acknowledgementsCode=source.slice(source.indexOf('  const acknowledgements ='),source.indexOf('  const isShared ='));
test('desktop and mobile acknowledgements mirror explicit checking and unchecking',()=>{
  const controls=[0,1].map(()=>({checked:false,addEventListener(event,fn){this.change=fn;}}));
  vm.runInNewContext(acknowledgementsCode,{document:{querySelectorAll:()=>controls},Array});
  controls[1].checked=true;controls[1].change();assert.deepEqual(controls.map(c=>c.checked),[true,true]);
  controls[0].checked=false;controls[0].change();assert.deepEqual(controls.map(c=>c.checked),[false,false]);
});

const scanCode=source.slice(source.indexOf('  async function retryScan()'),source.indexOf('  function startProgress('));
function scanContext({dirty=false,ack=false,canSave=true}={}){
  const calls=[],messages=[];
  const context=vm.createContext({dirty,busy:false,boot:{can_save:canSave,kind:'shared',revision:'fresh-signed-revision',save_url:'/fixture/save'},
    document:{querySelector:selector=>selector==='[data-acknowledge-shared]'?{checked:ack}:{content:'csrf'}},
    showFeedback:(...args)=>messages.push(args),setBusy:()=>{},populate:()=>{},setDirty:()=>{},startProgress:()=>{},window:{},
    fetch:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return{ok:true,json:async()=>({revision:'next',authored:{},run_id:'isolated'})};}});
  vm.runInContext(scanCode,context);return {context,calls,messages};
}
test('scoped scan cannot discard unsaved edits or implicitly acknowledge siblings',async()=>{
  for(const options of [{dirty:true,ack:true},{ack:false},{ack:true,canSave:false}]){
    const {context,calls,messages}=scanContext(options);await vm.runInContext('retryScan()',context);
    assert.equal(calls.length,0);assert.equal(messages.length,1);
  }
});
test('scoped scan uses existing signed local contract without any JSON patch or Shopify request',async()=>{
  const {context,calls}=scanContext({ack:true});await vm.runInContext('retryScan()',context);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/fixture/save');
  assert.deepEqual(calls[0].body,{kind:'shared',data:{},revision:'fresh-signed-revision',retry_scan:true,acknowledge_shared:true});
});
