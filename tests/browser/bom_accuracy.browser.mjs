import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
mkdirSync('/tmp/tlc-bom-accuracy',{recursive:true});
try {
 for(const [name,viewport]of [['desktop',{width:1440,height:1000}],['phone',{width:390,height:844}]]) {
  const context=await browser.newContext({viewport});
  await context.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:7513'?r.continue():r.abort());
  const page=await context.newPage(), errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.dismiss());
  await page.goto('http://127.0.0.1:7513/production/costing/settings');
  const defaults=page.locator('form').filter({has:page.locator('[name=tariff]')});
  const version=await defaults.locator('[name=version]').inputValue();
  assert.equal((await context.request.post('http://127.0.0.1:7513/production/costing/settings',{form:{version,buffer:'10',print_waste_rate:'11',tariff:'.24226',labour_rate:'30',markup:'100',fee_percent:'2',fee_fixed:'.25',overhead:'0'}})).status(),200);
  await page.goto('http://127.0.0.1:7513/production/bom');
  const count=()=>page.locator('table[data-bom-rows] tbody tr').count();
  const counts=async()=>Promise.all(['inventory','manual','pack','process'].map(kind=>page.locator(`table[data-bom-rows=${kind}] tbody tr`).count()));
  assert.deepEqual(await counts(),[0,0,0,0]);
  assert.equal(await page.locator('[data-advanced]').getAttribute('open'),null);
  // Every Add dialog must cancel without inserting a DOM row or form value.
  for(const label of ['Add Material / Component','Add Hypothetical Material','Add Production Process','Add Packaging Item']) {
   for(const method of ['cancel','escape','backdrop']) {
    await page.getByRole('button',{name:label,exact:true}).click();
    const dialog=page.getByRole('dialog');
    if(method==='cancel')await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
    else if(method==='escape')await page.keyboard.press('Escape');
    else await page.mouse.click(1,1);
    await dialog.waitFor({state:'detached'});assert.equal(await count(),0);
   }
  }
  assert.equal(await page.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}),false);
  await page.getByRole('button',{name:'Add Material / Component',exact:true}).click();
  let inventory=page.getByRole('dialog');
  await inventory.locator('[name=item_id]').selectOption({label:'PLA fictional · material'});
  await inventory.locator('[name=quantity]').fill('2');await inventory.locator('[name=unit]').selectOption('g');
  await inventory.getByRole('button',{name:'Apply Row'}).click();assert.deepEqual(await counts(),[1,0,0,0]);
  const invRow=page.locator('[data-bom-rows=inventory] tbody tr');
  await invRow.getByRole('button',{name:'Edit Material / Component'}).click();
  await page.getByRole('dialog').locator('[name=quantity]').fill('3');await page.getByRole('dialog').getByRole('button',{name:'Cancel'}).click();assert.equal(await invRow.locator('[name=quantity]').inputValue(),'2');
  await invRow.getByRole('button',{name:'Edit Material / Component'}).click();
  await page.getByRole('dialog').locator('[name=quantity]').fill('3');await page.getByRole('dialog').getByRole('button',{name:'Apply Row'}).click();assert.equal(await invRow.locator('[name=quantity]').inputValue(),'3');
  await invRow.getByRole('button',{name:'Remove',exact:true}).click();assert.deepEqual(await counts(),[0,0,0,0]);
  await page.locator('input[name=name]').fill(`Santa acceptance ${name}`);
  for(const [material,qty]of [['Red','13'],['Ivory','17'],['Beige','2'],['Black','2']]) {
   await page.getByRole('button',{name:'Add Hypothetical Material',exact:true}).click();const dialog=page.getByRole('dialog');
   for(const [field,value]of Object.entries({manual_name:material,manual_purchase_cost:'13.99',manual_purchase_quantity:'1',manual_quantity:qty}))await dialog.locator(`[name=${field}]`).fill(value);
   await dialog.locator('[name=manual_purchase_unit]').selectOption('kg');await dialog.locator('[name=manual_unit]').selectOption('g');await dialog.locator('[name=manual_print_material]').selectOption('yes');
   await dialog.getByRole('button',{name:'Apply Row'}).click();
  }
  await page.getByRole('button',{name:'Add Production Process',exact:true}).click();let dialog=page.getByRole('dialog');
  const eid=await dialog.locator('[name=equipment_id] option').filter({hasText:'PLA provisional'}).first().getAttribute('value');
  await dialog.locator('[name=equipment_id]').selectOption(eid);await dialog.locator('[name=runtime_hours]').fill('4');await dialog.locator('[name=runtime_minutes]').fill('10');
  await dialog.getByRole('status').filter({hasText:'Per finished product: 250 minutes'}).waitFor();
  assert.equal(await dialog.locator('[name=usable_units]').isVisible(),false);
  await dialog.getByRole('button',{name:'Apply Row'}).click();
  await page.locator('[name=labour_minutes]').fill('2');await page.locator('[name=selling_price]').fill('6');
  assert.deepEqual(await counts(),[0,4,0,1]);
  const preview=async()=>{
   const response=page.waitForResponse(r=>r.url().includes('/production/bom')&&r.request().method()==='POST'&&r.request().postData()?.includes('preview'));
   await page.getByRole('button',{name:'Calculate Preview',exact:true}).click();assert.equal((await response).status(),200);
   await page.locator('#live-costing').getByRole('heading',{name:'Profitability Preview',exact:true}).waitFor();
  };
  for(let i=0;i<5;i++){await preview();assert.deepEqual(await counts(),[0,4,0,1]);}
  assert.match(await page.locator('#live-costing').textContent(),/0\.0523226/);
  await page.locator('[data-advanced] summary').click();
  for(const [rate,amount]of [['20','0.095132'],['0','0.00000'],['','0.0523226'],['11','0.0523226']]) {
   await page.locator('[name=print_waste_rate]').fill(rate);await preview();
   await page.waitForFunction(amount=>document.querySelector('#live-costing').textContent.includes(amount),amount);
   assert.deepEqual(await counts(),[0,4,0,1]);
  }
  // Edit -> Cancel retains the exact original line.
  const red=page.locator('table[data-bom-rows=manual] tbody tr').first();
  await red.getByRole('button',{name:'Edit Hypothetical Material'}).click();dialog=page.getByRole('dialog');
  await dialog.locator('[name=manual_quantity]').fill('99');await dialog.getByRole('button',{name:'Cancel'}).click();
  assert.equal(await red.locator('[name=manual_quantity]').inputValue(),'13');
  // Box purchase uses the same helper, then becomes exactly one packaging line.
  await page.getByRole('button',{name:'Add Packaging Item',exact:true}).click();dialog=page.getByRole('dialog');
  for(const [field,value]of Object.entries({pack_name:'Product box',pack_purchase_cost:'15',pack_purchase_quantity:'50',pack_quantity:'1'}))await dialog.locator(`[name=${field}]`).fill(value);
  await dialog.locator('[name=pack_purchase_unit]').selectOption('piece');await dialog.locator('[name=pack_unit]').selectOption('piece');
  await dialog.getByRole('status').filter({hasText:'£0.30'}).waitFor();
  await page.screenshot({path:`/tmp/tlc-bom-accuracy/${name}-packaging.png`,fullPage:true});
  await dialog.getByRole('button',{name:'Apply Row'}).click();assert.deepEqual(await counts(),[0,4,1,1]);
  for(const [kind,label,field]of [['pack','Edit Packaging Item','pack_quantity'],['process','Edit Production Process','runtime_hours']]) {
   const row=page.locator(`table[data-bom-rows=${kind}] tbody tr`).first();
   const before=await row.locator('input,select').evaluateAll(nodes=>nodes.map(node=>[node.name,node.value]));
   await row.getByRole('button',{name:label,exact:true}).click();await page.getByRole('dialog').locator(`[name=${field}]`).fill('99');await page.keyboard.press('Escape');
   assert.deepEqual(await row.locator('input,select').evaluateAll(nodes=>nodes.map(node=>[node.name,node.value])),before);
  }
  for(let i=0;i<3;i++) {
   await page.getByRole('button',{name:'Save Local Scenario',exact:true}).click();await page.waitForURL(/\/production\/bom\/\d+$/);
   await page.locator('[data-bom-rows=process] .bom-row-actions').waitFor();assert.deepEqual(await counts(),[0,4,1,1]);await preview();
  }
  assert.equal(await page.locator('[name=print_waste_rate]').inputValue(),'11');
  const savedUrl=page.url();await page.goto('http://127.0.0.1:7513/production/bom');assert.deepEqual(await counts(),[0,0,0,0]);await page.goto(savedUrl);assert.deepEqual(await counts(),[0,4,1,1]);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(name==='phone') {
   const nav=page.locator('.mobile-bottom-nav');assert.equal(await nav.evaluate(el=>getComputedStyle(el).position),'fixed');assert.ok(await nav.isVisible());
   const last=page.getByRole('button',{name:'Delete scenario from saved list'});await last.scrollIntoViewIfNeeded();
   assert.ok((await last.boundingBox()).y+(await last.boundingBox()).height<=(await nav.boundingBox()).y);
   const finalText=page.locator('.bom-workspace > form').last().locator('p');await finalText.scrollIntoViewIfNeeded();assert.ok((await finalText.boundingBox()).y+(await finalText.boundingBox()).height<=(await nav.boundingBox()).y);
   await page.getByRole('button',{name:'Add Packaging Item',exact:true}).click();
   await page.setViewportSize({width:390,height:500});dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Cancel'}).scrollIntoViewIfNeeded();await dialog.getByRole('button',{name:'Cancel'}).click();assert.deepEqual(await counts(),[0,4,1,1]);await page.setViewportSize(viewport);
  }
  await page.screenshot({path:`/tmp/tlc-bom-accuracy/${name}-saved.png`,fullPage:true});
  assert.deepEqual(errors,[]);await context.close();console.log(`${name}: Santa exact allowance, packaging, Add/Edit cancellation, 5 previews, repeated saves, reopen, responsive navigation PASS`);
 }
}finally{await browser.close();}
