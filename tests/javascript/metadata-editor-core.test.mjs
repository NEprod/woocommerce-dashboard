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
  const calls = [], feedback = [], children = [], progress = [], events = [];
  let dirty = true;
  const context = vm.createContext({structuredClone, Event, busy: false, activeMode: 'guided', originalDocument: before,
    boot: {kind: shared ? 'shared' : 'override', can_save: true, revision: 'signed-source', save_url: '/edit_products/TEST-0001/save'},
    guidedDocument: () => after, validateDocument: async () => true,
    setBusy: (value) => {context.busy = value;}, setDirty: (value) => {dirty = value;}, populate: () => {},
    showErrors: () => {}, showFeedback: (...args) => feedback.push(args), startProgress: (id) => progress.push(id),
    retryScan: () => {throw new Error('scan retried automatically');},
    feedback: {appendChild: (child) => children.push(child)},
    document: {querySelector: (selector) => selector === '[data-acknowledge-shared]' ? shared ? {checked: acknowledged} : null : {content: 'test-csrf'},
      createElement: (name) => ({name, addEventListener(event, callback) {this.callback = callback;}})},
    window: {location: {href: 'http://localhost/editor'},dispatchEvent:event=>events.push(event.type)},
    fetch: async (url, options) => {
      calls.push({url, options, payload: JSON.parse(options.body)});
      return {ok: status < 400, status, json: async () => response || {ok: true, authored: after, revision: 'new-source', run_id: 'local-run'}};
    }});
  vm.runInContext(patchCode + saveCode, context);
  return {context, calls, feedback, children, progress, events, dirty: () => dirty};
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
  assert.deepEqual(e.events,['tlc:local-metadata-saved']);
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

const guidedCode = source.slice(source.indexOf('  function guidedDocument('), source.indexOf('  function documentPatch('));
const valuesCode = source.slice(source.indexOf('  function guidedValues('), source.indexOf('  function guidedDocument('));
function customsEditor(value, enabled=true, field='harmonized_system_code', marketFixture={}) {
  const control={dataset:{field},value};
  const context=vm.createContext({structuredClone,window:{},isShared:false,
    authored:{[field]:'001234',future:{keep:true}},initialValues:{[field]:'001234',future:{keep:true}},
    knownFields:new Set([field]),customisationDesigner:null,packagingDesigner:null,finishedDesigner:null,
    guided:{querySelectorAll:selector=>selector==='[data-field]'?[control]:[]},
    overrideEnabled:key=>enabled&&key===field,attributeValues:()=>({}),
    modifierValues:()=>({}),explicitVariation:false,marketEditor:null,...marketFixture});
  vm.runInContext(valuesCode+guidedCode,context);
  return JSON.parse(vm.runInContext('JSON.stringify(guidedDocument())',context));
}
test('HS Code remains text with leading zeros and changes only its authored scalar',()=>{
  assert.ok(source.includes('"harmonized_system_code"'));
  assert.deepEqual(customsEditor('0012345678901'),{harmonized_system_code:'0012345678901',future:{keep:true}});
});
test('blank or disabled HS override restores inheritance rather than inventing remote clearing',()=>{
  assert.deepEqual(customsEditor(''),{future:{keep:true}});
  assert.deepEqual(customsEditor('001234',false),{future:{keep:true}});
});
test('Customs Description can be saved, explicitly cleared or reset to inheritance',()=>{
  assert.deepEqual(customsEditor('Greeting card',true,'customs_description'),{customs_description:'Greeting card',future:{keep:true}});
  assert.deepEqual(customsEditor('',true,'customs_description'),{customs_description:'',future:{keep:true}});
  assert.deepEqual(customsEditor('',false,'customs_description'),{future:{keep:true}});
});
test('Markets changes persist additional IDs, with empty and absent both UK-only', () => {
  const ids = ['gid://shopify/Market/1', 'gid://shopify/Market/2'];
  const knownFields = new Set(['harmonized_system_code', 'shopify_market_ids']);
  assert.deepEqual(customsEditor('001234', true, 'harmonized_system_code', {
    isShared:true, knownFields, marketEditor:{}, marketValues:()=>ids
  }), {harmonized_system_code:'001234',future:{keep:true},shopify_market_ids:ids});
  assert.deepEqual(customsEditor('001234', true, 'harmonized_system_code', {
    isShared:true, knownFields, marketEditor:{}, marketValues:()=>[],
    authored:{harmonized_system_code:'001234',shopify_market_ids:ids},
    initialValues:{harmonized_system_code:'001234',shopify_market_ids:ids}
  }), {harmonized_system_code:'001234',shopify_market_ids:[]});
  assert.deepEqual(customsEditor('001234', true, 'harmonized_system_code', {
    isShared:true, knownFields, marketEditor:{}, marketValues:()=>[],
    authored:{harmonized_system_code:'001234'},
    initialValues:{harmonized_system_code:'001234',shopify_market_ids:[]}
  }), {harmonized_system_code:'001234'});
});
test('unchanged legacy live and explicit false survive guided roundtrip without authorisation', () => {
  const authored = {live: false, requires_shipping: false, future: {private: true}};
  const context=vm.createContext({structuredClone,authored,initialValues: {shopify_status:'DRAFT',requires_shipping:false},
    guidedValues:()=>({shopify_status:'DRAFT',requires_shipping:false})});
  vm.runInContext(guidedCode,context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(guidedDocument())',context)),authored);
});
test('explicit status edit keeps legacy intent consistent and reset restores inheritance', () => {
  const context=vm.createContext({structuredClone,authored:{live:true,shopify_status:'ACTIVE'},
    initialValues:{shopify_status:'ACTIVE'},guidedValues:()=>({shopify_status:'ARCHIVED'})});
  vm.runInContext(guidedCode,context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(guidedDocument())',context)),{live:false,shopify_status:'ARCHIVED'});
  context.guidedValues=()=>({});
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(guidedDocument())',context)),{});
});
test('channel intent preserves explicit false and removes only deliberately unmanaged handles', () => {
  const context=vm.createContext({structuredClone,channelDraft:{'online-store':false,unavailable:true},
    guided:{querySelectorAll:()=>[{dataset:{channelIntent:'online-store'},value:'false'}]}});
  const code=source.slice(source.indexOf('  function channelValues('),source.indexOf('  function renderChannels('));
  vm.runInContext(code,context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(channelValues())',context)),{'online-store':false,unavailable:true});
});
