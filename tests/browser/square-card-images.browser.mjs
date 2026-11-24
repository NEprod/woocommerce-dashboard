// Focused rendering check of the shared Collections/Components/Materials card CSS.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const css=await readFile(new URL('../../app/static/assets/css/custom.css',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--disable-background-networking']});
try {
  for (const width of [1440,390]) {
    const page=await browser.newPage({viewport:{width,height:1000}});
    const image=(w,h)=>`data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="100%" height="100%" fill="green"/><rect x="1" y="1" width="${w-2}" height="${h-2}" fill="none" stroke="orange" stroke-width="2"/></svg>`)}`;
    await page.setContent(`<style>${css}</style><section class="collections-grid">${[[800,200],[200,800],[400,400]].map(([w,h])=>`<article class="collection-card"><a class="collection-card-media" href="#"><img src="${image(w,h)}" alt="${w} by ${h}"></a><div class="collection-card-body">Card details</div></article>`).join('')}<article class="collection-card"><a class="collection-card-media" href="#"><span class="collection-image-fallback">G</span></a><div class="collection-card-body">No image</div></article></section>`);
    await page.locator('img').evaluateAll(imgs=>Promise.all(imgs.map(img=>img.decode())));
    const frames=await page.locator('.collection-card-media').evaluateAll(els=>els.map(el=>{
      const frame=el.getBoundingClientRect(),img=el.querySelector('img'),box=img?.getBoundingClientRect();
      return {width:frame.width,height:frame.height,image:box&&{width:box.width,height:box.height,fit:getComputedStyle(img).objectFit},next:el.nextElementSibling.getBoundingClientRect().top,bottom:frame.bottom};
    }));
    for(const frame of frames){
      assert.ok(Math.abs(frame.width-frame.height)<=1,'Square image area');
      assert.ok(frame.next>=frame.bottom-1,'Details remain below the image');
      if(frame.image){assert.equal(frame.image.fit,'contain');assert.ok(Math.abs(frame.image.height-frame.height)<=1);assert.ok(Math.abs(frame.image.width-frame.width)<=1);}
    }
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:`/tmp/square-card-images-${width}.png`,fullPage:true});
    console.log(`PASS ${width}px: square image/fallback frames; portrait, landscape and square images contained; no overflow`);
    await page.close();
  }
} finally {await browser.close();}
