// Run against organisation_fixture.py only: fictional catalogue, outbound blocked.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = 'http://127.0.0.1:7503';
const output = process.env.TLC_BROWSER_OUTPUT || '/tmp/tlc-image-sync';
mkdirSync(output, {recursive: true});
const browser = await chromium.launch({headless: true,
  executablePath: process.env.TLC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--disable-background-networking']});

try {
  for (const [name, viewport] of [['desktop', {width: 1440, height: 900}],
    ['tablet', {width: 1024, height: 900}], ['phone', {width: 390, height: 844}]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin && ['GET', 'HEAD'].includes(route.request().method())
        ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let response = await page.goto(origin + '/collections/1');
    assert.equal(response.status(), 200);
    const action = page.getByRole('button', {name: 'Update Images'});
    assert.equal(await action.count(), 1);
    assert.match(await action.getAttribute('title'), /existing scoped scanner/);
    assert.equal(await action.locator('xpath=ancestor::form').getAttribute('action'), '/collections/1/update-images');
    await action.scrollIntoViewIfNeeded();
    await page.screenshot({path: `${output}/${name}-collection-update-images.png`});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: collection overflows`);

    response = await page.goto(origin + '/products/1/shopify/review');
    assert.equal(response.status(), 200);
    await page.locator('[data-preview-form]').waitFor();
    // The actual review/thumbnail rendering is covered with a mocked adapter in
    // pytest. Here only the shared table's responsive presentation is measured.
    await page.evaluate(() => {
      document.querySelector('[data-preview-result]').innerHTML = `<section class="workspace-card table-panel" data-review-plan>
        <header class="workspace-card-header table-panel-header"><h2>Fictional image review</h2></header>
        <div class="workspace-card-body"><details open><summary>Product · 1 proposed change</summary>
        <table class="table shopify-review-table"><thead><tr><th>Field</th><th>Current Shopify</th><th>Proposed TLC Value</th><th>Action / Status</th></tr></thead>
        <tbody><tr><th scope="row"><img class="shopify-review-thumbnail" src="/catalogue-images/products/1/gallery/0" alt="">Image · original.png</th>
        <td>Existing managed image</td><td>Replace with changed original</td><td>Replace</td></tr></tbody></table></details></div></section>`;
      document.dispatchEvent(new Event('tlc:review-ready'));
    });
    const layout = await page.locator('[data-review-plan]').evaluate(panel => {
      const table = panel.querySelector('table');
      const wrapper = table.closest('.operator-table-scroll, .table-responsive');
      return {pageWidth: document.documentElement.scrollWidth, viewport: innerWidth,
        wrapperWidth: wrapper?.clientWidth, scrollWidth: wrapper?.scrollWidth,
        tableWidth: table.getBoundingClientRect().width,
        rowWidth: table.tBodies[0].rows[0].getBoundingClientRect().width};
    });
    assert.ok(layout.pageWidth <= layout.viewport, `${name}: review page overflows ${JSON.stringify(layout)}`);
    assert.ok(layout.wrapperWidth > 0 && Math.abs(layout.rowWidth - layout.tableWidth) <= 2,
      `${name}: table wrapper/separator ${JSON.stringify(layout)}`);
    if (name === 'phone') assert.ok(layout.scrollWidth > layout.wrapperWidth,
      `phone: review must scroll locally ${JSON.stringify(layout)}`);
    await page.locator('[data-review-plan]').scrollIntoViewIfNeeded();
    await page.screenshot({path: `${output}/${name}-image-review.png`});
    assert.deepEqual(errors, [], `${name}: JavaScript errors`);
    await context.close();
  }
  console.log('Image workflow desktop, tablet and phone layout checks passed.');
} finally {
  await browser.close();
}
