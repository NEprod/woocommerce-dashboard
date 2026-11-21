import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-stock-cards',{recursive:true});
try {
 for(const [name,viewport] of [['desktop',{width:1440,height:900}],['phone',{width:390,height:844}]]) {
  const context=await browser.newContext({viewport});
  await context.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:7514'?r.continue():r.abort());
  const page=await context.newPage();
  let prints=0;await page.exposeFunction('recordPrint',()=>prints++);await page.addInitScript(()=>{window.print=()=>window.recordPrint();});
  for(const id of [1,2,4]) {
   const response=await page.goto(`http://127.0.0.1:7514/production/item/${id}/stock-card`);
   assert.equal(response.status(),200);
   await page.locator('.stock-card-barcode img').evaluate(img=>img.decode());
   assert.equal(prints,0,'opening must not print');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:`/tmp/tlc-stock-cards/${name}-${id}.png`,fullPage:true});
   await page.emulateMedia({media:'print'});
   const layout=await page.locator('.inventory-stock-card').evaluate(el=>({width:el.offsetWidth,height:el.offsetHeight,overflow:el.scrollHeight>el.clientHeight,bodyHeight:document.body.scrollHeight}));
   assert.equal(layout.width,384);assert.equal(layout.height,288);assert.equal(layout.overflow,false);
   assert.ok(layout.bodyHeight<=288,JSON.stringify(layout));
   await page.emulateMedia({media:'screen'});
  }
  await page.getByRole('button',{name:'Print Stock Card',exact:true}).click();
  assert.equal(prints,1);
  await context.close();console.log(name+': 3 card types, exact print dimensions, no overflow or automatic print PASS');
 }
} finally {await browser.close();}
