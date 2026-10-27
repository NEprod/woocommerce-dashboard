import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const output=process.env.TLC_BROWSER_OUTPUT;
if(!output)throw new Error('Explicit disposable screenshot output required');
fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
const results=[];
try {
 for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
  const context=await browser.newContext({viewport,serviceWorkers:'block'});
  await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const [stage,port,pages] of [['before',7517,[['tasks','/settings/tasks'],['publishing','/settings/publishing']]],['after',7518,[['tasks','/settings/tasks'],['queue','/publishing/queue'],['events','/operations/events'],['settings','/settings/publishing']]]]) {
   for(const [label,path] of pages) {
    const response=await page.goto(`http://127.0.0.1:${port}${path}`);assert.equal(response.status(),200,path);
    await page.screenshot({path:`${output}/${stage}-${name}-${label}.png`});
    if(stage==='after')assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,path);
    results.push({stage,viewport:name,page:label,status:response.status(),height:await page.evaluate(()=>document.documentElement.scrollHeight)});
   }
  }
  await page.goto('http://127.0.0.1:7518/publishing/queue');
  await page.getByRole('link',{name:'Next',exact:true}).click();
  assert.match(await page.locator('body').innerText(),/30 products · page 2/);
  await page.goto('http://127.0.0.1:7518/operations/events?q=FIXTURE-029');
  assert.match(await page.locator('body').innerText(),/1 events/);
  await page.getByRole('link',{name:'Fixture Card 029',exact:true}).click();
  await page.getByText('Price: £4.50 → £5.00').waitFor();
  await page.goto('http://127.0.0.1:7518/settings/tasks?task=shopify_releases');
  await page.getByRole('heading',{name:'Scheduled Product Releases',exact:true}).waitFor();
  assert.match(await page.locator('body').innerText(),/GMT/);
  await page.getByText('Schedule settings',{exact:true}).click();
  assert.equal(await page.locator('select[name=interval]').inputValue(),'5');
  // Viewing controls must not start work or enable any authority.
  await page.goto('http://127.0.0.1:7518/settings/publishing');
  assert.equal(await page.getByLabel('Enable automatic publishing').isChecked(),false);
  if(name==='mobile') {
   const save=page.getByRole('button',{name:'Save publishing authority',exact:true});
   await save.scrollIntoViewIfNeeded();
   const control=await save.boundingBox();const nav=await page.locator('.mobile-bottom-nav').boundingBox();
   assert.ok(control.y+control.height<=nav.y,'Save control overlaps fixed navigation');
  }
  assert.deepEqual(errors,[]);
  results.push({viewport:name,queue_pagination:true,event_filter_and_detail:true,release_settings:true,authority_still_off:true,no_browser_errors:true});
  await context.close();
 }
 fs.writeFileSync(`${output}/browser-results.json`,JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
} finally {await browser.close();}
