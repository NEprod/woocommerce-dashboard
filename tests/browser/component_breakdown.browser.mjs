// Uses the existing disposable, offline catalogue fixture on loopback only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try {
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const seed=await (await context.request.get(origin+'/fixture')).json();
    const page=await context.newPage();
    await page.goto(`${origin}/production/item/${seed.items[1]}`);
    await page.getByRole('heading',{name:'One good component',exact:true}).waitFor();
    assert.match(await page.locator('main').innerText(),/0\.15389/);
    assert.match(await page.locator('main').innerText(),/Whole batch \(4 good components\)/);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No component-page horizontal overflow');
    await page.screenshot({path:`/tmp/component-breakdown-${name}.png`,fullPage:true});
    for(const url of [`/collections/${seed.cid}/metadata`,`/edit_products/${seed.pid}/edit/override`]){
      await page.goto(origin+url);
      const workspace=page.locator('[data-catalogue-costing]');
      await workspace.getByRole('heading',{name:'Production cost breakdown',exact:true}).waitFor();
      const text=await workspace.innerText();
      for(const label of ['Manufactured components — base recipe cost','Manufactured-component material waste allowance','Production electricity','Hands-on labour','Packaging'])assert.ok(text.includes(label),label);
      const summary=workspace.locator('.bom-cost-summary');
      assert.ok(await summary.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Summary amounts stay visible without horizontal scrolling');
      await workspace.screenshot({path:`/tmp/component-breakdown-${name}-${url.includes('collections')?'collection':'override'}.png`});
    }
    console.log(`PASS ${name}: component batch/piece totals and collection/override breakdowns`);
    await context.close();
  }
} finally {await browser.close();}
