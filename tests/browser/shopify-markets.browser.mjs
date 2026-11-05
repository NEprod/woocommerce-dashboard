// Run against tests/browser/royal_mail_fixture.py on loopback only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_BINARY});
const rows = [
  {id: 'gid://shopify/Market/1', name: 'Fictional United Kingdom', active: true},
  {id: 'gid://shopify/Market/2', name: 'Fictional Europe', active: true},
  {id: 'gid://shopify/Market/3', name: 'Fictional United States', active: true},
];
try {
  for (const [layout, viewport] of [['desktop', {width: 1440, height: 1000}], ['mobile', {width: 390, height: 844}]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://127.0.0.1:7524') return route.abort();
      if (url.pathname.endsWith('/shopify/markets/discover')) return route.fulfill({json: {markets: rows}});
      return route.continue();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:7524/edit_products/1/edit/shared');
    const section = page.locator('[data-market-editor]');
    const expand = () => section.locator('summary').click();
    const persist = async () => {
      await page.locator('[data-acknowledge-shared]').check();
      const saved = page.waitForResponse(r => r.url().endsWith('/save') && r.request().method() === 'POST');
      await page.locator('[data-save-metadata]:visible').last().click();
      assert.equal((await saved).status(), 200);
      await page.getByText('Local scan completed', {exact: true}).waitFor({timeout: 20000});
    };
    await expand();
    const managed = section.getByLabel('Manage Markets for this collection');
    await managed.check();
    for (const row of rows.slice(0, 2)) await section.getByLabel(row.name).check();
    assert.equal(await section.locator('[data-market-id]:checked').count(), 2);
    assert.equal(await page.locator('[data-discover-channels]').count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: `/tmp/tlc-markets-${layout}.png`});
    await persist();
    await page.reload();
    await expand();
    await section.getByLabel(rows[0].name).waitFor();
    assert.equal(await section.locator('[data-market-id]:checked').count(), 2);
    await managed.uncheck();
    await persist();
    await page.reload();
    await expand();
    assert.equal(await managed.isChecked(), false);
    assert.equal(await section.locator('[data-market-id]:checked').count(), 0);
    assert.deepEqual(errors, []);
    console.log(`${layout}: multi-select, scoped save, reload, unmanaged and no overflow passed`);
    await context.close();
  }
} finally {
  await browser.close();
}
