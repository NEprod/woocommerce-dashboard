import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js', import.meta.url), 'utf8');
const patchCode = source.slice(source.indexOf('  function documentPatch('), source.indexOf('  function clearErrors('));
const saveCode = source.slice(source.indexOf('  async function save('), source.indexOf('  async function retryScan('));

function editor({shared = false, acknowledged = true, status = 200, response = null} = {}) {
  const before = {price: 6.5, future: {empty: [], private: 'preserve'}, image_attributes: ['Style']};
  const after = {...before, description: 'Reviewed local copy'};
  const calls = [], feedback = [], children = [], progress = [];
  let dirty = true;
  const context = vm.createContext({structuredClone, busy: false, activeMode: 'guided', originalDocument: before,
    boot: {kind: shared ? 'shared' : 'override', can_save: true, revision: 'signed-source', save_url: '/edit_products/TEST-0001/save'},
    guidedDocument: () => after, validateDocument: async () => true,
    setBusy: (value) => {context.busy = value;}, setDirty: (value) => {dirty = value;}, populate: () => {},
    showErrors: () => {}, showFeedback: (...args) => feedback.push(args), startProgress: (id) => progress.push(id),
    retryScan: () => {throw new Error('scan retried automatically');},
    feedback: {appendChild: (child) => children.push(child)},
    document: {querySelector: (selector) => selector === '[data-acknowledge-shared]' ? shared ? {checked: acknowledged} : null : {content: 'test-csrf'},
      createElement: (name) => ({name, addEventListener(event, callback) {this.callback = callback;}})},
    window: {location: {href: 'http://localhost/editor'}},
    fetch: async (url, options) => {
      calls.push({url, options, payload: JSON.parse(options.body)});
      return {ok: status < 400, status, json: async () => response || {ok: true, authored: after, revision: 'new-source', run_id: 'local-run'}};
    }});
  vm.runInContext(patchCode + saveCode, context);
  return {context, calls, feedback, children, progress, dirty: () => dirty};
}

test('guided save submits a CSRF-protected signed patch without hidden-field replacement', async () => {
  const e = editor(); await vm.runInContext('save()', e.context);
  assert.equal(e.calls.length, 1);
  const request = e.calls[0];
  assert.equal(request.url, '/edit_products/TEST-0001/save');
  assert.equal(request.options.headers['X-CSRFToken'], 'test-csrf');
  assert.deepEqual(request.payload.data, {description: 'Reviewed local copy'});
  assert.deepEqual(request.payload.remove_paths, []);
  assert.equal(request.payload.revision, 'signed-source');
  assert.equal('replace' in request.payload, false);
  assert.equal(e.dirty(), false);
  assert.deepEqual(e.progress, ['local-run']);
});

test('shared scope must be acknowledged before any save request', async () => {
  const e = editor({shared: true, acknowledged: false}); await vm.runInContext('save()', e.context);
  assert.equal(e.calls.length, 0); assert.equal(e.dirty(), true);
  assert.equal(e.feedback.at(-1)[1], 'Confirm shared scope');
});

test('stale review retains the draft and asks for reload instead of overwriting', async () => {
  const e = editor({status: 409, response: {error: 'metadata_conflict', message: 'Source changed'}});
  await vm.runInContext('save()', e.context);
  assert.equal(e.dirty(), true); assert.equal(e.progress.length, 0);
  assert.equal(e.feedback.at(-1)[1], 'Reload and review your draft');
  assert.equal(e.children[0].href, 'http://localhost/editor');
});

test('post-save scan failure is not labelled unsaved and offers explicit retry only', async () => {
  const e = editor({status: 500, response: {error: 'metadata_saved_scan_pending', json_saved: true,
    message: 'Projection pending', revision: 'saved-source', scanner_url: '/scanner'}});
  await vm.runInContext('save()', e.context);
  assert.equal(e.feedback.at(-1)[1], 'JSON saved — scan needs recovery');
  assert.equal(e.context.boot.revision, 'saved-source');
  assert.equal(e.children.at(-1).textContent, 'Retry local scan only');
  assert.equal(e.calls.length, 1); assert.equal(e.progress.length, 0);
});
