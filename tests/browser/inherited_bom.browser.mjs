// Disposable catalogue fixture: inherited rows are visible, never local data.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try {
  for(const [name,viewport]of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
    const seed=await(await context.request.get(origin+'/fixture')).json(),pid=name==='mobile'?seed.mobile_pid:seed.pid;
    await page.goto(`${origin}/edit_products/${pid}/edit/override`);
    const scope=page.locator('[data-catalogue-costing]'),mode=scope.locator('[data-product-bom-mode]');
    const inherited=kind=>scope.locator(`[data-bom-rows=${kind}] tbody tr[data-inherited]`);
    const local=kind=>scope.locator(`[data-bom-rows=${kind}] tbody tr:not([data-inherited])`);
    await scope.locator('[data-catalogue-results] .table-panel').first().waitFor();
    assert.equal(await mode.inputValue(),'inherit');
    for(const[k,n]of [['inventory',2],['packaging',1],['process',1]]){
      assert.equal(await inherited(k).count(),n);assert.equal(await inherited(k).first().isVisible(),true);
      assert.equal(await inherited(k).locator('button').count(),0);
      assert.equal(await inherited(k).locator('input:not(:disabled),select:not(:disabled)').count(),0);
      assert.equal(await local(k).count(),0);
    }
    assert.equal(await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).count(),0);
    assert.equal(await scope.locator('[name=labour_minutes]').inputValue(),'2');
    assert.equal(await scope.locator('[name=labour_minutes]').isDisabled(),true);
    await inherited('packaging').locator('[data-inventory-total]').getByText(/£/).waitFor();
    await mode.selectOption('add');await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).waitFor();
    assert.equal(await inherited('inventory').count(),2);assert.equal(await inherited('packaging').count(),1);
    assert.equal(await scope.locator('[data-inherited-value=labour_minutes]').innerText(),'Inherited: 2 (read-only)');
    await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).click();
    const modal=page.getByRole('dialog');await modal.locator('[name=item_id]').selectOption(String(seed.items[2]));
    await modal.locator('[name=quantity]').fill('2');await modal.locator('[name=unit]').selectOption('piece');
    await modal.getByRole('button',{name:'Apply Row',exact:true}).click();
    assert.equal(await inherited('packaging').count(),1);assert.equal(await local('packaging').count(),1);
    assert.equal(await local('packaging').getByRole('button',{name:'Remove',exact:true}).count(),1);
    // Trigger a second serialization; inherited rows must not become additions.
    await scope.locator('[name=labour_minutes]').fill('1');
    const saved=page.waitForResponse(r=>r.url().includes('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]').first().click();const response=await saved;
    assert.equal(response.status(),200);
    const data=response.request().postDataJSON().data.production;
    assert.equal(data.mode,'add');assert.equal(Object.keys(data.lines).length,1);
    assert.equal(Object.values(data.lines)[0].quantity,'2');assert.equal(data.costing.equipment.length,0);
    assert.equal(data.costing.labour_minutes,'1');
    await page.reload();await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).waitFor();
    assert.equal(await inherited('packaging').count(),1);assert.equal(await local('packaging').count(),1);
    await scope.locator('[data-catalogue-results] .table-panel').first().waitFor();
    await page.screenshot({path:`/tmp/inherited-bom-${name}.png`,fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(name==='mobile')assert.equal(await page.locator('.mobile-bottom-nav').evaluate(el=>getComputedStyle(el).position),'fixed');
    await mode.selectOption('replace');await scope.getByRole('button',{name:'Add Packaging Item',exact:true}).waitFor();
    assert.equal(await scope.locator('tr[data-inherited]').count(),0);
    assert.equal(await local('packaging').count(),0);
    assert.deepEqual(errors,[]);console.log(`PASS ${name}: Inherit All read-only sections; Inherit Plus alongside additions; sparse save/reopen; Rewrite clean`);
    await context.close();
  }
}finally{await browser.close();}
