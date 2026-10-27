import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const output=process.env.TLC_BROWSER_OUTPUT;if(!output)throw new Error('Explicit screenshot directory required');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
let context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
const base='http://127.0.0.1:7532';
async function configure(c){await c.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());}
await configure(context);let page=await context.newPage();let confirmations=0;
context.on('request',r=>{if(r.method()==='POST'&&new URLSearchParams(r.postData()||'').get('action')==='confirm')confirmations++;});
async function state(){return(await context.request.get(base+'/__fixture__/state')).json();}
async function until(fn){let s;for(let i=0;i<600;i++){s=await state();if(fn(s))return s;await new Promise(r=>setTimeout(r,100));}throw Error(JSON.stringify(s));}
try{
 const start=await state();await page.goto(base+'/products/'+start.product_id+'/shopify/review');
 await page.getByRole('button',{name:'Build read-only Preview'}).click();
 await page.locator('input[name="acknowledge"]').check();
 await page.getByRole('button',{name:'Confirm draft creation & memberships'}).click();
 const pending=await until(s=>s.waiting);assert.equal(pending.variants,14);assert.equal(pending.operation_status,'pending');
 await page.waitForFunction(()=>document.querySelector('[data-publish-stage]')?.textContent.includes('readback'));
 const first=await page.locator('[data-publish-details]').textContent();
 await page.waitForFunction(value=>document.querySelector('[data-publish-details]')?.textContent!==value,first);
 assert.equal(confirmations,1);assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).some(k=>k.startsWith('tlc-shopify-publish:'))),true);
 await page.screenshot({path:output+'/mobile-pending-verification-modal.png',fullPage:false});
 await page.locator('[data-publish-close]').click();await page.locator('[data-publish-resume]').click();
 await page.reload();await page.locator('[data-shopify-progress][open]').waitFor();assert.equal(confirmations,1);
 await context.close();
 context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});await configure(context);page=await context.newPage();
 await page.goto(base+'/operations/'+pending.operation_id);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:output+'/desktop-pending-operation.png',fullPage:true});
 await context.request.post(base+'/__fixture__/continue');const result=await until(s=>s.operation_status==='succeeded');
 assert.equal(result.operation_id,pending.operation_id);assert.equal(result.variants,14);assert.equal(result.uploads,53);assert.equal(result.creates,1);assert.equal(result.variant_creates,1);
 await page.reload();await page.screenshot({path:output+'/desktop-completed-operation.png',fullPage:true});
 fs.writeFileSync(output+'/browser-results.json',JSON.stringify({confirmations,pending,result,modal_reload:true,new_session:true,simulation:'isolated Chromium, not native Safari'},null,2));
 console.log('PASS: modal continues polling pending verification; close/reopen/reload makes no duplicate confirmation; new session sees same completed 14/53 operation');
}finally{await browser.close();}
