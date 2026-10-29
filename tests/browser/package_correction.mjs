import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const output=process.env.TLC_BROWSER_EVIDENCE || '/private/tmp/tlc-package-correction-browser';
fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.TLC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try {
  for(const [label,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
    const context=await browser.newContext({viewport,serviceWorkers:'block'});
    await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    assert.equal((await page.goto('http://127.0.0.1:7540/products/1/shopify/package-correction')).status(),200);
    await page.getByRole('button',{name:'Check identity and review package — reads only'}).click();
    await page.getByRole('heading',{name:'Fresh package-only approval'}).waitFor();
    assert.match(await page.locator('body').innerText(),/Fictional mailer 123/);
    assert.match(await page.locator('body').innerText(),/Shopify Accepted.*not API Verified/s);
    const approval=page.locator('input[name="acknowledge"]');
    assert.equal(await approval.isChecked(),false);await approval.check();
    assert.equal(await page.getByRole('button',{name:'Approve package correction once'}).isEnabled(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:output+'/'+label+'.png',fullPage:true});
    assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: desktop/mobile local-only preview, exact identity/package display, fresh approval control, no overflow or browser errors. No live browser tabs or Shopify used.');
} finally {await browser.close();}
