// Disposable royal_mail_fixture only. Never a real Royal Mail account.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BINARY || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const results=[];
try {
  for(const [layout,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
    const context=await browser.newContext({viewport,acceptDownloads:true});
    await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:7524/edit_products/1/edit/shared');
    const field=page.locator('#metadata-customs_description');
    assert.equal(await field.inputValue(),'Greeting card');
    await field.fill('Wooden ornament');await page.locator('[data-acknowledge-shared]').check();
    const saved=page.waitForResponse(r=>r.url().endsWith('/save')&&r.request().method()==='POST');
    await page.locator('[data-save-metadata]:visible').last().click();assert.equal((await saved).status(),200);
    await page.getByText('Local scan completed',{exact:true}).waitFor({timeout:20000});
    await page.reload();assert.equal(await field.inputValue(),'Wooden ornament');
    await field.scrollIntoViewIfNeeded();await page.screenshot({path:`/tmp/tlc-royal-mail-editor-${layout}.png`});
    await page.goto('http://127.0.0.1:7524/packaging/');
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'Export Royal Mail CSV'}).click();
    const file=await download;assert.match(file.suggestedFilename(),/^tlc-royal-mail-products-\d{4}-\d{2}-\d{2}\.csv$/);
    const content=await fs.readFile(await file.path(),'utf8');
    assert.ok(content.startsWith('SKU,Product Name,Price (GBP),Weight (KG),Customs Description,HS Code,Non-standardised Manufacturer ID,Country of Origin\r\n'));
    assert.ok(content.includes('Wooden ornament,001234'));
    assert.ok(!content.includes('NBNOC-0001'));
    await page.locator('[data-export-feedback]').getByText(/exported successfully/).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`/tmp/tlc-royal-mail-export-${layout}.png`});
    await page.route('**/packaging/royal-mail.csv',r=>r.fulfill({status:409,contentType:'application/json',body:'{"error":"Local catalogue refresh is in progress"}'}));
    await page.getByRole('button',{name:'Export Royal Mail CSV'}).click();
    await page.locator('[data-export-feedback]').getByText(/export failed.*refresh is in progress/).waitFor();
    assert.deepEqual(errors,[]);
    // Restore for the next layout through the same local Save route.
    await page.goto('http://127.0.0.1:7524/edit_products/1/edit/shared');await field.fill('Greeting card');
    await page.locator('[data-acknowledge-shared]').check();
    await page.locator('[data-save-metadata]:visible').last().click();
    await page.getByText('Local scan completed',{exact:true}).waitFor({timeout:20000});
    results.push({layout,saveScopedScanReload:true,download:true,draftExcluded:true,successAndFailureFeedback:true,noOverflow:true,consoleErrors:errors});
    await context.close();
  }
  console.log(JSON.stringify(results));
} finally {await browser.close();}
