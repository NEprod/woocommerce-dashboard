import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7522';
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-order-fulfilment',{recursive:true});
try{
 for(const [name,viewport]of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
  const context=await browser.newContext({viewport});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const seed=await(await context.request.get(origin+'/fixture')).json();
  await page.addInitScript(({token})=>{
   window.BarcodeDetector=class{static async getSupportedFormats(){return ['qr_code','code_128','ean_13','ean_8','upc_a','upc_e','data_matrix','code_39','itf'];}async detect(){return [{rawValue:token,format:'code_128'}];}};
   Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>({getTracks:()=>[{stop(){}}]})});
   Object.defineProperty(HTMLVideoElement.prototype,'srcObject',{set(){},get(){return null;}});
   Object.defineProperty(HTMLVideoElement.prototype,'readyState',{get(){return 4;}});
   HTMLVideoElement.prototype.play=async()=>{};
  },{token:`TLC-ORDER-${seed.order}`});
  await page.goto(origin+'/production/orders/');
  await page.locator('[name=reference]').fill('#TEST-1001');await page.getByRole('button',{name:'Find order',exact:true}).click();
  await page.waitForURL(`${origin}/production/orders/${seed.order}`);const manual=page.url();
  await page.goto(origin+'/production/orders/');await page.getByRole('button',{name:'Scan Order Barcode',exact:true}).click();
  await page.waitForURL(manual);assert.equal(page.url(),manual);
  const afterLookup=await(await context.request.get(origin+'/fixture')).json();
  assert.equal(afterLookup.events,seed.events);assert.equal(afterLookup.transactions,seed.transactions);
  await page.getByRole('button',{name:'Start Picking',exact:true}).waitFor();
  await page.screenshot({path:`/tmp/tlc-order-fulfilment/${name}-order.png`,fullPage:true});
  await page.goto(origin+'/settings/shopify-webhooks');
  const count=afterLookup.webhooks;
  await page.getByRole('button',{name:'Add Webhook',exact:true}).click();
  let modal=page.getByRole('dialog');await modal.locator('[name=name]').fill('Discarded');
  await modal.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal((await(await context.request.get(origin+'/fixture')).json()).webhooks,count);
  await page.getByRole('button',{name:'Add Webhook',exact:true}).click();await page.keyboard.press('Escape');
  assert.equal((await(await context.request.get(origin+'/fixture')).json()).webhooks,count);
  await page.getByRole('button',{name:'Add Webhook',exact:true}).click();modal=page.getByRole('dialog');
  const profileName=`${name} updates ${Date.now()}`;
  await modal.locator('[name=name]').fill(profileName);
  await modal.locator('[name=topic]').selectOption(name==='desktop'?'orders/updated':'orders/create');
  await modal.locator('[name=endpoint]').fill(`https://${name}-${Date.now()}.example/webhooks/shopify/orders`);
  if(name==='mobile'){
   await page.setViewportSize({width:390,height:430});
   await modal.locator('[name=name]').focus();await page.keyboard.press('Tab');
   assert.equal(await modal.evaluate(el=>el.contains(document.activeElement)),true,'Keyboard focus remains in modal');
   await modal.getByRole('button',{name:'Save Webhook',exact:true}).scrollIntoViewIfNeeded();
   const bounds=await modal.getByRole('button',{name:'Save Webhook',exact:true}).boundingBox();
   assert.ok(bounds.y>=0&&bounds.y+bounds.height<=430,'Save accessible with reduced keyboard-sized viewport');
   await page.screenshot({path:'/tmp/tlc-order-fulfilment/mobile-modal-short-viewport.png'});
   await page.setViewportSize(viewport);
  }
  await modal.getByRole('button',{name:'Save Webhook',exact:true}).click();
  await page.waitForURL(origin+'/settings/shopify-webhooks');await page.getByRole('button',{name:`Edit webhook ${profileName}`,exact:true}).waitFor();
  assert.equal((await(await context.request.get(origin+'/fixture')).json()).webhooks,count+1);
  await page.getByRole('button',{name:`Edit webhook ${profileName}`,exact:true}).click();modal=page.getByRole('dialog');
  assert.equal(await modal.locator('[name=name]').inputValue(),profileName);
  await modal.locator('[name=name]').fill('Not saved');await modal.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('button',{name:`Edit webhook ${profileName}`,exact:true}).click();
  assert.equal(await page.getByRole('dialog').locator('[name=name]').inputValue(),profileName);
  await page.keyboard.press('Escape');
  await page.screenshot({path:`/tmp/tlc-order-fulfilment/${name}-webhooks.png`,fullPage:true});
  if(name==='mobile'){
   assert.equal(await page.locator('.mobile-bottom-nav').evaluate(el=>getComputedStyle(el).position),'fixed');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal page overflow');
   await page.locator('.webhook-settings').evaluate(el=>{el.scrollIntoView({block:'end'});window.scrollTo(0,document.documentElement.scrollHeight);});
   await page.waitForFunction(()=>{const last=[...document.querySelectorAll('.webhook-settings > details')].at(-1).getBoundingClientRect(),nav=document.querySelector('.mobile-bottom-nav').getBoundingClientRect();return last.bottom<=nav.top;});
   const last=await page.locator('.webhook-settings > details').last().boundingBox(),nav=await page.locator('.mobile-bottom-nav').boundingBox();
   assert.ok(last.y+last.height<=nav.y,'Final controls scroll above fixed navigation');
  }
  await page.goto(`${origin}/production/orders/${seed.order}/sheet`);await page.emulateMedia({media:'print'});
  await page.pdf({path:`/tmp/tlc-order-fulfilment/${name}-pick-sheet.pdf`,preferCSSPageSize:true});
  const after=await(await context.request.get(origin+'/fixture')).json();
  assert.equal(after.transactions,seed.transactions);assert.equal(after.picks,seed.picks);assert.equal(after.events,seed.events);
  assert.deepEqual(errors,[]);await context.close();
  console.log(`${name}: manual/camera same job, read-only lookup/printing, webhook Add/Cancel/Escape/Save/Pencil, fixed navigation PASS`);
 }
 const context=await browser.newContext({viewport:{width:390,height:844}});
 await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
 const page=await context.newPage(),seed=await(await context.request.get(origin+'/fixture')).json();
 await page.goto(`${origin}/production/orders/${seed.order}`);
 const start=page.locator('form[data-action=start_picking]');
 await start.locator('[name=reason]').fill('Browser acceptance: start picking');await start.locator('[name=confirmed]').check();
 await start.getByRole('button',{name:'Start Picking',exact:true}).click();
 const pick=page.locator('form[data-action=pick]');await pick.waitFor();
 assert.equal((await(await context.request.get(origin+'/fixture')).json()).transactions,seed.transactions,'Starting picking reserves, never issues');
 await pick.locator('[name=code]').selectOption('SANTA-BODY');await pick.locator('[name=location_id]').selectOption(String(seed.tub));
 await pick.locator('[name=quantity]').fill('1');await pick.locator('[name=reason]').fill('Browser confirmed actual component pick');await pick.locator('[name=confirmed]').check();
 const replay=await pick.evaluate(el=>({url:el.action,fields:Object.fromEntries(new FormData(el))}));
 await pick.getByRole('button',{name:'Confirm picked quantity / issue stock',exact:true}).click();
 const once=await(await context.request.get(origin+'/fixture')).json();
 assert.equal(once.picks,seed.picks+1);assert.equal(once.transactions,seed.transactions+1);
 await context.request.post(replay.url,{form:replay.fields});
 const twice=await(await context.request.get(origin+'/fixture')).json();
 assert.equal(twice.picks,once.picks);assert.equal(twice.transactions,once.transactions);
 await context.close();console.log('mobile: Start Picking reserves only; confirmed partial pick + replay posts exactly one ledger issue PASS');
}finally{await browser.close();}
