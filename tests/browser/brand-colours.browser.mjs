// Isolated fixture only: python3 tests/browser/organisation_fixture.py.
// Compares the pre-brand release with current CSS; never opens merchant tabs.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdirSync, writeFileSync} from 'node:fs';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseline = process.env.BRAND_BASELINE || '640d6071164b37bb6cc795ba34de5786eda5085a';
const origin = 'http://127.0.0.1:7503';
const output = process.env.BRAND_EVIDENCE || '/tmp/tlc-brand-colours';
mkdirSync(output, {recursive:true});
const sheets = ['custom.css','metadata-taxonomy.css','taxonomy.css'];
const oldCSS = Object.fromEntries(sheets.map(name => [name,execFileSync('git', ['show',`${baseline}:app/static/assets/css/${name}`],{encoding:'utf8'})]));
const semantic = [
  ...['','is-warning','is-neutral'].map(x=>`status-pill ${x}`),
  ...['','is-active','is-missing','is-override','is-shared','is-none','is-intent-published','is-intent-draft','is-intent-unresolved','is-shopify-success','is-shopify-warning','is-shopify-info','is-shopify-danger','is-shopify-neutral'].map(x=>`catalogue-pill ${x}`),
  ...['','status-healthy','status-succeeded','status-issues','status-missing','status-running','status-invalid','status-failed','status-partial','status-interrupted','status-marker_recovery_required','status-sent','status-disabled','status-not_configured','status-not_recorded'].map(x=>`status-badge ${x}`),
  ...['','is-active','is-succeeded','is-missing','is-failed','is-interrupted','is-partial','is-override'].map(x=>`compact-status ${x}`),
  ...['','is-completed','is-partial','is-failed'].map(x=>`status-pill operation-status ${x}`),
  ...['info','warning','danger','success'].map(x=>`alert alert-${x}`),
  'badge bg-info','readiness-mark','value-state is-inherited','form-alert','form-alert form-alert-success',
];
const specimen = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/assets/vendor/bootstrap/dist/css/bootstrap.min.css">${sheets.map(n=>`<link rel="stylesheet" href="/static/assets/css/${n}">`).join('')}<main class="app-main"><h1>Theme checks</h1><section class="workspace-panel">${semantic.map(c=>`<p><span data-semantic class="${c}">${c}</span></p>`).join('')}</section><section class="feature-panel"><button id="primary" class="btn btn-primary">Primary action</button><button id="disabled" class="btn btn-primary" disabled>Disabled</button><a class="collection-detail-link" href="#">View collection</a></section><section class="workspace-panel"><button id="light-primary" class="btn btn-primary">Primary action</button><button id="accent" class="btn btn-accent">Accent action</button><label class="form-label" for="field">Field label</label><input id="field" class="form-control" placeholder="Placeholder"><input class="form-check-input" type="checkbox" checked></section></main>`;
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
const report = {baseline, pages:[], semanticSamples:semantic.length, contrast:[]};
const colours = ['color','background-color','border-top-color','border-right-color','border-bottom-color','border-left-color','outline-color','accent-color'];
function ratio(a,b) {
  const luminance = rgb => rgb.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
  const x=luminance(a), y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
async function setup(viewport, before) {
  const context = await browser.newContext({viewport,reducedMotion:'reduce'});
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if(url.origin !== origin)return route.abort();
    if(url.pathname==='/__brand-specimen')return route.fulfill({contentType:'text/html; charset=utf-8',body:specimen});
    const name=url.pathname.split('/').at(-1);
    if(before && oldCSS[name])return route.fulfill({contentType:'text/css; charset=utf-8',body:oldCSS[name]});
    // A colour check never submits edits, starts scans or contacts Shopify.
    if(!['GET','HEAD'].includes(route.request().method()))return route.abort();
    return route.continue();
  });
  return context;
}
async function navigate(page,path) {
  const response=await page.goto(origin+path,{waitUntil:'networkidle'});
  assert.equal(response.status(),200,`${path}: HTTP ${response.status()}`);
  await page.evaluate(()=>document.fonts.ready);
}
async function geometry(page,selector='body *') {
  return page.evaluate(selector=>[...document.querySelectorAll(selector)].filter(e=>e.getClientRects().length && !e.closest('svg')).map(e=>{
    const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    return [e.tagName,e.className,...[r.x,r.y,r.width,r.height].map(n=>Math.round(n*100)/100),...['fontFamily','fontSize','fontWeight','lineHeight','position','padding','margin','borderWidth','borderRadius','gap','display','overflowX'].map(k=>s[k])];
  }),selector);
}
try {
  for (const [label,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
    const contexts=await Promise.all([setup(viewport,true),setup(viewport,false)]);
    const pages=await Promise.all(contexts.map(c=>c.newPage()));
    for(const [name,path] of [['dashboard','/'],['products','/products'],['collections','/collections'],['override','/edit_products/1/edit/override'],['shared','/edit_products/1/edit/shared'],['scanner','/scanner'],['operations','/operations'],['issues','/operations/issues'],['orders','/orders/'],['packages','/packaging/'],['package-editor','/packaging/new'],['connection','/settings/commerce'],['discovery','/shopify/discovery'],['shopify-review','/products/1/shopify/review'],['taxonomy','/taxonomy'],['taxonomy-review','/taxonomy/sync'],['intake','/image-preparation'],['settings','/settings']]) {
      await Promise.all(pages.map(p=>navigate(p,path)));
      assert.deepEqual(await geometry(pages[1]),await geometry(pages[0]),`${label}/${name}: layout changed`);
      const widths=await Promise.all(pages.map(p=>p.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))));
      assert.equal(widths[1].scroll,widths[0].scroll,`${label}/${name}: new overflow`);
      assert.ok(widths[1].scroll<=widths[1].width,`${label}/${name}: horizontal overflow`);
      assert.equal(await pages[1].locator('body').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(248, 243, 231)');
      if(name==='dashboard'){
        const target=pages[1].locator('.operational-panel a').first();
        const link=await target.evaluate(e=>[getComputedStyle(e).color,getComputedStyle(e.closest('.operational-panel')).backgroundColor]);
        assert.ok(ratio(...link)>=4.5,'Dark-panel link contrast');
        await target.hover();
        const hover=await target.evaluate(e=>[getComputedStyle(e).color,getComputedStyle(e.closest('.operational-panel')).backgroundColor]);
        assert.ok(ratio(...hover)>=4.5,'Dark-panel hover link contrast');
        await pages[1].mouse.move(0,0);
      }
      if(['dashboard','override','scanner'].includes(name)){
        for(let i=0;i<2;i++)await pages[i].screenshot({path:`${output}/${label}-${name}-${i?'after':'before'}.png`});
      }
      if(name==='override' && label==='mobile') {
        const save=await pages[1].locator('.metadata-mobile-savebar').boundingBox();
        const nav=await pages[1].locator('.mobile-bottom-nav').boundingBox();
        assert.ok(save && nav && save.y+save.height<=nav.y,'Save bar/nav overlap');
        await Promise.all(pages.map(p=>p.locator('[data-customisation-designer]').scrollIntoViewIfNeeded()));
        for(let i=0;i<2;i++)await pages[i].screenshot({path:`${output}/mobile-editor-cards-${i?'after':'before'}.png`});
      }
      if(name==='override') {
        for(const page of pages){
          await page.locator('[data-custom-add]').click();
          assert.equal(await page.locator('[data-custom-field-dialog]').isVisible(),true);
        }
        const modal='[data-custom-field-dialog], [data-custom-field-dialog] *';
        assert.deepEqual(await geometry(pages[1],modal),await geometry(pages[0],modal),`${label}: modal layout changed`);
        for(let i=0;i<2;i++)await pages[i].screenshot({path:`${output}/${label}-modal-${i?'after':'before'}.png`});
      }
      report.pages.push({viewport:label,page:name,identicalLayout:true,noHorizontalOverflow:true});
    }
    await Promise.all(pages.map(p=>navigate(p,'/__brand-specimen')));
    const snapshots=await Promise.all(pages.map(p=>p.locator('[data-semantic]').evaluateAll((els,props)=>els.map(e=>[e.className,...props.map(k=>getComputedStyle(e).getPropertyValue(k))]),colours)));
    assert.deepEqual(snapshots[1],snapshots[0],`${label}: semantic colours changed`);
    assert.deepEqual(await geometry(pages[1]),await geometry(pages[0]),`${label}: specimen layout changed`);
    // CSSOM comparison also covers hidden states and all responsive rules.
    const rules=await Promise.all(pages.map(p=>p.evaluate(()=>{
      function flatten(list){return [...list].flatMap(r=>r.style&&r.selectorText?[[r.selectorText,[...r.style].sort().map(k=>[k,r.style.getPropertyValue(k),r.style.getPropertyPriority(k)])],...flatten(r.cssRules||[])]:r.cssRules?[[r.conditionText||r.name||'group'],...flatten(r.cssRules)]:[]);}
      return [...document.styleSheets].flatMap(s=>flatten(s.cssRules));
    })));
    const clean=rules.map(list=>list.map(([selector,decls])=>[selector,decls?.filter(([k])=>!k.startsWith('--color-brand-'))]));
    const added=clean[1].filter(([selector])=>!clean[0].some(([old])=>old===selector));
    for(const [selector,decls] of added){
      assert.ok(selector.startsWith(':where('),'Unexpected new selector');
      assert.deepEqual(decls.map(d=>d[0]),['color'],'Only contextual contrast colour rules may be added');
    }
    const existing=clean[1].filter(([selector])=>clean[0].some(([old])=>old===selector));
    assert.equal(clean[0].length,existing.length);
    for(let i=0;i<clean[0].length;i++){
      assert.equal(clean[0][i][0],existing[i][0],'Selector/structure changed');
      const [selector,old]=clean[0][i],next=existing[i][1];
      if(!old)continue;
      assert.deepEqual(old.map(d=>d[0]),next.map(d=>d[0]),`${selector}: declarations added/removed`);
      for(let j=0;j<old.length;j++){
        assert.equal(old[j][2],next[j][2],`${selector}: cascade priority changed`);
        if(old[j][1]===next[j][1])continue;
        const key=old[j][0];
        assert.ok(key.endsWith('color') || /^(?:--color-|--focus-ring$|background$|outline$|box-shadow$)/.test(key),`${selector}: non-colour change ${key}`);
        const strip=s=>s.replace('var(--border-default)','1px solid COLOUR').replace(/#[\da-f]{3,8}\b|rgba?\([^)]*\)|var\(--(?:color-[\w-]+|focus-ring)\)/gi,'COLOUR');
        // Pending var() shorthand substitutions serialize longhand colours as
        // empty strings in CSSOM. Colour-only properties cannot alter geometry.
        if(!key.endsWith('color'))assert.equal(strip(old[j][1]),strip(next[j][1]),`${selector}: geometry in colour declaration changed`);
        assert.ok(!/(?:pill|badge|alert|status-|operation-status|readiness-mark)/.test(selector),`${selector}: semantic rule changed`);
      }
    }
    for(const selector of ['#light-primary','#accent','#field']){
      const colour=await pages[1].locator(selector).evaluate(e=>{const s=getComputedStyle(e);return {fg:s.color,bg:s.backgroundColor,border:s.borderTopColor};});
      const contrast=ratio(colour.fg,colour.bg);assert.ok(contrast>=4.5,`${selector}: text contrast ${contrast}`);
      report.contrast.push({viewport:label,selector,ratio:Number(contrast.toFixed(2))});
      if(selector==='#field')assert.ok(ratio(colour.border,colour.bg)>=3,'Input boundary contrast');
    }
    await pages[1].locator('#light-primary').hover();
    const hovered=await pages[1].locator('#light-primary').evaluate(e=>{const s=getComputedStyle(e);return [s.color,s.backgroundColor];});
    assert.ok(ratio(...hovered)>=4.5);
    await pages[1].locator('#field').focus();
    assert.equal(await pages[1].locator('#field').evaluate(e=>getComputedStyle(e).borderTopColor),'rgb(31, 61, 46)');
    for(let i=0;i<2;i++)await pages[i].screenshot({path:`${output}/${label}-states-${i?'after':'before'}.png`,fullPage:true});
    await Promise.all(contexts.map(c=>c.close()));
  }
  writeFileSync(`${output}/results.json`,JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {await browser.close();}
