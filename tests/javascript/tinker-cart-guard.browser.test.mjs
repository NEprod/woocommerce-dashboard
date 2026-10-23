// Isolated real browser tests; no Shopify, Discord, customer profile or order access.
// TLC_PLAYWRIGHT_MODULE can point at an existing installed Playwright package.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';

const require=createRequire(import.meta.url);
const playwright=require(process.env.TLC_PLAYWRIGHT_MODULE || 'playwright');
const root=new URL('../../',import.meta.url);
const fixture='/tests/browser/tinker-cart-guard.html';
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function world(browser,mobile,production=false) {
  const state={items:[],adds:0,changes:0};
  const server=createServer(async(req,res)=>{
    try {
      if (req.method==='POST') {
        let body='';for await (const chunk of req) body+=chunk;
        if (req.url==='/cart/add.js' || req.url==='/cart/add') {
          const raw=req.url==='/cart/add.js'?JSON.parse(body):Object.fromEntries(new URLSearchParams(body));
          const properties=raw.properties || Object.fromEntries(Object.entries(raw).filter(([key])=>key.startsWith('properties[')).map(([key,value])=>[key.slice(11,-1),value]));
          const key=createHash('sha256').update(JSON.stringify([raw.id,properties])).digest('hex');
          const item=state.items.find(item=>item.key===key);state.adds++;
          if(item)item.quantity+=Number(raw.quantity||1);
          else state.items.push({key,id:raw.id,quantity:Number(raw.quantity||1),properties});
        } else if(req.url==='/cart/change.js') {
          const raw=JSON.parse(body),item=state.items.find(item=>item.key===raw.id);
          assert.ok(item,'quantity changes target the line key');item.quantity=raw.quantity;state.changes++;
        } else throw Error(`Unexpected local write ${req.url}`);
        res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:state.items}));return;
      }
      if(req.url==='/cart.js') {
        res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:state.items}));return;
      }
      if(req.url==='/checkout') {
        // Contract fixture, NOT Shopify checkout/order acceptance. Private data
        // is retained server-side, while customer-facing properties omit '_'.
        res.setHeader('Content-Type','text/html');
        res.end('<h1>Offline checkout review — no order</h1>'+state.items.map(item=>'<article>'+Object.entries(item.properties).filter(([key])=>!key.startsWith('_')).map(([key,value])=>`<p>${escape(key)}: ${escape(value)}</p>`).join('')+'</article>').join(''));return;
      }
      const path=req.url==='/'?fixture:req.url;
      if(![fixture,'/tests/browser/storefront-customisations.html','/shopify/extensions/tlc-customisations/assets/tlc-customer-fields.js','/shopify/extensions/tlc-customisations/assets/tlc-customer-fields.css'].includes(path)) {res.writeHead(404);res.end();return;}
      res.setHeader('Content-Type',path.endsWith('.js')?'text/javascript':path.endsWith('.css')?'text/css':'text/html');
      let content=await readFile(new URL('.'+path,root));
      if(production && path===fixture) {
        content=content.toString().replace('id="personalisation"','id="personalisation" data-production-time="3-5 Days"')
          .replace('<div data-tlc-fields></div>','<div data-tlc-fields></div><p class="tlc-production-time"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/></svg><span>Production Time: 3-5 Days</span></p><input data-tlc-production-input type="hidden" name="properties[Production time]" value="3-5 Days" form="BuyButtons-ProductForm-main">')
          .replace('<button type="submit">Add personalised item</button>','<button type="submit">Add personalised item</button><div class="shopify-payment-button"><button type="button">Fixture Buy now</button></div>')
          .replace('<iframe name="native-result"',`<section class="shopify-section" id="metadata-only-section"><tlc-customer-fields id="metadata-only" data-product-id="103" data-form-id="metadata-only-form" data-production-time="5-10 Days"><p data-tlc-status></p><div data-tlc-fields></div><p class="tlc-production-time">Production Time: 5-10 Days</p><input data-tlc-production-input type="hidden" name="properties[Production time]" value="5-10 Days" form="metadata-only-form"></tlc-customer-fields><product-form-component data-product-id="103"><form id="metadata-only-form" action="/cart/add" on:submit="/handleSubmit"><input name="id" value="204" type="hidden"><button type="submit">Add production-only item</button></form></product-form-component></section><iframe name="native-result"`);
      }
      res.end(content);
    } catch(error){res.writeHead(500);res.end(String(error));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const context=await browser.newContext(mobile?{viewport:{width:390,height:844},isMobile:true,hasTouch:true}:{});
  await context.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
  const page=await context.newPage();await page.goto(origin);
  await page.waitForFunction(()=>document.querySelector('tlc-customer-fields')?.controls?.size===2);
  assert.equal(await page.getByLabel('Recipients Name').evaluate(input=>input.form===document.getElementById('BuyButtons-ProductForm-main')),true,'external controls bind by the real ID attribute, not shadowed form.id');
  return {page,state,origin,close:async()=>{await context.close();await new Promise(resolve=>server.close(resolve));}};
}
async function settle(page){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await page.waitForTimeout(80);}
async function fill(page,name,years='') {
  await page.getByLabel('Recipients Name').fill(name);await page.getByLabel('Anniversary Years').fill(years);
}
async function added(page,count){await page.waitForFunction(count=>document.querySelector('#cart-drawer').children.length===count,count);}

for(const engine of (process.env.TLC_TEST_ENGINES || 'chromium,webkit').split(',')) test(`${engine}: Tinker-compatible cart integration`,async t=>{
  const options=engine==='chromium' && process.platform==='darwin'?{executablePath:process.env.TLC_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}:{};
  const browser=await playwright[engine].launch({headless:true,...options});
  const capture=async(page,name)=>{
    if(!process.env.TLC_BROWSER_ARTIFACT_DIR)return;
    await mkdir(process.env.TLC_BROWSER_ARTIFACT_DIR,{recursive:true});
    await page.screenshot({path:join(process.env.TLC_BROWSER_ARTIFACT_DIR,`${engine}-${name}.png`),fullPage:true});
  };
  const run=async(name,fn,production=false)=>t.test(name,async()=>{const w=await world(browser,engine==='webkit'||process.env.TLC_MOBILE==='1',production);try{await fn(w);}finally{await w.close();}});
  try {
    await run('production-only and personalised blocks capture once, keep variant identity and snapshot order',async({page,state})=>{
      assert.equal(await page.locator('#personalisation .shopify-payment-button').count(),0);
      assert.equal(await page.locator('#BuyButtons-ProductForm-main .shopify-payment-button').isVisible(),false);
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await settle(page);
      assert.equal(state.adds,0,'production metadata does not bypass required answers');
      await fill(page,'Ellie','6');
      assert.equal(await page.evaluate(()=>new FormData(document.querySelector('#BuyButtons-ProductForm-main')).getAll('properties[Production time]').length),1);
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await added(page,1);
      assert.equal(state.items[0].properties['Production time'],'3-5 Days');
      assert.deepEqual(Object.keys(state.items[0].properties).slice(0,3),['Production time','Recipients Name','Anniversary Years']);
      assert.equal(JSON.parse(state.items[0].properties._tlc_personalisation).fields.length,2);
      await page.getByRole('button',{name:'Add production-only item',exact:true}).click();await added(page,2);
      assert.deepEqual(state.items[1].properties,{'Production time':'5-10 Days'});
      await page.evaluate(()=>{document.querySelector('#BuyButtons-ProductForm-main [name=id]').value='205';const form=document.querySelector('#BuyButtons-ProductForm-main');form.replaceWith(form.cloneNode(true));});
      await page.waitForFunction(()=>document.querySelector('#personalisation').form===document.querySelector('#BuyButtons-ProductForm-main'));
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await added(page,3);
      assert.equal(state.items[2].id,'205');
      assert.equal(await page.evaluate(()=>new FormData(document.querySelector('#BuyButtons-ProductForm-main')).getAll('properties[Production time]').length),1);
      for(const target of ['#cart-drawer','#cart-page']) {
        assert.match(await page.locator(target+' article p').first().innerText(),/^Production time: 3-5 Days/);
      }
      await page.locator('#cart-drawer article').first().getByRole('button',{name:'Increase quantity'}).click();await settle(page);
      await page.reload();await added(page,3);
      assert.equal(state.items[0].properties['Production time'],'3-5 Days');
      await capture(page,'production-time');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.getByRole('link',{name:'Offline checkout fixture (no order)',exact:true}).click();
      assert.match(await page.locator('body').innerText(),/Production time: 5-10 Days/);
    },true);
    await run('empty required name blocks document-capture AJAX before any request',async({page,state})=>{
      const button=page.getByRole('button',{name:'Add personalised item',exact:true});
      if(engine==='webkit')await button.tap();else await button.click();await settle(page);
      assert.equal(state.adds,0);assert.equal(state.items.length,0);
      assert.match(await page.locator('#personalisation [data-tlc-status]').textContent(),/Recipients Name/);
      await capture(page,'missing-required');
    });
    await run('Freddie/5 and Ellie/6 retain separate lines, clean labels and typed identities',async({page,state})=>{
      await fill(page,'Freddie','5');await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await added(page,1);
      await fill(page,'Ellie','6');await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await added(page,2);
      assert.equal(state.adds,2);assert.notEqual(state.items[0].key,state.items[1].key);
      for(const [item,name,years] of [[state.items[0],'Freddie',5],[state.items[1],'Ellie',6]]) {
        assert.equal(item.properties['Recipients Name'],name);assert.equal(item.properties['Anniversary Years'],String(years));
        const snapshot=JSON.parse(item.properties._tlc_personalisation);
        assert.equal(snapshot.product_id,'100');assert.equal(snapshot.revision,'a'.repeat(64));
        assert.equal(snapshot.fields.find(f=>f.id==='recipient').value,name);
        assert.equal(snapshot.fields.find(f=>f.id==='anniversary_length').value,years);
        const order={customAttributes:Object.entries(item.properties).map(([key,value])=>({key,value}))};
        assert.equal(JSON.parse(order.customAttributes.find(a=>a.key==='_tlc_personalisation').value).revision,snapshot.revision);
      }
      for(const target of ['#cart-drawer','#cart-page']) {
        const text=await page.locator(target).innerText();assert.match(text,/Recipients Name: Freddie/);assert.match(text,/Anniversary Years: 5/);
        assert.doesNotMatch(text,/recipient|anniversary_length|_tlc_| · /);
      }
      await capture(page,'readable-cart');
      await page.locator('#cart-drawer article').first().getByRole('button',{name:'Increase quantity'}).click();
      await page.waitForFunction(()=>document.querySelector('#cart-drawer strong').textContent.includes('× 2'));
      assert.equal(state.items[0].quantity,2);assert.equal(state.items[1].quantity,1);
      await page.reload();await added(page,2);assert.match(await page.locator('#cart-page').innerText(),/Recipients Name: Ellie/);
      await page.getByRole('link',{name:'Offline checkout fixture (no order)',exact:true}).click();
      assert.match(await page.locator('body').innerText(),/Recipients Name: Freddie/);
      assert.doesNotMatch(await page.locator('body').innerText(),/_tlc_|anniversary_length| · /);
      assert.equal(state.items.length,2);
    });
    await run('Enter and synthetic AJAX submit cannot bypass validation',async({page,state})=>{
      await page.getByLabel('Recipients Name').press('Enter');await settle(page);assert.equal(state.adds,0);
      await page.evaluate(()=>document.querySelector('#BuyButtons-ProductForm-main').dispatchEvent(new SubmitEvent('submit',{cancelable:true,bubbles:true})));
      await settle(page);assert.equal(state.adds,0);
      await fill(page,'Freddie','5');await page.getByLabel('Recipients Name').press('Enter');await added(page,1);assert.equal(state.adds,1);
    });
    await run('native requestSubmit and browser validation preserve valid submissions',async({page,state})=>{
      await page.evaluate(()=>{const form=document.querySelector('#BuyButtons-ProductForm-main');form.removeAttribute('on:submit');form.noValidate=false;form.requestSubmit();});
      await settle(page);assert.equal(state.adds,0);
      await fill(page,'Ellie','6');await page.evaluate(()=>document.querySelector('#BuyButtons-ProductForm-main').requestSubmit());
      await page.waitForFunction(()=>document.querySelector('iframe').contentDocument?.body.textContent.includes('Ellie'));
      assert.equal(state.adds,1);assert.equal(state.items[0].properties['Recipients Name'],'Ellie');
    });
    await run('submission reads actual current controls, rejects whitespace and drops stale properties',async({page,state})=>{
      await fill(page,'   ','5');await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await settle(page);assert.equal(state.adds,0);
      await page.getByLabel('Recipients Name').evaluate(input=>{input.value='Freddie';});
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await added(page,1);
      assert.equal(state.items[0].properties['Recipients Name'],'Freddie');
      await page.getByLabel('Recipients Name').evaluate(input=>{input.value='';});
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await settle(page);assert.equal(state.adds,1);
      assert.equal(await page.evaluate(()=>new FormData(document.querySelector('#BuyButtons-ProductForm-main')).get('properties[_tlc_personalisation]')),null);
    });
    await run('multiple products and normal unpersonalised forms stay isolated',async({page,state})=>{
      await page.getByRole('button',{name:'Add plain item',exact:true}).click();await added(page,1);
      assert.deepEqual(state.items[0].properties,{});
      await page.getByLabel('Second message').fill('Other product');await page.getByRole('button',{name:'Add second item',exact:true}).click();await added(page,2);
      assert.equal(state.items[1].properties['Second message'],'Other product');assert.equal(state.items[1].properties['Recipients Name'],undefined);
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await settle(page);assert.equal(state.adds,2);
    });
    await run('form replacement, variant changes, reconnect and reset retain one scoped guard',async({page,state})=>{
      await page.evaluate(()=>{const form=document.querySelector('#BuyButtons-ProductForm-main');form.replaceWith(form.cloneNode(true));});
      await page.waitForFunction(()=>document.querySelector('#personalisation').form===document.querySelector('#BuyButtons-ProductForm-main'));
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await settle(page);assert.equal(state.adds,0);
      await fill(page,'Ellie','6');await page.evaluate(()=>{document.querySelector('#BuyButtons-ProductForm-main [name=id]').value='203';const block=document.querySelector('#personalisation');block.remove();document.querySelector('#main-product').prepend(block);});
      assert.equal(await page.evaluate(()=>new FormData(document.querySelector('#BuyButtons-ProductForm-main')).getAll('properties[_tlc_personalisation]').length),1);
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await added(page,1);assert.equal(state.items[0].id,'203');
      await page.evaluate(()=>document.querySelector('#BuyButtons-ProductForm-main').reset());await settle(page);
      await page.getByRole('button',{name:'Add personalised item',exact:true}).click();await settle(page);assert.equal(state.adds,1);
    });
    await run('existing nine-control browser fixture remains compatible',async({page,origin,state})=>{
      await page.goto(origin+'/tests/browser/storefront-customisations.html');
      await page.waitForFunction(()=>document.querySelector('#result')?.textContent!=='RUNNING');
      assert.match(await page.locator('#result').textContent(),/^PASS 15 browser assertions/);
      assert.equal(state.adds,0);
    });
  } finally {await browser.close();}
});
