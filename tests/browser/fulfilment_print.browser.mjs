import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdtempSync,mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:7522',output=mkdtempSync('/tmp/tlc-print-layout-')+'/tmp/pdfs';mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 await context.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
 const page=await context.newPage();await page.addInitScript(()=>window.print=()=>{});
 const before=await(await context.request.get(origin+'/fixture')).json();
 for(const [name,orders,pages,lines] of [['one-card',[1],1,1],['two-card',[2],1,2],['bulk-two-orders',[1,2],2,3]]){
  if(orders.length===1)await page.goto(`${origin}/production/orders/${orders[0]}/sheet`);
  else{
   await page.goto(origin+'/orders/');
   await Promise.all([page.waitForURL('**/orders/print'),page.evaluate(()=>{const f=document.createElement('form');f.method='POST';f.action='/orders/print';for(const id of ['1','2']){const i=document.createElement('input');i.name='order_id';i.value=id;f.append(i);}document.body.append(f);f.submit();})]);
  }
  assert.equal(await page.locator('.order-sheet-line').count(),lines);
  await page.locator('.order-sheet-barcode img').evaluateAll(images=>Promise.all(images.map(i=>i.decode())));
  await page.emulateMedia({media:'print'});
  const path=`${output}/${name}.pdf`;await page.pdf({path,preferCSSPageSize:true,displayHeaderFooter:false,printBackground:true});
  assert.match(execFileSync('pdfinfo',[path],{encoding:'utf8'}),new RegExp(`Pages:\\s+${pages}\\b`));
  for(let number=1;number<=pages;number++){
   const text=execFileSync('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python',['-c','from pypdf import PdfReader; import sys; print(PdfReader(sys.argv[1]).pages[int(sys.argv[2])].extract_text())',path,String(number-1)],{encoding:'utf8'});
   for(const expected of ['Tender Loving Creations','Anniversary Level Complete','Synthetic recipient','GLOSS-SHEET','Customer / Delivery','TLC-ORDER-'])assert.ok(text.includes(expected),`${name} page ${number}: ${expected}`);
  }
  execFileSync('pdftoppm',['-scale-to','1200','-png',path,`${output}/${name}`]);
  console.log(`PASS ${name}: ${pages} A4 page(s), item details, materials, customer and barcode on each page; ${path}`);
  await page.emulateMedia({media:'screen'});
 }
 const after=await(await context.request.get(origin+'/fixture')).json();
 for(const key of ['events','picks','transactions','prints'])assert.equal(after[key],before[key],`Printing changed ${key}`);
 await context.close();
}finally{await browser.close();}
