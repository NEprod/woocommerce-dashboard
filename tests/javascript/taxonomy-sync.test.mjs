import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import vm from 'node:vm';
const {selectRows} = createRequire(import.meta.url)('../../app/static/assets/js/taxonomy-sync.js');
const row = (action, disabled = false) => ({dataset: {action}, checked: false, disabled});
test('initial load submits only the existing read-only preview form once', () => {
  let submissions = 0;
  const loading = {hidden: true}, view = {};
  const preview = {requestSubmit: () => { submissions += 1; }};
  const workspace = {dataset: {activeView: 'overview'}, querySelectorAll: () => [],
    querySelector: s => ({'[data-auto-preview]': preview, '[data-preview-loading]': loading,
                          '[data-preview-view]': view}[s] || null)};
  vm.runInNewContext(fs.readFileSync(new URL('../../app/static/assets/js/taxonomy-sync.js', import.meta.url), 'utf8'),
    {document: {querySelector: () => workspace}});
  assert.equal(submissions, 1);
  assert.equal(loading.hidden, false);
  assert.equal(view.value, 'overview');
});
test('group picks only eligible rows of one action; isolates other groups', () => {
  const group = [row('link'), row('create'), row(undefined), row('link', true)];
  const other = row('link');
  assert.equal(selectRows([...group, other], group, 'link', 50), '');
  assert.deepEqual(group.map(r => r.checked), [true, false, false, false]);
  assert.equal(other.checked, false);
  assert.match(selectRows(group, group, 'create', 50), /Clear/);
});
test('50 cap is atomic and selection count matches selected rows', () => {
  const rows = Array.from({length: 51}, () => row('create'));
  assert.match(selectRows(rows, rows, 'create', 50), /50/);
  assert.equal(rows.filter(r => r.checked).length, 0);
  assert.equal(selectRows(rows, rows.slice(0, 50), 'create', 50), '');
  assert.equal(rows.filter(r => r.checked).length, 50);
  assert.match(selectRows(rows, [rows[50]], 'create', 50), /50/);
});
test('imports cannot silently mix with creates or links', () => {
  const rows = [row('import'), row('link')];
  selectRows(rows, rows, 'import', 50);
  assert.match(selectRows(rows, rows, 'link', 50), /Clear/);
});
test('actual page listeners update selected count and clear selection', () => {
  const item = row('link');
  item.addEventListener = (_, callback) => { item.change = callback; };
  const count = {}, message = {}, preview = {};
  const form = {dataset: {limit: '50'}, querySelectorAll: s => s.includes('input') ? [item] : [],
    querySelector: s => s.includes('count') ? count : message,
    addEventListener: (event, callback) => { form[event] = callback; }};
  const workspace = {dataset: {activeView: 'overview'}, querySelectorAll: () => [],
    querySelector: s => s.includes('selection') ? form : preview};
  vm.runInNewContext(fs.readFileSync(new URL('../../app/static/assets/js/taxonomy-sync.js', import.meta.url), 'utf8'),
    {document: {querySelector: () => workspace}});
  item.checked = true; item.change();
  assert.equal(count.textContent, '1 / 50 selected');
  form.click({target: {closest: () => ({hasAttribute: () => true})}});
  assert.equal(count.textContent, '0 / 50 selected');
  assert.equal(item.checked, false);
});
