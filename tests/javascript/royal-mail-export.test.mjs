import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const script=fs.readFileSync(new URL('../../app/static/assets/js/royal-mail-export.js',import.meta.url),'utf8');
function fixture({ok=true,network=false,invalid=false}={}) {
  let run;const downloaded=[];const timers=[];
  const button={dataset:{royalMailExport:'/packaging/royal-mail.csv'},disabled:false,addEventListener:(e,f)=>run=f};
  const feedback={hidden:true,textContent:''};
  const values={'Content-Disposition':'attachment; filename="tlc-royal-mail-products-2026-11-04.csv"','Content-Type':'text/csv; charset=utf-8',
    'X-Export-Row-Count':'53','X-Export-Warnings':'{"Missing HS Code":2}'};
  const context=vm.createContext({document:{querySelector:s=>s==='[data-royal-mail-export]'?button:feedback,
    createElement:()=>({click(){downloaded.push(this.download);},remove(){}}),body:{appendChild(){}}},
    fetch:async(url,options)=>{assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');
      if(network)throw new Error('Network interrupted');return {ok,headers:{get:k=>invalid?null:values[k]},
        blob:async()=>({}),json:async()=>({error:'Duplicate sellable SKU: TEST-1'})};},
    URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},setTimeout:f=>timers.push(f)});
  vm.runInContext(script,context);
  return {button,feedback,downloaded,timers,run};
}
test('successful download uses server filename/count and existing alert notification styling',async()=>{
  const f=fixture();await f.run();assert.deepEqual(f.downloaded,['tlc-royal-mail-products-2026-11-04.csv']);
  assert.match(f.feedback.textContent,/53 product rows exported/);assert.match(f.feedback.textContent,/Missing HS Code: 2/);
  assert.equal(f.feedback.className,'alert alert-success');assert.equal(f.button.disabled,false);assert.equal(f.timers.length,1);
});
for(const scenario of [{ok:false},{network:true},{invalid:true}])test(`failed export cannot claim success ${JSON.stringify(scenario)}`,async()=>{
  const f=fixture(scenario);await f.run();assert.equal(f.downloaded.length,0);assert.equal(f.feedback.className,'alert alert-danger');
  assert.match(f.feedback.textContent,/export failed/);assert.equal(f.button.disabled,false);
});
