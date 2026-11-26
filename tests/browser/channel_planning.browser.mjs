// Uses the existing local catalogue_bom_fixture.py; all outbound HTTP blocked.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
  for(const[name,viewport]of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
    const seed=await(await context.request.get(origin+'/fixture')).json();
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
    await page.goto(origin+'/production/costing/settings');
    assert.equal(await page.locator('[name=channel_etsy_uplift_percent]').inputValue(),'15');
    assert.equal(await page.locator('[name=channel_etsy_minimum_uplift]').inputValue(),'1');
    assert.equal(await page.locator('[name=channel_shopify_rounding]').inputValue(),'49_99');
    assert.equal(await page.locator('[name=channel_payment_fixed]').inputValue(),'0.20');
    await page.getByRole('button',{name:'Save Channel Pricing & Fees',exact:true}).click();
    await page.waitForURL(origin+'/production/costing/settings');
    await page.reload();assert.equal(await page.locator('[name=channel_regulatory_percent]').inputValue(),'0.48');
    await page.goto(origin+'/production/bom');
    await page.getByText('Load Existing Product — independent what-if copy',{exact:true}).click();
    await page.getByLabel('Find product by name or SKU',{exact:true}).fill('Blue Token');
    await page.getByRole('button',{name:'Search Products',exact:true}).click();
    await page.locator(`a[href*="select_product=${seed.pid}"]`).click();
    await page.locator(`a[href*="load_product=${seed.pid}"]`).first().click();
    await page.getByText(/Independent copy of/).waitFor();
    const comparison=page.locator('[data-channel-comparison]');
    await comparison.locator('tbody tr').filter({hasText:'Etsy'}).first().waitFor();
    assert.match(await comparison.innerText(),/Shopify[\s\S]*Etsy/);
    assert.equal(await page.locator('[data-bom-rows=inventory] tbody tr').count(),2);
    assert.equal(await page.locator('[data-bom-rows=pack] tbody tr').count(),1);
    assert.equal(await page.locator('[name=labour_minutes]').inputValue(),'2');
    assert.equal(await page.locator('[name=price_rounding]').inputValue(),'');
    const material=page.locator('[data-bom-rows=inventory] tbody tr').filter({has:page.locator(`[name=item_id] option[value="${seed.items[0]}"]:checked`)});
    await material.getByRole('button',{name:'Edit Material / Component',exact:true}).click();
    await page.getByRole('dialog').locator('[name=quantity]').fill('30');
    await page.getByRole('dialog').getByRole('button',{name:'Apply Row',exact:true}).click();
    await page.locator('[name=etsy_price]').fill('15.99');
    await comparison.locator('td[data-column="Selling price"]').filter({hasText:'£15.99'}).waitFor();
    await page.locator('[name=name]').fill('Channel what-if '+name);
    await page.getByRole('button',{name:'Save Local Scenario',exact:true}).click();
    await page.waitForURL(/\/production\/bom\/\d+$/);
    assert.equal(await page.locator('[name=etsy_price]').inputValue(),'15.99');
    assert.equal(await material.locator('[name=quantity]').inputValue(),'30');
    await comparison.scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await comparison.screenshot({path:`/tmp/channel-comparison-${name}.png`});
    // Reload the source in the collection editor: the experiment never changed it.
    await page.goto(`${origin}/collections/${seed.cid}/metadata`);
    const scope=page.locator('[data-catalogue-costing]');
    await scope.locator('[data-channel-comparison]').waitFor();
    assert.equal(await scope.locator('[data-bom-rows=inventory] tbody tr').filter({has:page.locator(`[name=item_id] option[value="${seed.items[0]}"]:checked`)}).locator('[name=quantity]').inputValue(),'34');
    await page.goto(`${origin}/edit_products/${name==='mobile'?seed.mobile_pid:seed.pid}/edit/override`);
    await scope.locator('[data-channel-comparison]').waitFor();
    assert.equal(await scope.locator('[data-product-bom-mode]').inputValue(),'inherit');
    assert.equal(await scope.locator('[name=etsy_price]').isDisabled(),true);
    if(name==='mobile')assert.equal(await page.locator('.mobile-bottom-nav').evaluate(el=>getComputedStyle(el).position),'fixed');
    assert.deepEqual(errors,[]);console.log(`PASS ${name}: settings persistence, independent product copy/edit/save, channel comparisons in all editors, inheritance, source preserved`);
    await context.close();
  }
}finally{await browser.close();}
