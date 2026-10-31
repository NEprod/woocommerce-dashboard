import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../app/static/assets/js/prepared-metadata.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('  function prune('), source.indexOf('  function updateLines('));

test('direct Intake guided edits preserve supported properties without dedicated controls', () => {
  const authored = {
    price: '3.50', live: false, variation_attributes: [],
    customisation: {proof_policy: 'required', storefront_enabled: true,
      fields: {dogs_name: {label: 'Dog’s name', type: 'text', required: true}}},
    storefront_collections: ['Dogs'], shopify_channels: {online_store: false},
    future_setting: {preserve: true},
  };
  const original = structuredClone(authored);
  const context = vm.createContext({
    structuredClone, authored,
    root: {hasAttribute: () => true},
    guided: {querySelectorAll: selector => selector === '[data-field]' ? [
      {dataset: {field: 'price'}, value: '4.00'},
      {dataset: {field: 'live'}, value: 'false'},
    ] : []},
    known: new Set(Object.keys(authored)), packagingDesigner: null, finishedDesigner: null,
    listValues: () => [], attributesValue: () => ({}), modifiersValue: () => ({}),
  });
  const actual = JSON.parse(vm.runInContext(code + ';JSON.stringify(guidedDocument())', context));
  assert.equal(actual.price, '4.00');
  for (const key of ['customisation', 'storefront_collections', 'shopify_channels', 'variation_attributes', 'future_setting']) {
    assert.deepEqual(actual[key], original[key]);
  }
  assert.equal(actual.live, false);
  assert.deepEqual(authored, original);
});
