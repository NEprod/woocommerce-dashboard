import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const output=process.env.TLC_BROWSER_OUTPUT;if(!output)throw new Error('Explicit screenshot directory required');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
let context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
async function configure(c){await c.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());}
const base='http://127.0.0.1:7531';await configure(context);let page=await context.newPage();
async function state(){return(await context.request.get(base+'/__fixture__/state')).json();}
try{
 const initial=await state();assert.equal(initial.variants,1);assert.equal(initial.uploads,53);assert.equal(initial.creates,1);
 await page.goto(base+'/shopify/drafts/identity/'+initial.identity_id);
 await page.getByRole('button',{name:'Review missing variant work — reads only'}).click();
 await page.getByText('14 expected variants', {exact:false}).waitFor();
 assert.equal((await state()).variant_writes,0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:output+'/desktop-fresh-completion-review.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:output+'/mobile-fresh-completion-review.png',fullPage:true});
 await page.locator('input[name="acknowledge"]').check();
 await page.getByRole('button',{name:'Approve completion in server worker'}).click();
 await page.waitForURL('**/operations/**');const operation=page.url();await context.close();
 context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});await configure(context);page=await context.newPage();
 let result;for(let i=0;i<600;i++){result=await state();if(result.state==='verified')break;await new Promise(r=>setTimeout(r,100));}
 assert.equal(result.state,'verified');assert.equal(result.remote_id,initial.retained_id);assert.equal(result.variants,14);assert.equal(result.media,53);
 assert.equal(result.uploads,53);assert.equal(result.creates,1);
 await page.goto(operation);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:output+'/mobile-completed-retained-draft.png',fullPage:true});
 fs.writeFileSync(output+'/browser-results.json',JSON.stringify({initial,result,disconnected_after_approval:true,simulation:'isolated Chromium, not native Safari'},null,2));
 console.log('PASS: read-only recovery review, desktop/mobile, disconnected approval, same product, 14 variants/53 images, no repeated uploads/Create');
}finally{await browser.close();}
