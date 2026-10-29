// Disposable customisation_editor_fixture.py only. Never a live catalogue/tab.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
const results=[];
try {
  for (const [label,viewport,id] of [
    ['desktop',{width:1440,height:1000},'dogs_name'],
    ['mobile',{width:390,height:844},'dogs_name_mobile']]) {
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
    const page=await context.newPage();page.on('dialog',dialog=>dialog.dismiss());
    await page.goto('http://127.0.0.1:7495/edit_products/1/edit/override');
    const designer=page.locator('[data-customisation-designer]');
    await designer.scrollIntoViewIfNeeded();
    assert.equal(await designer.locator('[data-custom-edit="name_on_card"]').count(),0);
    await designer.locator('[data-custom-add]').click();
    const modal=page.locator('[data-custom-field-dialog]');
    await modal.locator('[data-custom-control="id"]').fill('dogs-name');
    await modal.locator('[data-custom-control="label"]').fill("Dog's name");
    await modal.locator('[data-custom-apply-field]').click();
    assert.match(await modal.locator('[data-custom-dialog-error]').innerText(),/underscores.*dogs_name/);
    assert.match(await modal.innerText(),/No hyphens or spaces/);
    await page.screenshot({path:`/tmp/tlc-stable-id-${label}-validation.png`});
    await modal.locator('[data-custom-control="id"]').fill(id);
    await modal.locator('[data-custom-apply-field]').click();
    assert.equal(await modal.isVisible(),false);
    const row=designer.locator('article').filter({has:page.locator(`[data-custom-edit="${id}"]`)});
    assert.match(await row.innerText(),/Product addition/);
    const response=page.waitForResponse(r=>r.url().endsWith('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]:visible').last().click();
    assert.equal((await response).status(),200);
    await page.getByText('Local scan completed',{exact:true}).waitFor({timeout:20000});
    if(await page.locator('#scanModal').isVisible())await page.locator('#scanModal').getByRole('button',{name:'Done',exact:true}).click();
    await page.reload();await designer.scrollIntoViewIfNeeded();
    await designer.locator(`[data-custom-edit="${id}"]`).click();
    assert.equal(await modal.locator('[data-custom-control="id"]').inputValue(),id);
    assert.equal(await modal.locator('[data-custom-control="id"]').getAttribute('readonly'),'');
    await modal.locator('[data-custom-control="label"]').fill('Name for your dog');
    await modal.locator('[data-custom-apply-field]').click();
    assert.equal(await designer.locator('[data-custom-edit="name_on_card"]').count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`/tmp/tlc-stable-id-${label}-saved.png`});
    results.push({layout:label,hyphenRejectedClearly:true,underscoreAdded:true,signedSaveScopedScanReopen:true,inheritedProtected:true,editIdentityStable:true,noOverflow:true});
    await context.close();
  }
  console.log(JSON.stringify(results));
} finally {await browser.close();}
