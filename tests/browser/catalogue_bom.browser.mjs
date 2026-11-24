import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-catalogue-bom',{recursive:true});
try {
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
    page.on('dialog',dialog=>dialog.accept());
    const seed=await (await context.request.get(origin+'/fixture')).json();
    if(name==='mobile')seed.pid=seed.mobile_pid;
    await page.goto(`${origin}/collections/${seed.cid}/metadata`);
    const workspace=page.locator('[data-catalogue-costing]');
    await workspace.getByRole('button',{name:'Add Production Process',exact:true}).waitFor();
    await workspace.locator('[data-catalogue-results] .table-panel').first().waitFor();
    assert.equal(await workspace.locator('table[data-bom-rows=inventory] tbody tr').count(),2);
    assert.equal(await workspace.locator('table[data-bom-rows=packaging] tbody tr').count(),1);
    assert.equal(await workspace.locator('table[data-bom-rows=process] tbody tr').count(),1);
    assert.equal(await workspace.locator('[name=manual_name]').count(),0);
    const count=()=>workspace.locator('table[data-bom-rows] tbody tr').count();const before=await count();
    for(const label of ['Add Material / Component','Add Packaging Item','Add Production Process']){
      await workspace.getByRole('button',{name:label,exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();
      assert.equal(await count(),before);
      await workspace.getByRole('button',{name:label,exact:true}).click();await page.keyboard.press('Escape');assert.equal(await count(),before);
    }
    assert.equal(await page.locator('[data-save-state]').first().innerText(),'Saved');
    await workspace.locator('[data-catalogue-advanced]').evaluate(el=>el.open=true);
    await workspace.locator('[name=markup]').fill('-1');
    await workspace.getByRole('status').filter({hasText:'non-negative'}).waitFor();
    await workspace.locator('[name=markup]').fill('100');await workspace.locator('[data-catalogue-results] .table-panel').first().waitFor();assert.equal(await count(),before);
    await workspace.locator('[data-bom-rows=process]').getByRole('button',{name:'Edit Production Process',exact:true}).click();
    let modal=page.getByRole('dialog');assert.equal(await modal.locator('[name=runtime_hours]').inputValue(),'4');assert.equal(await modal.locator('[name=runtime_minutes]').inputValue(),'10');
    assert.equal(await modal.locator('[name=usable_units]').isVisible(),false);
    await modal.locator('[name=runtime_hours]').fill('5');await modal.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await workspace.locator('[name=minutes]').inputValue(),'250');
    await workspace.getByRole('button',{name:'Add Material / Component',exact:true}).click();modal=page.getByRole('dialog');
    await modal.locator('[name=item_id]').selectOption(String(seed.items[0]));await modal.locator('[name=quantity]').fill('2');await modal.locator('[name=unit]').selectOption('g');
    await modal.getByRole('button',{name:'Apply Row',exact:true}).click();assert.equal(await count(),before+1);
    await workspace.locator('[data-bom-rows=inventory] tbody tr').last().getByRole('button',{name:'Remove',exact:true}).click();assert.equal(await count(),before);
    // Import preview/cancel changes neither working rows nor JSON.
    await workspace.locator('[data-import-scenario]').selectOption(String(seed.blocked));await workspace.getByRole('button',{name:'Import Saved Test BOM',exact:true}).click();
    modal=page.getByRole('dialog');await modal.getByRole('button',{name:'Apply Import',exact:true}).waitFor();assert.equal(await modal.getByRole('button',{name:'Apply Import',exact:true}).isDisabled(),true);assert.match(await modal.innerText(),/hypothetical/i);await modal.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await count(),before);
    await workspace.locator('[data-import-scenario]').selectOption(String(seed.scenario));await workspace.getByRole('button',{name:'Import Saved Test BOM',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await count(),before);
    await workspace.getByRole('button',{name:'Import Saved Test BOM',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Apply Import',exact:true}).click();
    await workspace.getByRole('button',{name:'Add Production Process',exact:true}).waitFor();assert.equal(await count(),before);
    // Explicit Save Metadata is the only persistence operation.
    await page.locator('[data-acknowledge-shared]:visible').first().check();
    const saved=page.waitForResponse(r=>r.url().includes('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]').first().click();assert.equal((await saved).status(),200);
    await page.reload();await workspace.getByRole('button',{name:'Add Production Process',exact:true}).waitFor();assert.equal(await count(),before);
    await workspace.locator('[data-catalogue-results] .table-panel').first().waitFor();
    assert.equal(await workspace.locator('[name=print_waste_rate]').inputValue(),'11');
    assert.equal(await workspace.locator('[name=markup]').inputValue(),'100');
    assert.equal(await workspace.locator('[name=labour_minutes]').inputValue(),'2');
    await workspace.screenshot({path:`/tmp/tlc-catalogue-bom/${name}-collection-bom.png`});
    await page.screenshot({path:`/tmp/tlc-catalogue-bom/${name}-collection.png`,fullPage:true});
    await page.goto(`${origin}/edit_products/${seed.pid}/edit/override`);
    await workspace.locator('[data-product-bom-mode]').waitFor();assert.equal(await workspace.locator('[data-product-bom-mode]').inputValue(),'inherit');
    assert.equal(await workspace.getByRole('button',{name:'Import Saved Test BOM',exact:true}).isDisabled(),true);
    assert.equal(await workspace.getByRole('button',{name:'Add Material / Component',exact:true}).count(),0);
    await workspace.locator('[data-product-bom-mode]').selectOption('add');await workspace.getByRole('button',{name:'Add Material / Component',exact:true}).waitFor();
    assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr[data-inherited]').count(),2);
    assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr:not([data-inherited])').count(),0);
    await workspace.getByRole('button',{name:'Add Material / Component',exact:true}).click();modal=page.getByRole('dialog');await modal.locator('[name=item_id]').selectOption(String(seed.items[1]));await modal.locator('[name=quantity]').fill('1');await modal.locator('[name=unit]').selectOption('piece');await modal.getByRole('button',{name:'Apply Row',exact:true}).click();
    assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr:not([data-inherited])').count(),1);
    await workspace.locator('[data-product-bom-mode]').selectOption('inherit');await workspace.locator('[data-product-bom-mode]').waitFor();
    await workspace.locator('[data-product-bom-mode]').selectOption('add');await workspace.getByRole('button',{name:'Add Material / Component',exact:true}).waitFor();assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr:not([data-inherited])').count(),1);
    await workspace.locator('[data-product-bom-mode]').selectOption('replace');await workspace.getByRole('button',{name:'Add Material / Component',exact:true}).waitFor();assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr').count(),0);
    await workspace.locator('[data-import-scenario]').selectOption(String(seed.scenario));await workspace.getByRole('button',{name:'Import Saved Test BOM',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Apply Import',exact:true}).click();await workspace.getByRole('button',{name:'Add Production Process',exact:true}).waitFor();
    assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr').count(),2);
    const productSave=page.waitForResponse(r=>r.url().includes('/save')&&r.request().method()==='POST');await page.locator('[data-save-metadata]').first().click();assert.equal((await productSave).status(),200);
    await page.reload();await workspace.getByRole('button',{name:'Add Production Process',exact:true}).waitFor();assert.equal(await workspace.locator('[data-product-bom-mode]').inputValue(),'replace');assert.equal(await workspace.locator('[data-bom-rows=inventory] tbody tr').count(),2);
    await workspace.locator('[data-catalogue-results] .table-panel').first().waitFor();
    await workspace.screenshot({path:`/tmp/tlc-catalogue-bom/${name}-override-bom.png`});
    await workspace.scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    if(name==='mobile'){assert.equal(await page.locator('.mobile-bottom-nav').evaluate(el=>getComputedStyle(el).position),'fixed');}
    await page.screenshot({path:`/tmp/tlc-catalogue-bom/${name}-override.png`,fullPage:true});
    assert.deepEqual(errors,[]);await context.close();console.log(`${name}: shared row modals, hours/minutes, import cancel/apply/rejection, mode drafts, JSON save/reopen, responsive navigation PASS`);
  }
}finally{await browser.close();}
