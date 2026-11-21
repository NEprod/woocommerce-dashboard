import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-stock-cards',{recursive:true});
mkdirSync('/tmp/tlc-stock-cards/tmp/pdfs',{recursive:true});
try {
 for(const [name,viewport] of [['desktop',{width:1440,height:900}],['phone',{width:390,height:844}]]) {
  const context=await browser.newContext({viewport});
  await context.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:7514'?r.continue():r.abort());
  const page=await context.newPage();
  let prints=0;await page.exposeFunction('recordPrint',()=>prints++);await page.addInitScript(()=>{window.print=()=>window.recordPrint();});
  for(const id of [1,2,4,5]) {
   const response=await page.goto(`http://127.0.0.1:7514/production/item/${id}/stock-card`);
   assert.equal(response.status(),200);
   const barcode=page.locator('.stock-card-barcode img');
   if(id===4){assert.equal(await barcode.count(),0);await page.getByRole('alert').filter({hasText:'Identifier too long'}).waitFor();}
   else {await barcode.evaluate(img=>img.decode());assert.ok((await barcode.getAttribute('src')).endsWith('layout=stock-card'));}
   assert.equal(prints,0,'opening must not print');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:`/tmp/tlc-stock-cards/${name}-${id}.png`,fullPage:true});
   await page.emulateMedia({media:'print'});
   const layout=await page.locator('.inventory-stock-card').evaluate(el=>({width:el.offsetWidth,height:el.offsetHeight,overflow:el.scrollHeight>el.clientHeight,bodyHeight:document.body.scrollHeight}));
   assert.equal(layout.width,192);assert.equal(layout.height,336);assert.equal(layout.overflow,false);
   assert.ok(layout.bodyHeight<=336,JSON.stringify(layout));
   const details=await page.locator('.stock-card-copy').evaluate(el=>({height:el.clientHeight,contents:el.scrollHeight}));
   assert.ok(details.contents<=details.height+1,JSON.stringify(details));
   if(id!==4){const size=await barcode.boundingBox();assert.ok(Math.abs(size.width-170.88)<.1);assert.ok(Math.abs(size.height-43.2)<.1);}
   if(id===1||id===5){
    assert.equal(await page.locator('.stock-card-photo').evaluate(img=>getComputedStyle(img).objectFit),'contain');
    const bounds=await page.locator('.stock-card-photo').evaluate(img=>{const a=img.getBoundingClientRect(),b=img.parentElement.getBoundingClientRect();return {image:[a.left,a.top,a.right,a.bottom],area:[b.left,b.top,b.right,b.bottom]};});
    assert.ok(bounds.image[0]>=bounds.area[0]-.1&&bounds.image[1]>=bounds.area[1]-.1&&bounds.image[2]<=bounds.area[2]+.1&&bounds.image[3]<=bounds.area[3]+.1,JSON.stringify(bounds));
   }
   else assert.equal(await page.locator('.stock-card-placeholder').count(),1);
   const pdf=`/tmp/tlc-stock-cards/tmp/pdfs/${name}-${id}.pdf`;
   await page.pdf({path:pdf,preferCSSPageSize:true,printBackground:true,displayHeaderFooter:false,scale:1});
   const info=execFileSync('pdfinfo',[pdf],{encoding:'utf8'});
   assert.match(info,/Pages:\s+1\b/);assert.match(info,/Page size:\s+144 x 252 pts/);
   await page.emulateMedia({media:'screen'});
  }
  await page.getByRole('button',{name:'Print Stock Card',exact:true}).click();
  assert.equal(prints,1);
  await context.close();console.log(name+': portrait 2 × 3.5-inch cards, 3 item types, images/placeholders, long-code warning, print sizing, no overflow or automatic print PASS');
 }
} finally {await browser.close();}
