import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=new URL('../../',import.meta.url);
const html=execFileSync('python3',['-c','import sys; sys.path.insert(0,"tests"); from test_profitability_display import render,example; print(render(example()))'],{cwd:root,encoding:'utf8'});
const css=(await Promise.all(['vendor/bootstrap/dist/css/bootstrap.min.css','css/custom.css','css/inventory.css'].map(p=>readFile(new URL('app/static/assets/'+p,root),'utf8')))).join('\n');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try{
  for(const width of [1440,390]){
    const page=await browser.newPage({viewport:{width,height:1000}});
    await page.setContent(`<style>${css}</style><main style="max-width:900px;margin:16px">${html}</main>`);
    const table=page.locator('.bom-cost-summary');
    const profit=page.locator('[data-profit-summary]');
    assert.match(await profit.innerText(),/Estimated profit per item\s+£2.66/);
    assert.doesNotMatch(await profit.innerText(),/4.62/);
    assert.ok(await profit.locator('strong').evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=24),'Profit is prominent');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await profit.screenshot({path:`/tmp/profit-per-item-${width}.png`});
    assert.equal(await table.locator('tbody tr').count(),7);
    assert.doesNotMatch(await table.innerText(),/Direct raw materials|Direct 3D-print material failure|Other \/ overhead/);
    const box=await table.evaluate(el=>{
      const p=el.closest('section').getBoundingClientRect(),t=el.getBoundingClientRect(),th=el.querySelector('th');
      return {left:t.left-p.left,right:p.right-t.right,padding:parseFloat(getComputedStyle(th).paddingLeft)};
    });
    assert.ok(box.left<=2&&box.right<=2,`Full-width header at ${width}px: ${JSON.stringify(box)}`);
    assert.ok(box.padding>0,'Readable cell padding preserved');
    await table.screenshot({path:`/tmp/production-cost-breakdown-${width}.png`});
    console.log(`PASS ${width}px: clear prominent profit, separate suggested comparison, zero categories hidden, full-width header`);
    await page.close();
  }
}finally{await browser.close();}
