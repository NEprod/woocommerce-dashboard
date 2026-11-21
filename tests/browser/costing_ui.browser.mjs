import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-costing-ui',{recursive:true});
try {
 for(const [name,viewport] of [['desktop',{width:1440,height:900}],['phone',{width:390,height:844}]]){
  const context=await browser.newContext({viewport});
  await context.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:7513'?r.continue():r.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:7513/production/costing/settings');
  const original=await page.locator('tbody').first().innerText();
  await page.locator('[data-costing-dialog="equipment-modal"][data-record]').first().click();
  const equipment=page.locator('#equipment-modal');await equipment.locator('[name=name]').fill('Cancelled draft');
  await page.screenshot({path:`/tmp/tlc-costing-ui/${name}-equipment-modal.png`,fullPage:true});
  await equipment.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await page.locator('tbody').first().innerText(),original);
  if(name==='desktop'){
   await page.getByRole('button',{name:'Add Fee Profile',exact:true}).click();const fee=page.locator('#fee-modal');
   await fee.locator('[name=name]').fill('UI Online');await fee.locator('[name=percent]').fill('2');await fee.locator('[name=fixed]').fill('.25');await fee.getByRole('button',{name:'Save Fee Profile'}).click();
   await page.waitForFunction(()=>document.querySelector('tbody')&&document.body.textContent.includes('UI Online'));
   await page.getByRole('button',{name:'Edit fee profile UI Online',exact:true}).click();await page.locator('#fee-modal [name=percent]').fill('3');await page.locator('#fee-modal').getByRole('button',{name:'Save Fee Profile'}).click();
   await page.waitForFunction(()=>Array.from(document.querySelectorAll('tr')).some(r=>r.textContent.includes('UI Online')&&r.textContent.includes('3%')));
   await page.locator('input[name=tariff]').fill('.40');await page.locator('input[name=labour_rate]').fill('12');await page.getByRole('button',{name:'Save Defaults',exact:true}).click();await page.waitForLoadState('networkidle');
  }
  await page.goto('http://127.0.0.1:7513/production/bom');
  await page.locator('input[name=name]').fill('UI hypothetical scenario');
  await page.getByRole('button',{name:'Add Hypothetical Material',exact:true}).click();const modal=page.getByRole('dialog');
  await modal.locator('[name=manual_cost_mode]').selectOption('direct');
  await modal.locator('[name=manual_name]').fill('Test PLA');await modal.locator('[name=manual_quantity]').fill('50');await modal.locator('[name=manual_unit]').selectOption('g');await modal.locator('[name=manual_cost]').fill('.02');await modal.getByRole('button',{name:'Apply Row'}).click();
  await page.locator('#live-costing').getByText('Test PLA',{exact:true}).waitFor();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:`/tmp/tlc-costing-ui/${name}-planner.png`,fullPage:true});
  assert.deepEqual(errors,[]);await context.close();console.log(name+': modal cancel, dynamic hypothetical row and live costing PASS');
 }
}finally{await browser.close();}
