// Isolated fictional fixture only; no external requests or task execution.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.TLC_PLAYWRIGHT_MODULE || '/Users/dalethomas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = 'http://127.0.0.1:7503';
const output = process.env.TLC_BROWSER_OUTPUT || '/tmp/tlc-navigation-system';
mkdirSync(output, {recursive: true});
const browser = await chromium.launch({headless: true, executablePath: process.env.TLC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});

try {
  for (const [name, viewport] of [['desktop', {width: 1440, height: 900}], ['tablet', {width: 1024, height: 850}], ['phone', {width: 390, height: 844}]]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin && ['GET', 'HEAD'].includes(route.request().method()) ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const [label, path] of [['settings', '/settings'], ['scheduled', '/settings/tasks'], ['connections', '/settings/commerce'], ['status', '/system/status'], ['tasks', '/system/tasks'], ['history', '/operations'], ['scanner', '/scanner']]) {
      const response = await page.goto(origin + path, {waitUntil: 'domcontentloaded'});
      assert.equal(response.status(), 200, `${name}/${label} response`);
      const widths = await page.evaluate(() => ({page: document.documentElement.scrollWidth, viewport: innerWidth}));
      assert.ok(widths.page <= widths.viewport, `${name}/${label} page overflow: ${JSON.stringify(widths)}`);
      await page.screenshot({path: `${output}/${name}-${label}.png`, fullPage: false});
    }
    await page.goto(origin + '/system/tasks');
    const menu = name === 'phone' ? page.locator('#appNavigation') : page.locator('.app-sidebar');
    if (name === 'phone') await page.getByRole('button', {name: 'Open more navigation'}).click();
    assert.equal(await menu.locator('[data-navigation-group="System"]').evaluate(el => el.open), true, `${name} deep-link section`);
    await menu.locator('[data-navigation-group="Settings"] summary').click();
    assert.equal(await menu.locator('[data-navigation-group="Settings"]').evaluate(el => el.open), true, `${name} opened Settings`);
    await page.waitForFunction(() => {
      const visible = innerWidth <= 767 ? document.querySelector('#appNavigation') : document.querySelector('.app-sidebar');
      return !visible?.querySelector('[data-navigation-group="System"]')?.open;
    });
    assert.equal(await menu.locator('[data-navigation-group="System"]').evaluate(el => el.open), false, `${name} closed System`);
    assert.deepEqual(errors, [], `${name} script errors`);
    await context.close();
  }
  const context = await browser.newContext({viewport: {width: 1440, height: 900}});
  let scheduleWrites = 0;
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (route.request().method() === 'POST' && url.pathname === '/settings/tasks/shopify_products') {
      scheduleWrites++;
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({enabled: false, interval: 30})});
    }
    return ['GET', 'HEAD'].includes(route.request().method()) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  await page.goto(origin + '/settings/tasks');
  assert.equal(scheduleWrites, 0, 'opening settings never saves or starts a task');
  await page.locator('#schedule-shopify_products select[name="interval"]').selectOption('30');
  await page.locator('#schedule-shopify_products input[name="enabled"]').uncheck();
  assert.equal(await page.locator('[data-schedule-state="shopify_products"]').textContent(), 'Unsaved changes');
  await page.locator('[form="schedule-shopify_products"]').click();
  await page.locator('[data-schedule-state="shopify_products"]').getByText('Saved').waitFor();
  assert.equal(scheduleWrites, 1, 'only the selected task save handler receives one request');
  await context.close();
  console.log('navigation/settings/system browser checks: 3 viewport widths, 7 routes each, accordion, overflow and one isolated per-task save passed');
} finally { await browser.close(); }
