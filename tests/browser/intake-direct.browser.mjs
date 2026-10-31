// Local disposable fixture only. Never opens a user's live browser context.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const evidence=[];
try {
  for (const [name,viewport,prefix] of [['Desktop',{width:1440,height:1000},'DESK-'],['Mobile',{width:390,height:844},'MOB-']]) {
    const context=await browser.newContext({viewport});
    await context.route('**/*', route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
    const page=await context.newPage(); const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const source='Dogs '+name;
    async function open(stage) { await page.goto(`http://127.0.0.1:7518/image-preparation/stage/${stage}?path=${encodeURIComponent(source)}`); }
    async function accept() {
      await page.locator('[data-intake-accept] button:not([disabled])').waitFor();
      await page.locator('[data-intake-accept] button').click();
      await page.waitForURL('**/image-preparation/result/**');
      await page.getByText('Original files prepared in place.',{exact:false}).waitFor();
    }
    await open('group');
    await page.screenshot({path:`/tmp/tlc-intake-${name.toLowerCase()}-group.png`,fullPage:true});
    await accept();
    await open('folders');
    await page.getByRole('textbox',{name:'Folder name prefix',exact:true}).fill('TLC ');
    await page.locator('[data-intake-preview]').getByText(/TLC hound/).first().waitFor();
    await accept();
    await open('rename');
    await page.getByRole('textbox',{name:'Filename prefix',exact:true}).fill(prefix);
    await page.locator('[data-intake-preview]').getByText(/tlc_hound_01\.png/).first().waitFor();
    await accept();
    await open('metadata');
    await page.locator('#prepared-sku-prefix').fill(prefix);
    await page.locator('#prepared-collection-type').selectOption('Simple');
    await page.locator('#prepared-price').fill('3.50');
    await page.locator('#prepared-live').selectOption('false');
    await page.locator('[data-intake-accept] button:not([disabled])').waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`/tmp/tlc-intake-${name.toLowerCase()}-json.png`,fullPage:true});
    await accept();
    await open('handoff');
    await page.locator('[data-intake-accept] button').click();
    await page.waitForURL('**/image-preparation/result/**');
    await page.getByRole('link',{name:'succeeded',exact:true}).waitFor({timeout:30000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`/tmp/tlc-intake-${name.toLowerCase()}-registered.png`,fullPage:true});
    const data=await (await context.request.get('http://127.0.0.1:7518/api/edit_products')).text();
    assert.ok(data.includes(prefix+'0001'));
    assert.deepEqual(errors,[]);
    evidence.push({layout:name,reactiveGroupingFoldersImagesJSON:true,acceptedOriginalsInPlace:true,scopedRegistration:true,noHorizontalOverflow:true,consoleErrors:errors});
    await context.close();
  }
  console.log(JSON.stringify(evidence));
} finally {await browser.close();}
