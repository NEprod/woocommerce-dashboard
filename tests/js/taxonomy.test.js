const test = require('node:test');
const assert = require('node:assert/strict');
const { formatJSON, highlightJSON } = require('../../app/static/assets/js/taxonomy.js');
test('formatting preserves registry values and array order', () => {
  const text = '{"names":["Café","Paper & Ink"]}';
  assert.deepEqual(JSON.parse(formatJSON(text)), JSON.parse(text));
});
test('invalid JSON is rejected without returning a replacement', () => {
  assert.throws(() => formatJSON('{'));
});
test('formatting never silently drops duplicate keys', () => {
  assert.equal((formatJSON('{"key":1,"key":2}').match(/"key"/g) || []).length, 2);
});
test('syntax preview escapes authored HTML and remains bounded', () => {
  const preview = highlightJSON('{"name":"<img src=x onerror=alert(1)>"}');
  assert.ok(!preview.includes('<img'));
  assert.ok(preview.includes('&lt;img'));
  assert.ok(highlightJSON('x'.repeat(50000)).length === 40000);
});
