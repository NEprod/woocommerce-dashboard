import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-hypothetical-purchase',{recursive:true});
try{
 for(const [name,viewport]of [['desktop',{width:1440,height:900}],['phone',{width:390,height:844}]]){
  const context=await browser.newContext({viewport});
  await context.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:7513'?r.continue():r.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:7513/production/bom');await page.locator('input[name=name]').fill('Purchase basis test');
  await page.getByRole('button',{name:'Add Hypothetical Material',exact:true}).click();
  const modal=page.getByRole('dialog');assert.equal(await modal.locator('[name=manual_cost_mode]').inputValue(),'purchase');
  assert.equal(await modal.locator('[name=manual_purchase_unit]').inputValue(),'');
  await modal.locator('[name=manual_name]').fill('Red PLA');
  await modal.locator('[name=manual_purchase_cost]').fill('13.99');await modal.locator('[name=manual_purchase_quantity]').fill('1');
  await modal.locator('[name=manual_purchase_unit]').selectOption('kg');await modal.locator('[name=manual_quantity]').fill('26');await modal.locator('[name=manual_unit]').selectOption('g');
  await modal.getByRole('status').filter({hasText:'0.36374'}).waitFor();
  assert.match(await modal.getByRole('status').innerText(),/0.01399/);
  assert.ok(await modal.evaluate(el=>el.getBoundingClientRect().width<=innerWidth));
  await page.screenshot({path:`/tmp/tlc-hypothetical-purchase/${name}-modal.png`,fullPage:true});
  await modal.getByRole('button',{name:'Apply Row'}).click();
  const row=page.locator('table[data-hypothetical-preview] tbody tr').filter({hasText:'Red PLA'});
  await row.locator('[data-manual-total]').filter({hasText:'0.36374'}).waitFor();
  await row.getByRole('button',{name:'Edit Hypothetical Material',exact:true}).click();
  assert.equal(await modal.locator('[name=manual_purchase_cost]').inputValue(),'13.99');
  await modal.locator('[name=manual_quantity]').fill('52');await modal.getByRole('status').filter({hasText:'0.72748'}).waitFor();
  await modal.getByRole('button',{name:'Cancel',exact:true}).click();assert.match(await row.locator('[data-manual-total]').innerText(),/0.36374/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);await context.close();console.log(name+': purchase modal calculation, exact line total, edit/cancel and responsive layout PASS');
 }
}finally{await browser.close();}
