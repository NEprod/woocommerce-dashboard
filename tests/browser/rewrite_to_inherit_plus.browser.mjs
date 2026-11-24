// Run against catalogue_bom_fixture.py --rewrite-fixture; no live data.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
  for(const[name,viewport]of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
    const seed=await(await context.request.get(origin+'/fixture')).json(),pid=name==='mobile'?seed.mobile_pid:seed.pid;
    const page=await context.newPage(),errors=[];let accept=false,saves=0;
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>accept?d.accept():d.dismiss());
    page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/save'))saves++;});
    await page.goto(`${origin}/edit_products/${pid}/edit/override`);
    const scope=page.locator('[data-catalogue-costing]'),mode=scope.locator('[data-product-bom-mode]');
    const local=kind=>scope.locator(`[data-bom-rows=${kind}] tbody tr:not([data-inherited])`);
    const inherited=kind=>scope.locator(`[data-bom-rows=${kind}] tbody tr[data-inherited]`);
    await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).waitFor();
    assert.equal(await mode.inputValue(),'replace');
    await mode.selectOption('add');assert.equal(await mode.inputValue(),'replace'); // Cancel is inert.
    assert.equal(await local('inventory').count(),2);assert.equal(await scope.locator('tr[data-inherited]').count(),0);
    accept=true;await mode.selectOption('add');
    await scope.getByRole('status').filter({hasText:'Review overlapping requirements'}).waitFor();
    for(const[k,n]of [['inventory',2],['packaging',1],['process',1]]){
      assert.equal(await local(k).count(),n);assert.equal(await inherited(k).count(),n);
      assert.equal(await inherited(k).locator('button').count(),0);
    }
    assert.equal(await scope.locator('[name=labour_minutes]').inputValue(),'2');
    assert.equal(await scope.locator('[name=print_waste_rate]').inputValue(),'11');
    assert.equal(saves,0,'Switching only changes the working draft');
    // Switching twice must retain the Rewrite draft without accumulating rows.
    await mode.selectOption('replace');await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).waitFor();
    assert.equal(await local('inventory').count(),2);assert.equal(await inherited('inventory').count(),0);
    await mode.selectOption('add');await scope.getByRole('status').filter({hasText:'Review overlapping requirements'}).waitFor();
    assert.equal(await local('inventory').count(),2);
    await local('packaging').getByRole('button',{name:'Remove',exact:true}).click();
    assert.equal(await local('packaging').count(),0);assert.equal(await inherited('packaging').count(),1);
    const saved=page.waitForResponse(r=>r.url().includes('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]').first().click();const response=await saved;assert.equal(response.status(),200);
    const data=response.request().postDataJSON().data.production;
    assert.equal(data.mode,'add');assert.equal(Object.keys(data.lines).length,2);
    assert.ok(Object.keys(data.lines).every(k=>!['body','pla','box'].includes(k)),'Reused shared IDs are rekeyed, not merged');
    assert.equal(data.costing.equipment.length,1);assert.equal(data.costing.equipment[0].minutes,'250');
    // Save is a sparse patch: unchanged scalar assumptions need not be sent.
    if('labour_minutes' in data.costing)assert.equal(data.costing.labour_minutes,'2');
    if('print_waste_rate' in data.costing)assert.equal(data.costing.print_waste_rate,'11');
    await page.reload();await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).waitFor();
    assert.equal(await mode.inputValue(),'add');assert.equal(await local('inventory').count(),2);
    assert.equal(await inherited('inventory').count(),2);assert.equal(await inherited('packaging').count(),1);
    assert.equal(await scope.locator('[name=labour_minutes]').inputValue(),'2');
    assert.equal(await scope.locator('[name=print_waste_rate]').inputValue(),'11');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[]);console.log(`PASS ${name}: Rewrite carried to Inherit Plus, collisions warned/rekeyed, Cancel and repeat switching safe, remove/save/reopen sparse`);
    await context.close();
  }
}finally{await browser.close();}
