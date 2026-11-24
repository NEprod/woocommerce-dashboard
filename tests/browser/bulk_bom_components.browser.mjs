// Existing disposable catalogue_bom_fixture.py; no inventory or Shopify writes.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516',label='Add Multiple Materials / Components';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
  for(const[name,viewport]of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
    const page=await context.newPage(),errors=[];let answers=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>answers.shift()===false?d.dismiss():d.accept());
    const seed=await(await context.request.get(origin+'/fixture')).json();
    const scope=page.locator('[data-catalogue-costing]'),rows=()=>scope.locator('[data-bom-rows=inventory] tbody tr');
    const open=async()=>{await scope.getByRole('button',{name:label,exact:true}).click();return page.getByRole('dialog');};
    const choose=async dialog=>{for(const id of seed.items.slice(0,2))await dialog.locator(`[data-bulk-choice][value="${id}"]`).check();};
    const fill=async dialog=>{
      const fields=dialog.locator('fieldset');
      await fields.nth(0).locator('[name=quantity]').fill('3');await fields.nth(0).locator('[name=unit]').selectOption('g');
      await fields.nth(0).locator('[name=print_material]').selectOption('yes');
      await fields.nth(1).locator('[name=quantity]').fill('2');await fields.nth(1).locator('[name=unit]').selectOption('piece');
    };
    await page.goto(`${origin}/collections/${seed.cid}/metadata`);await scope.getByRole('button',{name:label,exact:true}).waitFor();
    const before=await rows().count();
    let dialog=await open();await choose(dialog);await dialog.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await rows().count(),before);
    dialog=await open();await choose(dialog);await page.keyboard.press('Escape');assert.equal(await rows().count(),before);
    dialog=await open();await choose(dialog);await fill(dialog);
    // One invalid unit must block the entire group, not just its own row.
    await dialog.locator('fieldset').nth(1).locator('[name=unit]').selectOption('g');
    await dialog.locator('fieldset').nth(1).getByRole('status').filter({hasText:/convert|compatible|unit/i}).waitFor();
    assert.equal(await dialog.getByRole('button',{name:'Apply All',exact:true}).isDisabled(),true);assert.equal(await rows().count(),before);
    await dialog.locator('fieldset').nth(1).locator('[name=unit]').selectOption('piece');
    await page.screenshot({path:`/tmp/bulk-bom-${name}.png`,fullPage:true});
    answers=[true,false];await dialog.getByRole('button',{name:'Apply All',exact:true}).click();
    await dialog.getByRole('status').filter({hasText:'No rows added. Review'}).waitFor();assert.equal(await rows().count(),before);
    await dialog.getByRole('button',{name:'Apply All',exact:true}).click();await dialog.waitFor({state:'detached'});
    assert.equal(await rows().count(),before+2);
    assert.equal(await rows().nth(before).locator('[name=quantity]').inputValue(),'3');
    assert.equal(await rows().nth(before+1).locator('[name=quantity]').inputValue(),'2');
    assert.equal(await rows().nth(before).locator('[name=print_material]').inputValue(),'yes');
    await page.locator('[data-acknowledge-shared]:visible').first().check();
    let saved=page.waitForResponse(r=>r.url().includes('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]').first().click();assert.equal((await saved).status(),200);
    await page.reload();await scope.getByRole('button',{name:label,exact:true}).waitFor();assert.equal(await rows().count(),before+2);
    // Both writable override modes use the same modal; Inherit All has no Add.
    await page.goto(`${origin}/edit_products/${name==='mobile'?seed.mobile_pid:seed.pid}/edit/override`);
    const mode=scope.locator('[data-product-bom-mode]');await mode.waitFor();
    assert.equal(await mode.inputValue(),'inherit');assert.equal(await scope.getByRole('button',{name:label,exact:true}).count(),0);
    for(const value of ['add','replace']){
      await mode.selectOption(value);await scope.getByRole('button',{name:label,exact:true}).waitFor();
      dialog=await open();await choose(dialog);await fill(dialog);await dialog.getByRole('button',{name:'Apply All',exact:true}).click();await dialog.waitFor({state:'detached'});
      assert.equal(await rows().filter({has:page.locator('[name=item_id]')}).count(),value==='add'?before+4:2);
      saved=page.waitForResponse(r=>r.url().includes('/save')&&r.request().method()==='POST');
      await page.locator('[data-save-metadata]').first().click();assert.equal((await saved).status(),200);
      await page.reload();await scope.getByRole('button',{name:label,exact:true}).waitFor();assert.equal(await mode.inputValue(),value);
      assert.equal(await scope.locator('[data-bom-rows=inventory] tbody tr:not([data-inherited])').count(),2);
    }
    await page.goto(origin+'/production/bom');assert.equal(await page.getByRole('button',{name:label,exact:true}).count(),0);
    assert.deepEqual(errors,[]);console.log(`PASS ${name}: bulk validation, Cancel/Escape, atomic duplicate review, values, collection/add/replace save/reopen, Inherit All and standalone unchanged`);
    await context.close();
  }
}finally{await browser.close();}
