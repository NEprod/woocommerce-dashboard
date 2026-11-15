import assert from 'node:assert/strict';
import {test} from 'node:test';
import {setupStockDestination} from '../../app/static/assets/js/inventory-receiving.mjs';

function control(fields = {}) {
  const handlers = {};
  return {...fields, addEventListener: (event, callback) => { handlers[event] = callback; },
    fire: (event) => handlers[event]?.()};
}

function workshop(kind = 'receive', reply) {
  const receive = kind === 'receive';
  const cards = ['Shelf A', 'Shelf B'].map((label, i) => control({value:String(i + 10), dataset:{locationLabel:label}}));
  const ordinary = control({value:'10'});
  const video = control();
  const nodes = {
    '[name=item_id]':control({value:'42'}), '[name=qty]':control({value:'200'}),
    '[name=request_id]':control({value:'request-1'}), '[name=csrf_token]':control({value:'csrf-1'}),
    '[name=entry_unit]':receive ? control({value:'units'}) : null,
    '[name=source]':receive ? null : control({value:'10'}),
    '[data-keep-unassigned]':receive ? control({checked:false}) : null,
    '[data-unassigned-note]':receive ? control() : null,
    '[data-normal-move-destination]':receive ? null : control({querySelector: () => ordinary}),
    '[data-physical-destination]':control({querySelector: () => video, querySelectorAll: () => receive ? cards : []}),
    ...Object.fromEntries(['scanned-destination','location-proof','review-verified-stock','scan-location','cancel-location',
      'use-location','destination-status','scan-status'].map(key => [`[data-${key}]`, control({value:''})])),
  };
  const requests = [];
  const responses = {'A':{destination:'10', label:'Shelf A', proof:'scan-a'}, 'B':{destination:'11', label:'Shelf B', proof:'scan-b'},
    'C':{destination:'12', label:'Shelf C', proof:'scan-c'}, 'ITEM':{error:'This is an item barcode.'}};
  const host = control({document:control(), fetch: async (path, request) => {
    const code = request.body.get('code');
    requests.push([path, request]);
    if (reply) return reply(code);
    return {ok:!!responses[code]?.proof, json:async () => responses[code] ?? {error:'Unknown location barcode.'}};
  }});
  let lastScan, cameraStops = 0;
  const form = {querySelector: selector => nodes[selector], dataset:{verifiedStock:kind, stockUnit:'g', locationCheck:`/production/stock/${kind}/scan-location`}};
  setupStockDestination(form, host, (env, preview, status, result, running) => {
    assert.equal(preview, video);
    lastScan = result;
    return {start: () => { running(true); }, stop: () => { cameraStops++; running(false); }};
  });
  const n = key => nodes[`[data-${key}]`];
  async function scan(code) {
    n('scan-location').fire('click');
    lastScan(code);
    await new Promise(setImmediate);
  }
  return {nodes, n, cards, ordinary, requests, host, scan, cameraStops:() => cameraStops};
}

test('a suggestion requires a scan; mismatches need an explicit switch or matching rescan', async () => {
  const {cards, n, nodes, scan, requests} = workshop();
  assert.equal(n('keep-unassigned').checked, false);
  cards[0].fire('change');
  assert.equal(n('review-verified-stock').disabled, true);
  assert.equal(n('scanned-destination').value, '10');
  await scan('B');
  assert.match(n('destination-status').textContent, /Selected Shelf A, but scanned Shelf B/);
  assert.equal(n('location-proof').value, '');
  assert.equal(n('review-verified-stock').disabled, true);
  n('use-location').fire('click');
  assert.equal(n('scanned-destination').value, '11');
  assert.equal(n('location-proof').value, 'scan-b');
  assert.equal(n('review-verified-stock').disabled, false);
  cards[0].fire('change');
  assert.equal(n('location-proof').value, '');
  await scan('A');
  assert.equal(n('review-verified-stock').disabled, false);
  assert.equal(nodes['[name=qty]'].value, '200');
  assert.deepEqual(Object.fromEntries(requests.at(-1)[1].body), {code:'A', item_id:'42', request_id:'request-1', csrf_token:'csrf-1'});
});

test('an unknown or item label is not a destination; scan an unsuggested location directly', async () => {
  const {n, scan} = workshop();
  await scan('UNKNOWN');
  assert.match(n('scan-status').textContent, /Unknown/);
  await scan('ITEM');
  assert.match(n('scan-status').textContent, /item barcode/);
  assert.equal(n('review-verified-stock').disabled, true);
  await scan('C');
  assert.match(n('destination-status').textContent, /Verified: Shelf C/);
  assert.equal(n('scanned-destination').value, '12');
  assert.equal(n('review-verified-stock').disabled, false);
});

test('switching modes, changing the item and cancelling reset verification', async () => {
  const {n, nodes, scan, cameraStops} = workshop();
  await scan('A');
  n('keep-unassigned').checked = true;
  n('keep-unassigned').fire('change');
  assert.equal(n('physical-destination').hidden, true);
  assert.equal(n('scanned-destination').disabled, true);
  assert.equal(n('location-proof').value, '');
  assert.equal(n('review-verified-stock').disabled, false);
  nodes['[name=qty]'].value = '0';
  nodes['[name=qty]'].fire('input');
  assert.equal(n('review-verified-stock').disabled, true);
  nodes['[name=qty]'].value = '20';
  n('keep-unassigned').checked = false;
  n('keep-unassigned').fire('change');
  assert.equal(n('review-verified-stock').disabled, true);
  await scan('B');
  nodes['[name=item_id]'].value = '43';
  nodes['[name=item_id]'].fire('change');
  assert.equal(n('location-proof').value, '');
  assert.equal(n('scanned-destination').value, '');
  await scan('A');
  n('scan-location').fire('click');
  n('cancel-location').fire('click');
  assert.equal(n('review-verified-stock').disabled, true);
  assert.equal(n('location-proof').value, '');
  assert.match(n('scan-status').textContent, /cancelled/);
  assert.ok(cameraStops() >= 4);
});

test('Move Stock requires a scanned destination only when the source is built-in Unassigned', async () => {
  const {n, nodes, scan, ordinary, host} = workshop('move');
  const source = nodes['[name=source]'];
  assert.equal(ordinary.disabled, false);
  assert.equal(n('review-verified-stock').disabled, false);
  source.value = 'unassigned';
  source.fire('change');
  assert.equal(ordinary.disabled, true);
  assert.equal(n('review-verified-stock').disabled, true);
  await scan('C');
  assert.equal(n('scanned-destination').value, '12');
  assert.equal(n('review-verified-stock').disabled, false);
  host.fire('pagehide');
  assert.equal(n('review-verified-stock').disabled, true);
  source.value = '10';
  source.fire('change');
  assert.equal(ordinary.disabled, false);
  assert.equal(n('location-proof').value, '');
});

test('a scan response arriving after mode switching is discarded', async () => {
  let resolve;
  const {n, scan} = workshop('receive', () => new Promise(callback => { resolve = callback; }));
  await scan('A');
  n('keep-unassigned').checked = true;
  n('keep-unassigned').fire('change');
  resolve({ok:true, json:async () => ({destination:'10', label:'Shelf A', proof:'scan-a'})});
  await new Promise(setImmediate);
  n('keep-unassigned').checked = false;
  n('keep-unassigned').fire('change');
  assert.equal(n('scanned-destination').value, '');
  assert.equal(n('review-verified-stock').disabled, true);
});
