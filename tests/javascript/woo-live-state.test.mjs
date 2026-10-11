import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {actionLabel, drain, requestState, terminal, failureView} = createRequire(import.meta.url)('../../app/static/assets/js/woo-live-state.js');

test('operational actions hand off to review, never directly publish', () => {
  assert.equal(actionLabel('local_only'), 'Review Create');
  assert.equal(actionLabel('remote_drift'), 'Review Update');
  assert.equal(actionLabel('link_candidate'), 'Review & Link');
  assert.equal(actionLabel('no_change'), 'Review Preview');
});
test('incremental refresh is sequential, deduplicated and finite', async () => {
  const events = [];
  await drain([1, 1, 2], async (id, force) => { events.push(['read', id, force]); return {state: 'no_change'}; },
    (id, state) => events.push(['render', id, state.state]), true);
  assert.deepEqual(events, [['read',1,true],['render',1,'no_change'],['read',2,true],['render',2,'no_change']]);
});
test('failed row preserves last known UI and does not retry forever', async () => {
  const rendered = [];
  await drain([1, 2], async id => { if (id === 1) throw Error('offline'); return {state:'local_only'}; },
    (id, state) => rendered.push([id, state]));
  assert.match(rendered[0][1].error, /last known/);
  assert.equal(rendered[1][1].state, 'local_only');
});

test('only the current request is checking and every completed queue returns to zero', async () => {
  const active = new Set(), sizes = [], rendered = [];
  await drain([1, 2, 2], async () => ({state:'no_change'}), (id,s) => {
    assert(active.has(id)); rendered.push(s.state);
  }, false, {activity(id, checking) { checking ? active.add(id) : active.delete(id); sizes.push(active.size); }});
  assert.deepEqual(sizes, [1,0,1,0]);
  assert.deepEqual(rendered, ['no_change','no_change']);
  assert.equal(active.size, 0); // Unprocessed/off-page IDs never enter this set.
});

test('busy exhaustion is terminal, bounded and can be retried manually', async () => {
  let reads = 0; const results = [], states = [];
  const options = {pause: async () => {}, activity: (_,busy) => states.push(busy)};
  await drain([1], async () => { reads++; return {state:'unknown',busy:true,message:'Publishing is running.'}; },
    (_,s) => results.push(s), true, options);
  assert.equal(reads, 3); assert.match(results[0].error, /retry later/);
  assert.deepEqual(states,[true,false]);
  await drain([1], async () => ({state:'no_change'}), (_,s) => results.push(s), true, options);
  assert.equal(results[1].state, 'no_change');
  assert.equal(states.at(-1), false);
});

for (const failure of ['network','http','json','deadline','invalid']) {
  test(failure + ' finalises checking and allows the queue to continue', async () => {
    const checking = [], results = [];
    const fetcher = async () => {
      if (failure === 'network') throw Error('offline');
      if (failure === 'deadline') return new Promise(() => {});
      return {ok: failure !== 'http', json: async () => {
        if (failure === 'json') throw Error('invalid JSON');
        return {unexpected: true};
      }};
    };
    await drain([1,2], id => id === 1 ? requestState(fetcher, '/fake', {}, 5) : Promise.resolve({state:'local_only'}),
      (_,s) => results.push(s), true, {activity: (_,active) => checking.push(active)});
    assert(results[0].error); assert.equal(results[1].state, 'local_only');
    assert.deepEqual(checking, [true,false,true,false]);
  });
}

test('unknown is terminal unavailable; last-known states never become invented No Change', () => {
  assert.equal(terminal({state:'unknown',label:'Checking Woo…',needs_refresh:false}).label, 'Unable to refresh');
  assert.deepEqual(failureView({state:'unknown',label:'Checking Woo…'}, 'offline'), {
    state:'unknown',label:'Unable to refresh',message:'No verified state is available. · offline'});
  const prior = {state:'no_change',label:'No Change',note:'Verified yesterday'};
  assert.equal(failureView(prior,'offline').state,'no_change');
  assert.match(failureView(prior,'offline').message,/Verified yesterday/);
  for (const state of ['update','remote_drift','blocked','link_candidate','recovery_required','local_only']) {
    assert.equal(terminal({state}).state, state);
  }
});
