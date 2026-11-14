import assert from 'node:assert/strict';
import {test} from 'node:test';
import {barcodeOption, cameraSession, normalizeCode} from '../../app/static/assets/js/inventory.mjs';

function mockCamera({supported = ['qr_code', 'code_128', 'ean_13'], result = ['SH-A-01'], error, secure = true} = {}) {
  const events = {state: [], status: [], code: [], permissions: [], formats: [], stopped: 0};
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
    BarcodeDetector: class {
      static async getSupportedFormats() { return supported; }
      constructor(options) { events.formats = options.formats; }
      async detect() { return result.map(rawValue => ({rawValue})); }
    },
    setTimeout(fn, ms) { timers.set(++timeId, {fn, ms}); return timeId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const session = cameraSession(host, video, (message) => events.status.push(message),
    (code) => events.code.push(code), (running) => events.state.push(running));
  return {session, host, timers, events, media, video};
}

test('QR first, only supported formats requested and camera stops after decoding', async () => {
  const {session, events, video, timers} = mockCamera();
  await session.start();
  assert.deepEqual(events.formats, ['qr_code', 'code_128']);
  assert.deepEqual(events.permissions, [{audio: false, video: {facingMode: {ideal: 'environment'}}}]);
  assert.deepEqual(events.code, ['SH-A-01']);
  assert.equal(events.stopped, 1);
  assert.equal(video.srcObject, null);
  assert.equal(video.hidden, true);
  assert.equal(timers.size, 0);
});

test('Code 128 works when QR is not provided by the browser decoder', async () => {
  const {session, events} = mockCamera({supported: ['code_128'], result: ['PART-01']});
  await session.start();
  assert.deepEqual(events.formats, ['code_128']);
  assert.deepEqual(events.code, ['PART-01']);
});

test('HTTP, unsupported and denied cameras give a manual path and no result', async () => {
  for (const [options, message] of [
    [{secure: false}, /HTTPS.*Enter the barcode/],
    [{supported: ['ean_13']}, /cannot scan.*Enter the barcode/],
    [{error: Object.assign(new Error(), {name:'NotAllowedError'})}, /denied.*enter the barcode/],
  ]) {
    const {session, events, video, timers} = mockCamera(options);
    await session.start();
    assert.match(events.status.at(-1), message);
    assert.deepEqual(events.code, []);
    assert.equal(video.hidden, true);
    assert.equal(timers.size, 0);
  }
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

test('manual, camera and keyboard wedge use the same known code resolver', () => {
  const options = [{value: '5', dataset: {code: 'SH-A-01'}}, {value: '12', dataset: {code: 'RAW-PLA'}}];
  assert.equal(normalizeCode(' raw-pla\n'), 'RAW-PLA');
  assert.equal(barcodeOption(options,'sh-a-01').value,'5');
  assert.equal(barcodeOption(options,'UNKNOWN'),undefined);
  assert.equal(normalizeCode('stock/12'),null);
  assert.equal(normalizeCode('ß-12'),null);
});
