// Read-only rendering against tests/browser/organisation_fixture.py only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin=process.env.TLC_PROMOTIONS_ORIGIN || 'http://127.0.0.1:7503';
const out=process.env.TLC_BROWSER_OUTPUT || '/tmp/tlc-promotions-m22';
mkdirSync(out,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.TLC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try {
  for (const [name,viewport] of [['desktop',{width:1440,height:900}],['tablet',{width:1024,height:900}],['phone',{width:390,height:844}]]) {
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      return url.origin===origin && ['GET','HEAD'].includes(route.request().method()) ? route.continue() : route.abort();
    });
    const page=await context.newPage();
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    for (const path of ['/promotions/','/promotions/new']) {
      const response=await page.goto(origin+path,{waitUntil:'domcontentloaded'});
      assert.equal(response.status(),200,`${name}${path}`);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${name}${path} page overflow`);
      const panels=await page.locator('.table-panel').count();
      assert.ok(panels>0,`${name}${path} shared panels`);
      if (path==='/promotions/' && process.env.TLC_PROMOTIONS_ORIGIN) {
        const table=page.locator('.table-panel .table-responsive table');
        assert.equal(await table.locator('tbody tr').count(),1,`${name} saved promotion row`);
        if (name==='phone') assert.ok(await table.evaluate(node=>node.parentElement.scrollWidth>node.parentElement.clientWidth),'phone listing scrolls inside its panel');
        await page.screenshot({path:`${out}/${name}-listing.png`,fullPage:false});
      }
    }
    const controls=await page.locator('[data-promotion-editor] .guided-field input:not([type="hidden"]), [data-promotion-editor] .guided-field select').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>n.getBoundingClientRect().height));
    assert.ok(controls.length>12 && controls.every(height=>Math.abs(height-44)<1),`${name} control heights ${controls}`);
    assert.equal(await page.locator('[data-tier-row]').count(),4,`${name} default tier rows`);
    await page.locator('[data-add-tier]').click();
    assert.equal(await page.locator('[data-tier-row]').count(),5,`${name} add tier`);
    const qualifying=page.locator('[data-promotion-targets="targets"]');
    await qualifying.locator('[data-target-search]').fill('Collection');
    await page.waitForTimeout(450);
    assert.ok(await qualifying.locator('[data-target-results] button').count()>=2,`${name} multiple local collections available`);
    await qualifying.locator('[data-target-results] button').first().click();
    await qualifying.locator('[data-target-results] button').nth(1).click();
    assert.equal(await qualifying.locator('[data-target-chosen] input[name="targets"]').count(),2,`${name} selected collections`);
    await qualifying.locator('[data-eligibility-mode]').selectOption('products');
    await page.waitForTimeout(450);
    assert.equal(await qualifying.locator('[data-target-results] button').count(),0,`${name} products view hides collection search results`);
    assert.equal(await qualifying.locator('[data-target-chosen] input[name="targets"]').count(),2,`${name} view change retains selections`);
    const exclusions=page.locator('[data-promotion-targets="exclusions"]');
    await exclusions.locator('[data-target-search]').fill('Blue');
    await page.waitForTimeout(450);
    assert.equal(await exclusions.locator('[data-target-results] button').count(),1,`${name} product exclusion search`);
    await exclusions.locator('[data-target-results] select').selectOption('0');
    await exclusions.locator('[data-target-results] button').click();
    assert.equal(await exclusions.locator('[data-target-chosen] input[name="exclusions"]').count(),1,`${name} selected exclusion`);
    await page.screenshot({path:`${out}/${name}-editor.png`,fullPage:false});
    if (process.env.TLC_PROMOTIONS_ORIGIN) {
      const review=await page.goto(origin+'/promotions/'+'f'.repeat(32)+'/review');
      assert.equal(review.status(),200,`${name} local review`);
      assert.ok(await page.locator('.table-panel-header').count()>0,`${name} shared review panels`);
      assert.equal(await page.locator('input[name="approval"]').count(),0,`${name} disconnected fixture cannot approve a write`);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${name} review overflow`);
      await page.screenshot({path:`${out}/${name}-review.png`,fullPage:false});
    }
    assert.deepEqual(errors,[],`${name} JavaScript errors`);
    await context.close();
  }
  console.log('Promotions list/editor: desktop, tablet and phone rendering passed; multiple collections selected; no writes.');
} finally { await browser.close(); }
