import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const loader = new URL('../../app/static/assets/js/product-bom.js', import.meta.url);
const window = {};
runInNewContext(readFileSync(loader, 'utf8'), {window});
const {Editor, layersFor, rows} = window.TLCProduction;
const add = (item_id, quantity = '1') => ({op:'add', item_id, quantity, unit:'piece'});

test('shared changes flow through sparse product and variant deltas', () => {
  const shared = {lines:{box:add(10), sleeve:add(11), thanks:add(12)}};
  const product = {lines:{sleeve:{op:'modify',quantity:'2'}, insert:add(13)},variants:{
    'A3-0001':{lines:{box:{op:'remove'}, paper:add(20)}},
    'A4-0002':{lines:{paper:add(21)}}
  }};
  const a4 = rows(layersFor(shared,product,'A4-0002'));
  const a3 = rows(layersFor(shared,product,'A3-0001'));
  assert.equal(a4.get('paper').item_id, 21);
  assert.equal(a3.get('paper').item_id, 20);
  assert.equal(a3.get('box').excluded, true);
  assert.equal(a4.get('box').excluded, false);
  assert.equal(a3.get('sleeve').quantity, '2');
  assert.equal(a3.get('thanks').item_id, 12);
  assert.equal(a3.get('insert').item_id, 13);
  delete product.lines.sleeve;
  assert.equal(rows(layersFor(shared,product,'A3-0001')).get('sleeve').quantity,'1');
  assert.equal(shared.lines.sleeve.quantity,'1');
});

test('reset removes only the selected change, without dropping sibling variant deltas', () => {
  const editor = Object.create(Editor.prototype);
  editor.value = {lines:{item:add(7)},variants:{'0003':{lines:{box:{op:'remove'}}},'0004':{lines:{paper:add(9)}}}};
  editor.onChange = () => {};
  editor.schedule = () => {};
  editor.change('0003','box',null,false);
  assert.equal(editor.value.variants['0003'],undefined);
  assert.equal(editor.value.variants['0004'].lines.paper.item_id,9);
  assert.equal(editor.value.lines.item.item_id,7);
});

test('existing editor mounts expandable variant panels and uses the existing save path', () => {
  const designer = readFileSync(loader,'utf8');
  const editor = readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
  const template = readFileSync(new URL('../../app/templates/metadata_editor.html',import.meta.url),'utf8');
  assert.match(template,/data-production-editor/);
  assert.match(editor,/const production = productionDesigner\?\.get\(\)/);
  assert.match(editor,/result\.production = production/);
  assert.match(designer,/make\('details'\)/);
  assert.match(designer,/details\.addEventListener\('toggle'/);
  assert.match(designer,/method:'POST'/);
  assert.match(designer,/'X-CSRFToken'/);
  assert.doesNotMatch(designer,/innerHTML|document\.querySelector.+save/);
});
