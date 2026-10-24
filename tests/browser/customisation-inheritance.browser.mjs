// Run only with customisation_inheritance_fixture.py. Never open a live tab.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
const results = [];
try {
  for (const [label, viewport, product] of [
    ['desktop-simple',{width:1440,height:1000},1],
    ['mobile-variable',{width:390,height:844},3],
    ['desktop-single-variable',{width:1440,height:1000},4]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    const page = await context.newPage();
    page.on('dialog', dialog => dialog.dismiss());
    const kind = product === 4 ? 'shared' : 'override';
    await page.goto(`http://127.0.0.1:7511/edit_products/${product}/edit/${kind}`);
    const designer = page.locator('[data-customisation-designer]');
    await designer.scrollIntoViewIfNeeded();
    const rows = designer.locator('[data-custom-fields] article');
    assert.equal(await rows.count(), 2);
    for (const row of await rows.all()) {
      assert.match(await row.innerText(), /Inherited/);
      assert.equal(await row.getByRole('button',{name:'Edit',exact:true}).count(), 0);
      assert.equal(await row.getByRole('button',{name:'Remove local field',exact:true}).count(), 0);
    }
    await designer.locator('[data-custom-add]').click();
    const modal = designer.locator('[data-custom-field-dialog]');
    await modal.locator('[data-custom-control="label"]').fill('Personal Message');
    await modal.locator('[data-custom-control="max_length"]').fill('200');
    await modal.locator('[data-custom-apply-field]').click();
    assert.equal(await rows.count(),3);
    assert.match(await rows.nth(2).innerText(),product===4?/Local field/:/Product addition/);
    assert.match(await rows.nth(0).innerText(),/Recipient Name/);
    assert.match(await rows.nth(1).innerText(),/Age/);
    assert.match(await page.locator('[data-save-state]').first().innerText(),/Unsaved/);
    await rows.nth(2).getByRole('button',{name:'Edit',exact:true}).click();
    await modal.locator('[data-custom-control="help_text"]').fill('Optional greeting');
    await modal.locator('[data-custom-apply-field]').click();
    // Removing a local addition never removes either template-owned field.
    await rows.nth(2).getByRole('button',{name:'Remove local field',exact:true}).click();
    assert.equal(await rows.count(),2);
    await designer.locator('[data-custom-add]').click();
    await modal.locator('[data-custom-control="label"]').fill('Personal Message');
    await modal.locator('[data-custom-apply-field]').click();
    if(await page.locator('[data-acknowledge-shared]').count())await page.locator('[data-acknowledge-shared]').check();
    const saved = page.waitForResponse(response=>response.url().endsWith('/save')&&response.request().method()==='POST');
    await page.locator('[data-save-metadata]:visible').last().click();
    const response = await saved;
    assert.equal(response.status(),200,await response.text());
    const projected = await context.request.post(`http://127.0.0.1:7511/fixture/rescan/${product}`);
    assert.equal(projected.status(),200,await projected.text());
    const state = await projected.json();
    assert.deepEqual(Object.keys(state.authored.customisation.fields),['personal_message']);
    if(product!==4)assert.equal(state.parent_unchanged,true);
    assert.equal(state.effective.fields.recipient.required,true);
    assert.equal(state.effective.fields.recipient.max_length,30);
    await page.reload();
    await designer.scrollIntoViewIfNeeded();
    assert.equal(await rows.count(),3);
    assert.match(await rows.nth(2).innerText(),/Personal Message/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(viewport.width<600){
      const buttonBox = await page.locator('[data-save-metadata]:visible').last().boundingBox();
      assert.ok(buttonBox && buttonBox.y>=0 && buttonBox.y+buttonBox.height<=viewport.height);
    }
    await page.screenshot({path:`/tmp/tlc-customisation-${label}.png`,fullPage:true});
    results.push({layout:label,readOnlyInherited:true,localAddEditRemove:true,sparseSaveReopenRescan:true,noOverflow:true});
    await context.close();
  }
  console.log(JSON.stringify(results));
} finally {await browser.close();}
