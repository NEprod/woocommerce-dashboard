import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
const base='http://127.0.0.1:7521';
try {
  for(const [name,viewport] of [['desktop',{width:1440,height:900}],['mobile',{width:390,height:844}]]) {
    const context=await browser.newContext({viewport,serviceWorkers:'block'});
    await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
    const page=await context.newPage(),errors=[],requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>requests.push([request.method(),request.url()]));
    await page.goto(base+'/settings/tasks?task=shopify_products');
    await page.getByRole('button',{name:'Run Now',exact:true}).click();
    await page.waitForURL(/\/settings\/tasks\/runs\/[a-f0-9]+$/);
    const runUrl=page.url(),id=runUrl.split('/').at(-1);
    const advance=async(state,checked)=>{
      const response=await context.request.post(base+'/fixture/progress/'+id,{data:{state,checked}});assert.equal(response.status(),200);
    };
    await advance('running',1);
    await page.getByText('checked: 1',{exact:false}).first().waitFor();
    const details=page.locator('[data-task-run-report] details');
    await details.locator('summary').click();assert.equal(await details.evaluate(node=>node.open),true);
    await page.evaluate(()=>window.scrollTo(0,400));const scroll=await page.evaluate(()=>scrollY);
    await advance('running',2);
    await page.locator('[data-run-counters]').filter({hasText:'checked: 2'}).waitFor();
    assert.equal(await details.evaluate(node=>node.open),true);
    assert.ok(Math.abs((await page.evaluate(()=>scrollY))-scroll)<3);
    // Browser navigation leaves the worker alone; returning resumes this ID.
    await page.goto(base+'/settings/tasks');await page.goBack();
    await page.locator('[data-run-counters]').filter({hasText:'checked: 2'}).waitFor();
    await context.setOffline(true);
    await page.getByText('Offline. Saved progress is retained',{exact:false}).waitFor();
    await context.setOffline(false);
    await advance('completed',3);
    await page.getByText('Final saved result. Live updates stopped.',{exact:true}).waitFor();
    assert.match(await page.locator('[data-task-run-report]').innerText(),/Completed with findings/);
    const polls=requests.filter(([method,url])=>method==='GET' && url===runUrl).length;
    await page.waitForTimeout(4500);
    assert.equal(requests.filter(([method,url])=>method==='GET'&&url===runUrl).length,polls);
    assert.equal(requests.filter(([method,url])=>method==='POST'&&url.endsWith('/shopify_products/run')).length,1);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log(name+': accepted run followed; live checkpoints, scroll/details, navigation, offline recovery, terminal stop and one start PASS');
    await context.close();
  }
} finally {await browser.close();}
