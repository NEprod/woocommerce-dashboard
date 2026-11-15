import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {barcodeOption, cameraSession, initialize, normalizeCode} from '../../app/static/assets/js/inventory.mjs';
import {browserBarcodeFallback, decodedBarcodeValue} from '../../app/static/assets/js/inventory-barcode-fallback.mjs';

const retailFormats = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];
const allFormats = ['qr_code', 'code_128', ...retailFormats];
const cameraOptions = {audio:false, video:{facingMode:{ideal:'environment'}, width:{ideal:1280}, height:{ideal:720}}};

function mockCamera({native = true, supported = [...allFormats, 'data_matrix'], result = ['SH-A-01'], error,
  secure = true, decoder = async () => { throw new Error('no fallback fixture'); }} = {}) {
  const events = {state: [], status: [], code: [], permissions: [], formats: [], fallback: [], stopped: 0};
  const media = {getTracks: () => [{stop() { events.stopped++; }}]};
  const video = {readyState: 2, hidden: true, async play() {}};
  const timers = new Map();
  let timeId = 0;
  const host = {
    isSecureContext: secure,
    navigator: {mediaDevices: {async getUserMedia(options) {
      events.permissions.push(options);
      if (error) throw error;
      return media;
    }}},
    BarcodeDetector: native ? class {
      static async getSupportedFormats() { return supported; }
      constructor(options) { events.formats = options.formats; }
      async detect() { return result.map(rawValue => typeof rawValue === 'object' ? rawValue : ({rawValue, format:events.formats[0]})); }
    } : undefined,
    setTimeout(fn, ms) { timers.set(++timeId, {fn, ms}); return timeId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const session = cameraSession(host, video, (message) => events.status.push(message),
    (code) => events.code.push(code), (running) => events.state.push(running), async (env, missing) => {
      events.fallback.push(missing);
      return decoder(env, missing);
    });
  return {session, host, timers, events, media, video};
}

test('QR first, only supported formats requested and camera stops after decoding', async () => {
  const {session, events, video, timers} = mockCamera();
  await session.start();
  assert.deepEqual(events.formats, allFormats);
  assert.deepEqual(events.fallback, []);
  assert.deepEqual(events.permissions, [cameraOptions]);
  assert.deepEqual(events.code, ['SH-A-01']);
  assert.equal(events.stopped, 1);
  assert.equal(video.srcObject, null);
  assert.equal(video.hidden, true);
  assert.equal(timers.size, 0);
});

test('native Code 128 still works if the fallback for QR cannot load', async () => {
  const {session, events} = mockCamera({supported: ['code_128'], result: ['PART-01']});
  await session.start();
  assert.deepEqual(events.formats, ['code_128']);
  assert.deepEqual(events.fallback, [['qr_code', ...retailFormats]]);
  assert.deepEqual(events.code, ['PART-01']);
});

test('native QR support and local Code 128 support share one camera stream', async () => {
  const {session, events} = mockCamera({supported: ['qr_code'], result: [], decoder: async () => async () => 'PART-01'});
  await session.start();
  assert.deepEqual(events.formats, ['qr_code']);
  assert.deepEqual(events.fallback, [['code_128', ...retailFormats]]);
  assert.deepEqual(events.code, ['PART-01']);
  assert.equal(events.permissions.length, 1);
});

test('native retail detections must be strings with valid lengths and checksums', async () => {
  const results = [
    {format:'ean_13', rawValue:'6975337030233'}, {format:'ean_13', rawValue:'69753370302'},
    {format:'ean_8', rawValue:1234565}, {format:'upc_e', rawValue:'04252615'},
    {format:'upc_a', rawValue:'012345678905'},
  ];
  const {session, events} = mockCamera({result:results});
  await session.start();
  assert.deepEqual(events.code, ['012345678905']);
  assert.equal(events.stopped, 1);
  assert.equal(decodedBarcodeValue('0012345678905', 'upc_a'), '0012345678905');
  assert.equal(decodedBarcodeValue('012345678905', 'ean_13'), undefined);
  assert.equal(decodedBarcodeValue('04252614', 'upc_e'), '04252614');
});

test('HTTP, unavailable cameras and denied Safari permission give a manual path', async () => {
  for (const [options, message] of [
    [{secure: false}, /HTTPS.*Enter the barcode/],
    [{native: false, error: Object.assign(new Error(), {name:'NotAllowedError'})}, /denied.*enter the barcode/],
    [{native: false, error: Object.assign(new Error(), {name:'NotFoundError'})}, /Could not open the camera.*enter the barcode/],
  ]) {
    const {session, events, video, timers} = mockCamera(options);
    await session.start();
    assert.match(events.status.at(-1), message);
    assert.deepEqual(events.code, []);
    assert.deepEqual(events.fallback, []);
    assert.equal(video.hidden, true);
    assert.equal(timers.size, 0);
  }
  const {host, session, events} = mockCamera();
  delete host.navigator.mediaDevices;
  await session.start();
  assert.match(events.status.at(-1), /cannot access a camera.*Enter the barcode/);
});

test('a Safari fallback load error is actionable and stops the camera', async () => {
  const {session, events, video, timers} = mockCamera({native: false});
  await session.start();
  assert.match(events.status.at(-1), /barcode reader could not start.*enter the code/);
  assert.equal(events.stopped, 1);
  assert.equal(video.srcObject, null);
  assert.equal(timers.size, 0);
});

test('no readable label times out and decoder exceptions stop the stream', async () => {
  const {session, events, timers} = mockCamera({result: []});
  await session.start();
  [...timers.values()].find(timer => timer.ms === 30000).fn();
  assert.match(events.status.at(-1), /No readable barcode/);
  assert.equal(events.stopped, 1);
  assert.equal(timers.size, 0);

  const blocked = mockCamera();
  blocked.host.BarcodeDetector.prototype.detect = async () => { throw new Error('failed'); };
  await blocked.session.start();
  assert.match(blocked.events.status.at(-1), /could not be scanned/);
  assert.equal(blocked.events.stopped, 1);
});

test('stop closes a camera that returns after the operator cancels', async () => {
  const {session, host, events, media} = mockCamera();
  let allow;
  host.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { allow = resolve; });
  const pending = session.start();
  await Promise.resolve();
  session.stop();
  allow(media);
  await pending;
  assert.equal(events.stopped, 1);
  assert.deepEqual(events.code, []);
});

test('late fallback results are ignored after stopping and duplicates only dispatch once', async () => {
  let finish;
  let entered;
  const began = new Promise(resolve => { entered = resolve; });
  const stopped = mockCamera({native: false, decoder: async () => () => new Promise(resolve => {
    finish = resolve;
    entered();
  })});
  const pending = stopped.session.start();
  await began;
  stopped.session.stop();
  finish('SH-A-01');
  await pending;
  assert.deepEqual(stopped.events.code, []);
  assert.equal(stopped.events.stopped, 1);

  const duplicates = mockCamera({result: ['SH-A-01', 'SH-A-01']});
  await duplicates.session.start();
  assert.deepEqual(duplicates.events.code, ['SH-A-01']);
  assert.equal(duplicates.events.stopped, 1);
  assert.equal(duplicates.timers.size, 0);
});

test('page navigation and backgrounding release the current stream', async () => {
  for (const reason of ['pagehide', 'visibilitychange']) {
    const {host, events, video, timers} = mockCamera({result: []});
    const controls = {};
    const hooks = {};
    const form = {querySelector: () => ({})};
    const block = {
      addEventListener() {}, closest: () => form, hasAttribute: () => true,
      querySelector: (selector) => selector === '[data-scan-video]' ? video :
        controls[selector] ??= {addEventListener: (name, handler) => { hooks[selector] = handler; }},
    };
    const doc = {
      hidden: false, querySelector: () => null,
      querySelectorAll: (selector) => selector === '[data-workshop-scan]' ? [block] : [],
      addEventListener: (name, handler) => { hooks[name] = handler; },
    };
    host.addEventListener = doc.addEventListener;
    initialize(doc, host);
    hooks['[data-scan-open]']();
    await new Promise(setImmediate);
    assert.equal(video.hidden, false);
    doc.hidden = true;
    hooks[reason]();
    assert.equal(video.hidden, true);
    assert.equal(events.stopped, 1);
    assert.equal(timers.size, 0);
  }
});

// Independently encoded SH-A-01 using the dashboard's qrcode and python-barcode
// generators. The quiet zones and RGBA frame below simulate camera pixels.
const qr = `111111100100101111111
100000101100001000001
101110100111101011101
101110100101101011101
101110101000101011101
100000100110101000001
111111101010101111111
000000000001100000000
101010100001000010010
000100010000001000100
010111100000100010010
010011001010001000001
011100111100101011010
000000001111010100110
111111100011011101010
100000100001110110101
101110101011011100101
101110100100001100110
101110101010100011101
100000100010001000011
111111101110101010101`.split('\n');
const bars = '1101001000011011101000110001010001001101110010100011000100110111001001110110010011100110100001001101100011101011';
// GS1 modules generated independently using python-barcode. The two sample
// GTIN-13s are also checksum-valid even when printed in a Code 128 symbol.
const ean0232 = '10100010110010001011100101000010111101011101101010111001010000101110010110110010000101101100101';
const ean0270 = '10100010110010001011100101000010111101011101101010111001010000101110010110110010001001110010101';
const ean509888 = '10100010110100001001000100110110110111011000101010100111011100101110100100100010010001001000101';
const code1280270 = '110100111001011001000011000010010101000110001011000010011011011000111011001001011110111010011101100101011110001100011101011';
const badEAN = '10100010110010001011100101000010111101011101101010111001010000101110010110110010000101000010101';
const ean8 = '1010001101001100100100110111101010101011100100111010100001001110101'; // 01234565
const upcA = '10100011010011001001001101111010100011011000101010101000010001001001000111010011100101001110101'; // 012345678905
const upcE = '101001110100100110111001001101101011110011001010101'; // 04252614, expanded 042100005264

function frame(rows, quiet, scale) {
  const width = (rows[0].length + 2 * quiet) * scale;
  const height = (rows.length + 2 * quiet) * scale;
  const data = new Uint8ClampedArray(width * height * 4).fill(255);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (rows[Math.floor(y / scale) - quiet]?.[Math.floor(x / scale) - quiet] !== '1') continue;
      data.fill(0, (y * width + x) * 4, (y * width + x) * 4 + 3);
    }
  }
  return {width, height, data};
}

// Draw a small label inside a full camera-sized image. The subpixel coverage
// approximates antialiasing at varying frame scales rather than stretching a
// tight crop to fill the decoder's input.
function linearCameraFrame(pattern, width, height, pitch, rotated = false) {
  const data = new Uint8ClampedArray(width * height * 4).fill(255);
  const across = rotated ? height : width;
  const along = rotated ? width : height;
  const left = (across - pattern.length * pitch) / 2 + .25;
  for (let x = Math.ceil(left); x < left + pattern.length * pitch; x++) {
    let dark = 0;
    for (let sample = 0; sample < 8; sample++) dark += pattern[Math.floor((x + (sample + .5) / 8 - left) / pitch)] === '1';
    if (!dark) continue;
    for (let y = Math.floor(along / 2 - 24); y < along / 2 + 24; y++) {
      const i = 4 * (rotated ? x * width + width - 1 - y : y * width + x);
      data.fill(Math.round(255 * (1 - dark / 8)), i, i + 3);
    }
  }
  return {width, height, data};
}

test('real self-hosted WASM decodes TLC and retail fixtures without uploading frames', async (t) => {
  const asset = new URL('../../app/static/assets/js/vendor/zxing-wasm-3.1.6/dist/reader/zxing_reader.wasm', import.meta.url);
  const binary = await readFile(asset);
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(String(url), asset.href);
    assert.deepEqual(options, {credentials:'same-origin'});
    return new Response(binary, {headers: {'content-type':'application/wasm'}});
  });

  for (const pixels of [frame(qr, 4, 6), frame(Array(32).fill(bars), 14, 4)]) {
    const {session, host, video, events} = mockCamera({native: false, decoder: browserBarcodeFallback});
    // Exercise the downscaling path; the Canvas mock returns the resized fixture.
    video.videoWidth = 2560;
    video.videoHeight = 1440;
    const context = {
      drawImage: (...args) => assert.deepEqual(args, [video, 0, 0, 1280, 720]),
      getImageData: () => pixels,
    };
    host.document = {createElement: (tag) => { assert.equal(tag, 'canvas'); return {getContext: () => context}; }};
    await session.start();
    assert.deepEqual(events.code, ['SH-A-01']);
    assert.deepEqual(events.fallback, [allFormats]);
    assert.deepEqual(events.permissions, [cameraOptions]);
    assert.equal(events.stopped, 1);
  }

  await t.test('EAN-13, EAN-8, UPC-A and original eight-digit UPC-E, including leading zeros', async () => {
    async function decode(pattern, formats = allFormats) {
      const pixels = linearCameraFrame(pattern, 480, 270, 3);
      const context = {drawImage() {}, getImageData:() => pixels};
      const host = {document:{createElement:() => ({getContext:() => context})}};
      const read = await browserBarcodeFallback(host, formats);
      return read({videoWidth:480, videoHeight:270});
    }
    assert.equal(await decode(ean8), '01234565');
    // The same bars can be reported as EAN-13 or UPC-A. ZXing's output in
    // both modes preserves the full representation; the adapter removes no zero.
    assert.equal(await decode(upcA), '0012345678905');
    assert.equal(await decode(upcA, ['upc_a']), '0012345678905');
    assert.equal(await decode(upcE, ['upc_e']), '04252614');
    assert.equal(await decode(ean509888), '6937285509888');
    assert.equal(await decode(ean509888, ['qr_code', 'code_128']), undefined);
    assert.equal(await decode(code1280270, ['qr_code', 'code_128']), '6975337030270');
    assert.equal(await decode(badEAN), undefined);
    assert.equal(await decode(ean0232.slice(0, 80)), undefined);
  });

  await t.test('measured small EAN-13 labels at 1280 × 720 and 90° portrait, compared to the old cap', async () => {
    async function decode(pixels) {
      const context = {drawImage() {}, getImageData:() => pixels};
      const read = await browserBarcodeFallback({document:{createElement:() => ({getContext:() => context})}}, allFormats);
      return read({videoWidth:pixels.width, videoHeight:pixels.height});
    }
    for (const [pattern, expected] of [[ean0232,'6975337030232'],[ean0270,'6975337030270']]) {
      assert.equal(decodedBarcodeValue(expected, 'ean_13'), expected);
      assert.equal(await decode(linearCameraFrame(pattern, 960, 540, 1.7 * .75)), undefined);
      for (const pitch of [1.7, 2, 3]) {
        assert.equal(await decode(linearCameraFrame(pattern, 1280, 720, pitch)), expected);
        assert.equal(await decode(linearCameraFrame(pattern, 720, 1280, pitch, true)), expected);
      }
    }
  });

  await t.test('slight deskew remains bounded to two attempts in eight without touching the video', async () => {
    const angles = [];
    const blank = {width:320, height:180, data:new Uint8ClampedArray(320 * 180 * 4).fill(255)};
    const video = {videoWidth:320, videoHeight:180};
    const context = {save() {}, fillRect() {}, translate() {}, restore() {}, drawImage(frame) { assert.equal(frame, video); },
      rotate(radians) { angles.push(Math.round(radians * 180 / Math.PI)); }, getImageData:() => blank};
    const read = await browserBarcodeFallback({document:{createElement:() => ({getContext:() => context})}}, allFormats);
    for (let i = 0; i < 8; i++) assert.equal(await read(video), undefined);
    assert.deepEqual(angles, [2, -2]);
  });
  assert.equal(fetch.mock.callCount(), 1);
});

test('manual, camera and keyboard wedge use the same known code resolver', () => {
  const options = [{value: '5', dataset: {code: 'SH-A-01'}}, {value: '12', dataset: {code: 'RAW-PLA'}}];
  assert.equal(normalizeCode(' raw-pla\n'), 'RAW-PLA');
  assert.equal(barcodeOption(options,'sh-a-01').value,'5');
  assert.equal(barcodeOption(options,'UNKNOWN'),undefined);
  assert.equal(normalizeCode('stock/12'),null);
  assert.equal(normalizeCode('ß-12'),null);
  const retail = [{value:'first',dataset:{code:'012345678905'}},{value:'second',dataset:{code:'0012345678905'}}];
  assert.equal(barcodeOption(retail, '012345678905').value, 'first');
  assert.equal(barcodeOption(retail, '0012345678905').value, 'second');
  assert.equal(barcodeOption(retail, '6937285509888'), undefined);
});

test('a decoded unregistered manufacturer barcode is unknown to the form, not a camera error', async () => {
  const {host, events, video} = mockCamera({result:[{format:'ean_13', rawValue:'6937285509888'}]});
  const hooks = {};
  const controls = {
    '[data-scan-video]':video,
    '[data-scan-manual]':{value:'', addEventListener() {}},
  };
  const form = {requestSubmit() { throw new Error('Lookup must not submit the stock form'); }};
  const target = {value:'5', options:[{value:'5', dataset:{code:'SH-A-01'}}]};
  const block = {
    dataset:{workshopScan:'stock-item'}, closest:() => form, hasAttribute:() => false, addEventListener() {},
    querySelector: selector => controls[selector] ??= {addEventListener: (name, handler) => { hooks[selector] = handler; }},
  };
  host.addEventListener = () => {};
  initialize({getElementById:() => target, querySelector:() => null, addEventListener() {},
    querySelectorAll: selector => selector === '[data-workshop-scan]' ? [block] : []}, host);
  hooks['[data-scan-open]']();
  await new Promise(setImmediate);
  assert.equal(controls['[data-scan-manual]'].value, '6937285509888');
  assert.match(controls['[data-scan-status]'].textContent, /6937285509888 is not available for this field/);
  assert.equal(target.value, '5');
  assert.equal(events.stopped, 1);
});

function editor({raw = '000420', value = '', readOnly = false, respond} = {}) {
  const {host, events, timers, video} = mockCamera({result: raw ? [raw] : []});
  function control(initial = {}) {
    const handlers = {};
    return Object.assign({addEventListener: (type, callback) => { handlers[type] = callback; },
      fire: (type, event) => handlers[type]?.(event)}, initial);
  }
  const field = control({value, readOnly});
  const unsaved = {name: 'Edited filament', description: 'Unsaved notes'};
  const requests = [];
  let saves = 0;
  const form = {requestSubmit: () => { saves++; }, submit: () => { saves++; }};
  const start = control({hidden: true});
  const stop = control({hidden: true});
  const use = control({hidden: true});
  const status = {textContent: ''};
  const nodes = {'[data-scan-open]':start, '[data-scan-stop]':stop, '[data-scan-video]':video,
    '[data-scan-status]':status, '[data-scan-use]':use};
  const block = control({closest: () => form, hasAttribute: () => false, querySelector: selector => nodes[selector],
    dataset: {workshopScan:'inventory-item-barcode', editorScan:'/production/item/barcode-check'}});
  const doc = control({querySelector: () => null, getElementById: id => {
    assert.equal(id, 'inventory-item-barcode');
    return field;
  }, querySelectorAll: selector => selector === '[data-workshop-scan]' ? [block] : []});
  host.addEventListener = () => {};
  host.location = {href:'https://tlc.example.test/production/item/new/material'};
  host.fetch = async (url, options) => {
    requests.push({url, options});
    return respond ? respond(url) : {ok:true, status:200, json:async () => ({available:true})};
  };
  initialize(doc, host);
  return {field, start, stop, use, status, events, timers, video, requests, unsaved, saves: () => saves};
}

async function scanInEditor(view) {
  assert.equal(view.start.hidden, false);
  view.start.fire('click');
  await new Promise(setImmediate);
}

test('editor scan fills a text field with leading zeros, without submitting or touching other form data', async () => {
  const view = editor();
  await scanInEditor(view);
  assert.equal(view.field.value, '000420');
  assert.match(view.status.textContent, /use Save/);
  assert.deepEqual(view.unsaved, {name:'Edited filament', description:'Unsaved notes'});
  assert.equal(view.saves(), 0);
  assert.equal(view.events.stopped, 1);
  assert.equal(view.video.srcObject, null);
  assert.equal(view.timers.size, 0);
  assert.equal(view.requests.length, 1);
  assert.equal(view.requests[0].url.href, 'https://tlc.example.test/production/item/barcode-check?code=000420');
  assert.deepEqual(view.requests[0].options, {credentials:'same-origin', cache:'no-store'});
});

test('an entered code is not replaced without explicit approval and a keyboard scanner cannot submit', async () => {
  const view = editor({value:'TLC-001', raw:'Box-0006'});
  await scanInEditor(view);
  assert.equal(view.field.value, 'TLC-001');
  assert.equal(view.use.hidden, false);
  view.use.fire('click');
  assert.equal(view.field.value, 'Box-0006');
  assert.equal(view.use.hidden, true);
  view.field.value = '000017';
  view.field.fire('input');
  let prevented = false;
  view.field.fire('keydown', {key:'Enter', preventDefault: () => { prevented = true; }});
  assert.equal(view.field.value, '000017');
  assert.equal(prevented, true);
  assert.equal(view.saves(), 0);
});

test('editing checks the saved barcode but never offers replacement', async () => {
  const matching = editor({value:'000420', readOnly:true});
  await scanInEditor(matching);
  assert.match(matching.status.textContent, /matches the saved barcode/);
  assert.equal(matching.requests.length, 0);
  const different = editor({value:'TLC-001', readOnly:true});
  await scanInEditor(different);
  assert.match(different.status.textContent, /permanent barcode is unchanged/);
  assert.equal(different.field.value, 'TLC-001');
  assert.equal(different.use.hidden, true);
  assert.equal(different.saves(), 0);
});

test('conflicting or cancelled scans leave the old field value unchanged', async () => {
  const duplicate = editor({value:'TLC-001', respond: async () => ({status:409, json:async () => ({error:
    'Barcode 000420 is already assigned to location “Shelf A”.'})})});
  await scanInEditor(duplicate);
  assert.match(duplicate.status.textContent, /already assigned.*Shelf A/);
  assert.equal(duplicate.field.value, 'TLC-001');
  assert.equal(duplicate.use.hidden, true);
  assert.equal(duplicate.saves(), 0);
  const cancelled = editor({value:'TLC-001', raw:null});
  await scanInEditor(cancelled);
  assert.equal(cancelled.video.hidden, false);
  cancelled.stop.fire('click');
  assert.equal(cancelled.field.value, 'TLC-001');
  assert.equal(cancelled.video.hidden, true);
  assert.equal(cancelled.events.stopped, 1);
  assert.equal(cancelled.requests.length, 0);
  assert.equal(cancelled.saves(), 0);
});

test('a late availability result cannot overwrite new manual input', async () => {
  let reply;
  const view = editor({respond: () => new Promise(resolve => { reply = resolve; })});
  await scanInEditor(view);
  view.field.value = '000999';
  view.field.fire('input');
  reply({ok:true, status:200, json:async () => ({available:true})});
  await new Promise(setImmediate);
  assert.equal(view.field.value, '000999');
  assert.equal(view.use.hidden, true);
  assert.equal(view.saves(), 0);
});
