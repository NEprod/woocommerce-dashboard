import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const output=process.env.TLC_BROWSER_OUTPUT;if(!output)throw new Error('Explicit screenshot directory required');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
const base='http://127.0.0.1:7529';let context,page;
async function connect(mobile=true){context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},serviceWorkers:'block'});await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());page=await context.newPage();await page.goto(base+'/shopify/drafts');}
async function state(){return(await context.request.get(base+'/__fixture__/state')).json();}
async function until(predicate){let current;for(let i=0;i<600;i++){current=await state();if(predicate(current))return current;await new Promise(r=>setTimeout(r,100));}throw new Error(JSON.stringify(current));}
async function allow(key){assert.equal((await context.request.post(base+'/__fixture__/allow/'+key)).status(),200);}
async function capture(name,id){await page.goto(base+'/operations/'+id);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:output+'/'+name+'.png',fullPage:true});}
try{
 await connect();
 const preview=await context.request.post(base+'/products/1/shopify/review',{form:{action:'preview'}});assert.equal(preview.status(),200);
 const token=(await preview.text()).match(/name="review" value="([^"]+)"/)[1];
 const accepted=await context.request.post(base+'/products/1/shopify/review',{form:{action:'confirm',review:token,acknowledge:'yes'},headers:{Accept:'application/json'}});assert.equal(accepted.status(),202);
 const id=(await accepted.json()).operation_id;
 await context.close(); // Safari closes/locks after the durable 202 acceptance.
 await connect();assert.equal((await state()).uploads,0);await allow('start');
 await until(s=>s.phase==='upload_started');await capture('mobile-upload-started',id);
 await context.close(); // disconnect while server is inside an upload
 await connect();await allow('upload');await until(s=>s.phase==='batch_acknowledged');
 assert.equal((await state()).progress.publishing.uploads_acknowledged,6);await capture('mobile-batch-acknowledged',id);
 await page.reload();await context.close(); // refresh/navigate/close after durable acknowledgements
 await connect();await allow('batch');await until(s=>s.phase==='media_processing');
 assert.equal((await state()).uploads,53);assert.equal((await state()).creates,1);await capture('mobile-awaiting-processing',id);
 await context.close(); // disconnected while Shopify processing is pending
 await connect(false);await allow('processing');await until(s=>s.phase==='readback');await capture('desktop-readback',id);
 await context.close(); // disconnect during actual readback call
 await connect(false);await allow('readback');const result=await until(s=>s.progress.status==='completed');
 assert.equal(result.uploads,53);assert.equal(result.creates,1);assert.equal(result.progress.publishing.uploads_acknowledged,53);
 await capture('desktop-completed',id);await context.close();await connect();await capture('mobile-completed',id);
 fs.writeFileSync(output+'/browser-results.json',JSON.stringify({operation_id:id,disconnections:['accepted','upload_started','batch_acknowledged','media_processing','readback'],uploads:result.uploads,creates:result.creates,status:result.progress.status,simulation:'isolated Chromium contexts closed/reopened, not native iOS Safari'},null,2));
 console.log('PASS: five disconnect/reopen boundaries; 53 uploads, one Create, same completed operation ID');
}finally{await browser.close();}
