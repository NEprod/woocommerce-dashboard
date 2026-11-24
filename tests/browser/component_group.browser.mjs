// Existing offline catalogue fixture; no live inventory or Shopify access.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
  for(const [name,viewport]of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const seed=await (await context.request.get(origin+'/fixture')).json();
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/production/components/new-group');
    await page.getByLabel('Group name — e.g. Gingerbread Man',{exact:true}).fill(`Gingerbread Man ${name}`);
    await page.getByLabel('Default expected good pieces per batch',{exact:true}).fill('10');
    const rows=page.locator('[data-component-table] tbody tr');
    await page.getByRole('button',{name:'Add Component',exact:true}).click();
    await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await rows.count(),0);
    await page.getByRole('button',{name:'Add Component',exact:true}).click();await page.keyboard.press('Escape');assert.equal(await rows.count(),0);
    for(const [part,qty]of [['Eyes','13'],['Feet','2']]){
      await page.getByRole('button',{name:'Add Component',exact:true}).click();const modal=page.getByRole('dialog');
      await modal.getByLabel('Component name — e.g. Eyes',{exact:true}).fill(part);
      await modal.getByLabel('Unique component barcode',{exact:true}).fill(`GB-${name}-${part}`);
      await modal.getByLabel('Raw material',{exact:true}).selectOption(String(seed.items[0]));
      await modal.getByLabel('Quantity per good component',{exact:true}).fill(qty);
      assert.match(await modal.innerText(),new RegExp(`Total expected batch material: ${Number(qty)*10} g`));
      await modal.getByRole('button',{name:'Apply Component',exact:true}).click();
    }
    assert.equal(await rows.count(),2);
    await page.getByRole('button',{name:'Edit Eyes',exact:true}).click();
    await page.getByRole('dialog').getByLabel('Component name — e.g. Eyes',{exact:true}).fill('Wrong name');
    await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();assert.doesNotMatch(await rows.first().innerText(),/Wrong name/);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(name==='mobile')assert.ok(await page.locator('[data-component-table]').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Components use existing mobile BOM cards without sideways scrolling');
    await page.screenshot({path:`/tmp/component-group-${name}.png`,fullPage:true});
    await page.getByRole('button',{name:'Create separate components',exact:true}).click();
    await page.waitForURL(/\/production\/items\/components/);
    assert.match(await page.locator('main').innerText(),/2 separate manufactured components created/);
    assert.equal(await page.locator('[data-component-card]').count(),2);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:`/tmp/component-cards-${name}.png`,fullPage:true});
    await page.locator('[data-component-card] h2 a').first().click();
    assert.match(await page.locator('main').innerText(),/Total on hand: 0/);
    await page.getByRole('link',{name:'Edit item',exact:true}).click();assert.equal(await page.locator('[name=material_qty]').inputValue(),'13.000000');
    await page.goto(origin+'/production/items/materials');
    assert.ok(await page.locator('[data-material-card]').count()>0);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:`/tmp/material-cards-${name}.png`,fullPage:true});
    await page.locator('[data-material-card] .collection-card-media').first().click();
    assert.match(page.url(),/\/production\/item\/\d+$/);
    assert.deepEqual(errors,[]);
    console.log(`PASS ${name}: grouped entry, live batch totals, transactional modals, separate items and single-item editor`);
    await context.close();
  }
}finally{await browser.close();}
