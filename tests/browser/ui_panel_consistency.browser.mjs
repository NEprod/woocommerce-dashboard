// Run only against tests/browser/organisation_fixture.py (fictional, outbound-blocked).
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = 'http://127.0.0.1:7503';
const output = process.env.TLC_BROWSER_OUTPUT || '/tmp/tlc-ui-panels';
mkdirSync(output, {recursive: true});
const browser = await chromium.launch({headless: true, executablePath: process.env.TLC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--disable-background-networking']});
const results = [];

try {
  for (const [name, viewport] of [['desktop', {width: 1440, height: 950}], ['tablet', {width: 1024, height: 900}], ['phone', {width: 390, height: 844}]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin && ['GET', 'HEAD'].includes(route.request().method()) ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const paths = [
      ['dashboard', '/', '.recent-products'],
      ['workspace', '/products/1', '#resolved-metadata-title'],
      ['shared-editor', '/edit_products/1/edit/shared', '#pricing-title'],
      ['override-editor', '/edit_products/1/edit/override', '#pricing-title'],
      ['review', '/products/1/shopify/review', '[data-preview-form]'],
    ];
    const metrics = {};
    for (const [label, path, selector] of paths) {
      const response = await page.goto(origin + path, {waitUntil: 'domcontentloaded'});
      assert.equal(response.status(), 200, `${name}/${label} HTTP status`);
      await page.locator(selector).waitFor();
      if (label === 'review') {
        // The fixture has no Shopify connection. Exercise the existing review DOM/CSS
        // with fictional comparison rows; no preview or confirmation request is sent.
        await page.evaluate(() => {
          const variants = Array.from({length: 14}, (_, index) => `<details data-review-group="Variant ${index + 1}"><summary>Variant ${index + 1} · 1 proposed changes</summary></details>`).join('');
          document.querySelector('[data-preview-result]').innerHTML = `<section class="workspace-card table-panel" data-review-plan><header class="workspace-card-header table-panel-header"><h2>Fictional reviewed product</h2></header><div class="workspace-card-body"><details><summary>Status, sales channels and Markets</summary><table class="table shopify-review-table"><thead><tr><th>Field</th><th>Current Shopify</th><th>Proposed Value</th><th>Action / Status</th></tr></thead><tbody><tr><th scope="row">Online Store</th><td>Not published</td><td>Published</td><td>Update</td></tr></tbody></table></details>${variants}<details class="operator-help"><summary>Technical evidence</summary><p>Fictional readback</p></details></div></section>`;
          document.dispatchEvent(new Event('tlc:review-ready'));
        });
        assert.equal(await page.locator('[data-review-plan] > .workspace-card-body > details').first().evaluate(el => el.open), true, `${name}: ordinary comparison should open`);
        assert.equal(await page.locator('[data-review-group="Variant 1"]').evaluate(el => el.open), false, `${name}: large variant group should remain collapsed`);
        assert.equal(await page.locator('[data-review-plan] .operator-help').evaluate(el => el.open), false, `${name}: technical details should remain collapsed`);
      }
      const measure = await page.evaluate(({label}) => {
        const panel = label === 'dashboard' ? document.querySelector('.recent-products') : label === 'workspace' ? document.querySelector('#resolved-metadata-title').closest('.table-panel') : label.endsWith('editor') ? document.querySelector('#pricing-title').closest('.table-panel') : document.querySelector('[data-review-plan]');
        const header = panel?.querySelector(':scope > .table-panel-header');
        const tableRegion = panel?.querySelector('.table-responsive');
        const table = tableRegion?.querySelector('table');
        const rect = el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, width: r.width}; };
        return {
          pageWidth: document.documentElement.scrollWidth,
          viewportWidth: innerWidth,
          panel: panel ? rect(panel) : null,
          header: header ? rect(header) : null,
          headerColour: header ? getComputedStyle(header).backgroundColor : null,
          tableRegion: tableRegion ? {client: tableRegion.clientWidth, scroll: tableRegion.scrollWidth, ...rect(tableRegion)} : null,
          table: table ? rect(table) : null,
          tableHeadingColour: table ? getComputedStyle(table.querySelector('thead th')).backgroundColor : null,
          rowWidth: table?.querySelector('tbody tr') ? rect(table.querySelector('tbody tr')).width : null,
        };
      }, {label});
      assert.ok(measure.pageWidth <= measure.viewportWidth, `${name}/${label}: page-wide horizontal overflow ${JSON.stringify(measure)}`);
      if (measure.panel) {
        assert.ok(Math.abs(measure.header.left - measure.panel.left) <= 2, `${name}/${label}: header left inset ${JSON.stringify(measure)}`);
        assert.ok(Math.abs(measure.header.right - measure.panel.right) <= 2, `${name}/${label}: header right inset ${JSON.stringify(measure)}`);
      }
      if (label === 'workspace' || label === 'review') {
        assert.ok(measure.tableRegion, `${name}: resolved table scroll container missing`);
        assert.ok(Math.abs(measure.tableRegion.left - measure.panel.left) <= 2, `${name}: resolved table inset`);
        assert.ok(Math.abs(measure.rowWidth - measure.table.width) <= 2, `${name}: row separator inset`);
        if (name === 'phone') assert.ok(measure.tableRegion.scroll > measure.tableRegion.client, `phone: ${label} table must scroll locally`);
        if (label === 'workspace') assert.equal(await page.locator('#resolved-metadata-title').evaluate(el => el.closest('.table-panel').querySelector('.provenance-legend') === null), true);
      }
      if (label.endsWith('editor')) assert.equal(await page.locator('#pricing-title').evaluate(el => el.closest('.table-panel').querySelector(':scope > .table-panel-header') === el.closest('header')), true);
      metrics[label] = measure;
      await page.locator(label === 'review' ? '[data-review-plan] .operator-table-scroll' : selector).scrollIntoViewIfNeeded();
      await page.screenshot({path: `${output}/${name}-${label}.png`});
      if (label === 'review') {
        if (name === 'phone') await page.locator('.mobile-bottom-nav').evaluate(el => { el.style.visibility = 'hidden'; });
        await page.locator('[data-review-plan] .operator-table-scroll').screenshot({path: `${output}/${name}-review-table.png`});
      }
    }
    for (const label of ['workspace', 'shared-editor', 'override-editor', 'review']) {
      assert.equal(metrics[label].headerColour, metrics.dashboard.headerColour, `${name}/${label}: header colour differs from Dashboard`);
    }
    if (name !== 'phone') for (const label of ['workspace', 'review']) assert.equal(metrics[label].tableHeadingColour, metrics.dashboard.tableHeadingColour, `${name}/${label}: table heading differs from Dashboard`);
    assert.deepEqual(errors, [], `${name}: browser script errors`);
    results.push({name, widths: Object.fromEntries(Object.entries(metrics).map(([k, v]) => [k, `${v.pageWidth}/${v.viewportWidth}`])), resolvedTable: metrics.workspace.tableRegion && `${metrics.workspace.tableRegion.client}/${metrics.workspace.tableRegion.scroll}`});
    await context.close();
  }
  console.log(JSON.stringify(results));
} finally {
  await browser.close();
}
