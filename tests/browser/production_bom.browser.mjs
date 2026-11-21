import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require = createRequire(import.meta.url);
const {chromium} = require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-production-layout', {recursive:true});
try {
  for (const [name, viewport] of [['desktop',{width:1440,height:900}],['tablet',{width:1024,height:900}],['phone',{width:390,height:844}]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => new URL(route.request().url()).origin === 'http://127.0.0.1:7513' ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors=[];page.on('pageerror', e=>errors.push(e.message));
    for (const [label,path] of [['catalogue','/production/catalogue'],['create','/production/batches/create'],['batch','/production/batch/1'],['label','/production/batch/1/label'],['complete','/production/batch/1/complete'],['settings','/production/costing/settings'],['planner','/production/bom'],['bundles','/production/bundles']]) {
      const response=await page.goto('http://127.0.0.1:7513'+path);
      assert.equal(response.status(),200);
      if (label === 'bundles') {
        assert.equal(new URL(page.url()).pathname,'/promotions/');
        const tools=page.locator('.app-sidebar [data-navigation-group="Tools"]');
        assert.equal(await tools.locator('a[href="/promotions/"]').count(),1);
        assert.equal(await tools.locator('a[href="/production/bom"]').count(),1);
        assert.equal(await page.locator('a[href="/production/bundles"]').count(),0);
      }
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' overflow: '+label);
      await page.screenshot({path:`/tmp/tlc-production-layout/${name}-${label}.png`,fullPage:true});
    }
    assert.deepEqual(errors,[]);await context.close();
    console.log(name+': batch planning, completion, costing settings and standalone planner layout PASS');
  }
} finally {await browser.close();}
