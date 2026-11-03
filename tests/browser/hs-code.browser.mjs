// Local hs_code_fixture only, never the merchant's browser or catalogue.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BINARY || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const results=[];
try {
  for (const [label,viewport,id,code] of [['desktop',{width:1440,height:1000},1,'001234'],['mobile',{width:390,height:844},2,'0012345678901']]) {
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(`http://127.0.0.1:7523/edit_products/${id}/edit/override`);
    const hs=page.locator('#metadata-harmonized_system_code');
    const toggle=page.locator('[data-override-toggle="harmonized_system_code"]');
    assert.equal(await hs.inputValue(),'490900');assert.equal(await hs.isDisabled(),true);
    assert.equal(await hs.getAttribute('type'),'text');assert.equal(await hs.getAttribute('inputmode'),'numeric');
    await toggle.check();await hs.fill('not-a-code');
    const invalid=page.waitForResponse(r=>r.url().includes('/validate')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]:visible').last().click();await invalid;
    await page.locator('[data-field-error="harmonized_system_code"]').getByText(/6–13 digits/).waitFor();
    await hs.fill(code);
    const saved=page.waitForResponse(r=>r.url().endsWith('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]:visible').last().click();assert.equal((await saved).status(),200);
    await page.getByText('Local scan completed',{exact:true}).waitFor({timeout:20000});
    await page.reload();await hs.scrollIntoViewIfNeeded();
    assert.equal(await hs.inputValue(),code);assert.equal(await toggle.isChecked(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`/tmp/tlc-hs-${label}.png`});
    await toggle.uncheck();
    const removed=page.waitForResponse(r=>r.url().endsWith('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]:visible').last().click();assert.equal((await removed).status(),200);
    await page.getByText('Local scan completed',{exact:true}).waitFor({timeout:20000});
    await page.reload();assert.equal(await hs.inputValue(),'490900');assert.equal(await hs.isDisabled(),true);
    assert.deepEqual(errors,[]);
    results.push({layout:label,leadingZerosPreserved:true,validationClear:true,savedScopedScanReopened:true,inheritanceRestored:true,noOverflow:true,consoleErrors:errors});
    await context.close();
  }
  console.log(JSON.stringify(results));
} finally {await browser.close();}
