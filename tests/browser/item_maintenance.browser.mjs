import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7516';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
  for(const [index,name,viewport] of [[0,'desktop',{width:1440,height:1000}],[1,'mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const seed=await (await context.request.get(origin+'/maintenance-fixture')).json(),iid=seed.items[index];
    const page=await context.newPage();
    await page.goto(`${origin}/production/item/${iid}/edit`);
    assert.equal(await page.locator('#inventory-item-barcode').isEditable(),true);
    const corrected=`FIXED-${name.toUpperCase()}`;
    await page.locator('#inventory-item-barcode').fill(corrected);
    await page.locator('[name=confirm_barcode]').check();
    await page.getByRole('button',{name:'Save purchased component',exact:true}).click();
    await page.waitForURL(`${origin}/production/item/${iid}`);
    assert.match(await page.locator('main').innerText(),new RegExp(corrected));
    await page.getByRole('link',{name:'Edit item',exact:true}).click();
    await page.locator('#delete-item summary').click();
    await page.locator('[name=confirm_code]').fill(corrected);
    await page.locator('[name=confirmed]').check();
    await page.screenshot({path:`/tmp/item-maintenance-${name}.png`,fullPage:true});
    await page.getByRole('button',{name:'Delete unused item',exact:true}).click();
    await page.waitForURL(/\/production\/items\/components/);
    assert.match(await page.locator('main').innerText(),/Unused item deleted/);
    assert.equal((await context.request.get(`${origin}/production/item/${iid}`)).status(),404);
    console.log(`PASS ${name}: barcode correction and explicit unused-item deletion`);
    await context.close();
  }
}finally{await browser.close();}
