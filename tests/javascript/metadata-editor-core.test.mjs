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
test('EU identifier checkbox distinguishes explicit false from unspecified and inheritance', () => {
  const field = 'no_eu_standardized_identifier';
  const control = {dataset:{field}, checked:false, indeterminate:true};
  const setup = {authored:{}, initialValues:{}, knownFields:new Set([field]),
    guided:{querySelectorAll:selector=>selector==='[data-field]'?[control]:[]}};
  const values = () => customsEditor('', true, field, setup);
  assert.deepEqual(values(), {});
  control.indeterminate = false;
  control.checked = true;
  assert.deepEqual(values(), {[field]:true});
  control.checked = false;
  assert.deepEqual(values(), {[field]:false});
  setup.authored = setup.initialValues = {[field]:true};
  control.indeterminate = true;
  assert.deepEqual(values(), {});
  control.indeterminate = false;
  control.checked = true;
  assert.deepEqual(customsEditor('', false, field, setup), {});
});
test('Markets changes persist additional IDs, with empty and absent both UK-only', () => {
  const ids = ['gid://shopify/Market/1', 'gid://shopify/Market/2'];
  const knownFields = new Set(['harmonized_system_code', 'shopify_market_ids']);
  assert.deepEqual(customsEditor('001234', true, 'harmonized_system_code', {
    isShared:true, knownFields, marketEditor:{}, marketsEdited:true, marketValues:()=>ids
  }), {harmonized_system_code:'001234',future:{keep:true},shopify_market_ids:ids});
  assert.deepEqual(customsEditor('001234', true, 'harmonized_system_code', {
    isShared:true, knownFields, marketEditor:{}, marketsEdited:true, marketValues:()=>[],
    authored:{harmonized_system_code:'001234',shopify_market_ids:ids},
    initialValues:{harmonized_system_code:'001234',shopify_market_ids:ids}
  }), {harmonized_system_code:'001234',shopify_market_ids:[]});
  assert.deepEqual(customsEditor('001234', true, 'harmonized_system_code', {
    isShared:true, knownFields, marketEditor:{}, marketsEdited:true, marketValues:()=>[],
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

const destinationsCode = source.slice(source.indexOf('  function channelValues('), source.indexOf('  function registryChoices('));
const savedChannels = {online_store:true, 'shop-72':true, pos:false, 'click-drop-app-1':true};
const market = id => `gid://shopify/Market/${id}`;

function destinationEditor({shared=false, local={}, inherited=savedChannels}={}) {
  const element = () => ({dataset:{}, children:[], textContent:'',
    replaceChildren(){this.children=[]; this.textContent='';},
    get childNodes(){return this.children;},
    append(...children){this.children.push(...children);},
    appendChild(child){this.append(child);}, add(){},
    addEventListener(event, callback){this[event]=callback;},
  });
  const channelRows=element(), marketRows=element(), feedback=element();
  const override={checked:Object.hasOwn(local,'shopify_channels')};
  const field={dataset:{field:'description'}, value:'original'};
  const selected = rows => rows.children.flatMap(n=>n.children).filter(n=>n.dataset);
  const marketEditor={querySelector:key=>key==='[data-market-choices]'?marketRows:feedback,
    querySelectorAll:()=>selected(marketRows).filter(n=>n.checked && Object.hasOwn(n.dataset,'marketId'))};
  const context=vm.createContext({structuredClone,window:{},isShared:shared,authored:structuredClone(local),
    boot:{shared_reference:'Cards/product_info.json',authored_reference:shared?'Cards/product_info.json':'Cards/Item/product_info.json'},
    initialValues:{}, knownFields:new Set(['shopify_channels','shopify_market_ids','description']),
    channelDraft:{},channels:[{key:'online_store',name:'Online Store'}],channelsChecked:false,
    marketEditor,markets:[],marketsLoaded:false,marketsEdited:false,marketsLoading:false,
    customisationDesigner:null,packagingDesigner:null,finishedDesigner:null,explicitVariation:false,
    guided:{querySelector:key=>key==='[data-channel-choices]'?channelRows:override,
      querySelectorAll:key=>key==='[data-field]'?[field]:key==='[data-channel-intent]'
        ?selected(channelRows).filter(n=>Object.hasOwn(n.dataset,'channelIntent')):[]},
    document:{querySelector:()=>({content:'csrf'}),createElement:element,createTextNode:text=>({textContent:text})},
    Option:function(){}, setControlState(){}, setDirty(){}, showFeedback(){},
    overrideEnabled:key=>key==='shopify_channels'?shared||override.checked:key==='description',
    listValues:()=>[], attributeValues:()=>({}), modifierValues:()=>({}),
    startingChannels:local.shopify_channels||inherited, startingMarkets:local.shopify_market_ids||[market(1),market(3)],
  });
  vm.runInContext(destinationsCode+valuesCode+guidedCode+patchCode+`
    renderChannels(startingChannels); renderMarkets(startingMarkets); initialValues=guidedValues();`,context);
  const result = expression => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`,context));
  const text = node => node.textContent+node.children?.map(text).join('');
  const read = (target, payload, ok=true) => {
    context.fetch=async()=>({ok,json:async()=>payload});
    return vm.runInContext(`discover${target}()`,context);
  };
  return {context,field,read,result,channelRows,marketRows,selected,text};
}

test('discovery checks every effective inherited selection without dropping intent, including after failure',async()=>{
  const e=destinationEditor();
  const {read,result,text,channelRows,marketRows}=e;
  assert.match(text(channelRows),/Online Store .*Not verified .*Inherited from Cards\/product_info\.json/);
  assert.match(text(channelRows),/Click & Drop .*Not verified/);
  assert.match(text(marketRows),/Market\/3.*Not verified.*Inherited from Cards\/product_info\.json/);
  await read('Channels',{channels:[{key:'online_store',name:'Online Store'},{key:'shop-72',name:'Shop'},{key:'pos',name:'POS'}]});
  await read('Markets',{markets:[{id:market(1),name:'France',active:true}]});
  assert.match(text(channelRows),/Online Store .* · Available · Inherited from Cards\/product_info\.json/);
  assert.match(text(channelRows),/Click & Drop .* · Unavailable .*Saved intent: publish.*Inherited from Cards\/product_info\.json/);
  assert.match(text(marketRows),/France.* · Available · Inherited from Cards\/product_info\.json/);
  assert.match(text(marketRows),/Market\/3.* · Unavailable .*Inherited from Cards\/product_info\.json/);
  assert.ok(e.selected(marketRows).every(input=>input.disabled));
  await read('Channels',{error:'Incomplete channel discovery'},false);
  await read('Markets',{error:'Missing Markets access'},false);
  assert.doesNotMatch(text(channelRows)+text(marketRows),/ · (Available|Unavailable) /);
  assert.deepEqual(result('channelValues()'),savedChannels);
  assert.deepEqual(result('marketValues()'),[market(1),market(3)]);
  e.field.value='new description';
  assert.deepEqual(result('guidedDocument()'),{description:'new description'});
});

test('only an explicit action removes inherited channel intent or a shared Market',async()=>{
  const e=destinationEditor();
  await e.read('Channels',{channels:[{key:'online_store',name:'Online Store'},{key:'shop-72',name:'Shop'},{key:'pos',name:'POS'}]});
  e.selected(e.channelRows).find(n=>n.dataset.removeUnavailableChannel==='click-drop-app-1').click();
  assert.deepEqual(e.result('guidedDocument()'),{shopify_channels:{online_store:true,'shop-72':true,pos:false}});
  assert.match(e.text(e.channelRows),/POS.*Saved in Cards\/Item\/product_info\.json/);
  assert.equal(savedChannels['click-drop-app-1'],true);

  const local={shopify_channels:savedChannels,shopify_market_ids:[market(1),market(3)], customs_description:'Keep this'};
  const shared=destinationEditor({shared:true,local});
  await shared.read('Markets',{markets:[{id:market(1),name:'France',active:true}]});
  shared.field.value='new description';
  assert.deepEqual(shared.result('documentPatch(authored,guidedDocument())'),{data:{description:'new description'},remove_paths:[]});
  shared.selected(shared.marketRows).find(n=>n.value===market(3)).checked=false;
  shared.context.marketsEdited=true;
  assert.deepEqual(shared.result('documentPatch(authored,guidedDocument())'),{
    data:{description:'new description',shopify_market_ids:[market(1)]},remove_paths:[]});

  const override=destinationEditor({local:{shopify_channels:{'click-drop-app-1':false}}});
  assert.match(override.text(override.channelRows),/Saved intent: unpublish.*Saved in Cards\/Item\/product_info\.json/);
  override.selected(override.channelRows).find(n=>n.dataset.removeUnavailableChannel==='click-drop-app-1').click();
  assert.deepEqual(override.result('guidedDocument()'),{shopify_channels:{}});
});
