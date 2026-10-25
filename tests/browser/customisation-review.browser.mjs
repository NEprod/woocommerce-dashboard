// Run against customisation_review_fixture.py only; never submits a review.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync, writeFileSync} from 'node:fs';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const out='/tmp/tlc-customisation-review';mkdirSync(out,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BINARY});
const results=[];
try{
  for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport,reducedMotion:'reduce'});
    const writes=[];
    await context.route('**/*',route=>{
      const request=route.request();
      if(!['GET','HEAD'].includes(request.method())){writes.push(request.url());return route.abort();}
      return new URL(request.url()).origin==='http://127.0.0.1:7513'?route.continue():route.abort();
    });
    const page=await context.newPage();
    for(const scenario of ['changes','matching','unavailable','hash-only','reordered','long']){
      const response=await page.goto(`http://127.0.0.1:7513/fixture/customisation-review/${scenario}`,{waitUntil:'networkidle'});
      assert.equal(response.status(),200);
      const review=page.locator('[data-customisation-review]');
      assert.equal(await review.count(),1);
      await review.scrollIntoViewIfNeeded();
      const text=await review.innerText();
      assert.match(text,/Customer customisations/);
      assert.doesNotMatch(text,/[a-f0-9]{64}/,'Technical hashes must be collapsed initially');
      if(scenario==='changes'){
        assert.match(text,/Recipient Name — Changed/);
        assert.match(text,/Required: No → Yes/);
        assert.match(text,/Personal Message — Added · Optional text field/);
        await review.locator('[data-customisation-matching] > summary').click();
        assert.match(await review.innerText(),/Age · age/);
        assert.equal(await page.locator('input[name="acknowledge"][required]').count(),1);
        assert.equal(await page.locator('input[name="review"]').count(),1);
      }
      if(scenario==='unavailable'){
        assert.match(text,/previous customer customisation field settings are unavailable/);
        assert.equal(await review.locator('[data-customisation-changes]').count(),0);
      }
      if(['matching','hash-only'].includes(scenario)){
        assert.equal(await review.locator('[data-customisation-changes]').count(),0);
        assert.match(text,/No customer-facing field differences/);
      }
      if(scenario==='reordered')assert.match(text,/Position: 3 → 2/);
      if(scenario==='long')assert.equal(await review.locator('img').count(),0,'Remote labels/help must be escaped');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:`${out}/${name}-${scenario}.png`});
      await review.locator('[data-customisation-technical] > summary').click();
      assert.match(await review.innerText(),/[a-f0-9]{64}/);
      await review.getByText('Reviewed public configuration',{exact:true}).click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      results.push({viewport:name,scenario,noOverflow:true,hashesCollapsed:true,technicalDetailsAvailable:true});
    }
    assert.deepEqual(writes,[],'No mutations or confirmation POSTs');
    await context.close();
  }
  writeFileSync(`${out}/results.json`,JSON.stringify(results,null,2));
  console.log(JSON.stringify(results));
}finally{await browser.close();}
