// Read-only visual smoke checks against the fictional local catalogue fixture.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = 'http://127.0.0.1:7503';
const output = process.env.TLC_BROWSER_OUTPUT || '/tmp/tlc-final-ui-sweep';
mkdirSync(output, {recursive: true});
const browser = await chromium.launch({headless: true, executablePath: process.env.TLC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--disable-background-networking']});
const expected = ['Catalogue', 'Shopify', 'Operations', 'Settings', 'System'];

try {
  const report = [];
  for (const [viewportName, viewport] of [['desktop', {width: 1440, height: 900}], ['tablet', {width: 1024, height: 900}], ['phone', {width: 390, height: 844}]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin && ['GET', 'HEAD'].includes(route.request().method()) ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/');
    const groupNames = await page.locator('.app-sidebar [data-navigation-group]').evaluateAll(nodes => nodes.map(node => node.dataset.navigationGroup));
    assert.deepEqual(groupNames, expected, `${viewportName} navigation order`);
    const links = await page.locator('.app-sidebar .sidebar-child').evaluateAll(nodes => nodes.map(node => ({label: node.textContent.trim(), href: node.getAttribute('href')})));
    const destinations = [{label: 'Dashboard', href: '/'}, ...links];
    const mobileLabels = await page.locator('.mobile-bottom-nav .mobile-primary-link span').allTextContents();
    assert.deepEqual(mobileLabels, ['Dashboard', 'Products', 'Orders', 'Production', 'More'], `${viewportName} mobile shortcut order`);
    for (const {label, href} of destinations) {
      const response = await page.goto(origin + href, {waitUntil: 'domcontentloaded'});
      const metrics = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        viewport: innerWidth,
        tables: [...document.querySelectorAll('table')].map(node => ({className: node.className, parent: node.parentElement?.className, header: !!node.tHead})),
        panels: document.querySelectorAll('.table-panel, .workspace-panel, .app-card, .relationship-browser').length,
      }));
      report.push({viewport: viewportName, label, href, status: response.status(), ...metrics});
      if (response.status() === 200) {
        assert.ok(metrics.width <= metrics.viewport, `${viewportName}/${label} body-level overflow: ${metrics.width} > ${metrics.viewport}`);
        if (['Relationships', 'Shipping Packages'].includes(label)) {
          const tableLayout = await page.locator('.table-panel > .table-responsive').first().evaluate(wrapper => {
            const table = wrapper.querySelector('table');
            const panel = wrapper.closest('.table-panel');
            const heading = panel.querySelector(':scope > .table-panel-header');
            return {
              nativeRows: table.tBodies[0].rows.length,
              headings: table.tHead.rows[0].cells.length,
              wrapperWidth: wrapper.clientWidth,
              tableWidth: table.getBoundingClientRect().width,
              panelWidth: panel.getBoundingClientRect().width,
              headingWidth: heading.getBoundingClientRect().width,
              scrollWidth: wrapper.scrollWidth,
            };
          });
          assert.ok(tableLayout.nativeRows > 0 && tableLayout.headings >= 5, `${viewportName}/${label} must retain a real populated table`);
          assert.ok(Math.abs(tableLayout.headingWidth - tableLayout.panelWidth) <= 4, `${viewportName}/${label} full-width panel header: ${JSON.stringify(tableLayout)}`);
          if (viewportName === 'phone') {
            assert.ok(tableLayout.scrollWidth > tableLayout.wrapperWidth, `${label} table must scroll independently`);
            assert.ok(tableLayout.tableWidth > tableLayout.wrapperWidth, `${label} columns must remain available`);
          }
        }
        const slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');
        await page.screenshot({path: `${output}/${viewportName}-${slug}.png`, fullPage: false});
      }
    }
    assert.deepEqual(errors, [], `${viewportName} JavaScript errors`);
    await context.close();
  }
  // The main fictional catalogue has no saved Orders. A separate disposable fixture
  // supplies one synthetic order so the populated table is also measured.
  const ordersOrigin = process.env.TLC_ORDERS_ORIGIN;
  if (ordersOrigin) {
    for (const [name, viewport] of [['desktop', {width: 1440, height: 900}], ['phone', {width: 390, height: 844}]]) {
      const context = await browser.newContext({viewport});
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.origin === ordersOrigin ? route.continue() : route.abort();
      });
      const page = await context.newPage();
      await page.goto(ordersOrigin + '/login');
      await page.locator('#username').fill('orders-fixture');
      await page.locator('#password').fill('fictional-browser-only-password');
      await page.locator('form').first().locator('[type="submit"]').click();
      await page.goto(ordersOrigin + '/orders/');
      const layout = await page.locator('.orders-table').evaluate(table => {
        const wrapper = table.closest('.table-responsive');
        const panel = wrapper.closest('.table-panel');
        return {rows: table.tBodies[0].rows.length, headings: table.tHead.rows[0].cells.length,
          display: getComputedStyle(table.tBodies[0].rows[0]).display,
          page: document.documentElement.scrollWidth, viewport: innerWidth,
          scroll: wrapper.scrollWidth, client: wrapper.clientWidth,
          headerWidth: panel.querySelector(':scope > .table-panel-header').getBoundingClientRect().width,
          panelWidth: panel.getBoundingClientRect().width};
      });
      assert.ok(layout.rows > 0 && layout.headings === 6 && layout.display === 'table-row', `${name}/Orders native table: ${JSON.stringify(layout)}`);
      assert.ok(layout.page <= layout.viewport, `${name}/Orders body overflow: ${JSON.stringify(layout)}`);
      assert.ok(Math.abs(layout.headerWidth - layout.panelWidth) <= 4, `${name}/Orders panel header: ${JSON.stringify(layout)}`);
      if (name === 'phone') assert.ok(layout.scroll > layout.client, `phone/Orders must scroll locally: ${JSON.stringify(layout)}`);
      await page.screenshot({path: `${output}/${name}-orders-populated.png`, fullPage: false});
      await context.close();
    }
  }
  console.log(JSON.stringify({pages: report.length, successful: report.filter(row => row.status === 200).length, viewports: ['desktop', 'tablet', 'phone'], checkedTables: ordersOrigin ? ['Relationships', 'Shipping Packages', 'Orders'] : ['Relationships', 'Shipping Packages']}, null, 2));
} finally { await browser.close(); }
