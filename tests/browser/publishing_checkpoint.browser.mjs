import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const output=process.env.TLC_BROWSER_OUTPUT;
assert.ok(output);fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try {
 for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
  const context=await browser.newContext({viewport,serviceWorkers:'block'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const [label,path] of [['tasks','/settings/tasks'],['events','/operations/events'],['queue','/publishing/queue'],['validation','/publishing/scan/1']]) {
   const response=await page.goto('http://127.0.0.1:7518'+path);assert.equal(response.status(),200,path);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,path);
   await page.screenshot({path:`${output}/${name}-${label}.png`,fullPage:true});
  }
  await page.goto('http://127.0.0.1:7518/operations/events?trigger=hourly');
  await page.getByRole('link',{name:'Automatic Shopify Publisher',exact:true}).click();
  await page.getByRole('heading',{name:'Saved eligibility at cycle start'}).waitFor();
  assert.match(await page.locator('body').innerText(),/missing checkpoint/i);
  await page.goto('http://127.0.0.1:7518/operations/events?trigger=reconciliation');
  assert.match(await page.locator('body').innerText(),/no publication or local scan/);
  await page.goto('http://127.0.0.1:7518/publishing/queue');await page.getByRole('link',{name:'Next',exact:true}).click();
  assert.match(await page.locator('body').innerText(),/30 products · page 2/);
  assert.deepEqual(errors,[]);console.log(name+': tasks, cycles, reconciliation, validation guidance, pagination and no overflow PASS');
  await context.close();
 }
} finally {await browser.close();}
