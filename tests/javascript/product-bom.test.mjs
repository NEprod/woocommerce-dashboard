import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const loader = new URL('../../app/static/assets/js/product-bom.js', import.meta.url);
const window = {};
runInNewContext(readFileSync(loader, 'utf8'), {window});
const {Editor, layersFor, rows, modeOf, rule} = window.TLCProduction;
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

test('default Inherit and legacy Add do not materialize copies of shared option rules', () => {
  const shared = {options:{Size:{A3:{mode:'replace',lines:{paper:add(9)}}}}};
  const a3 = {name:'Size',value:'A3'};
  assert.equal(modeOf({},{}),'inherit');
  assert.equal(modeOf({}, {lines:{insert:add(2)}}),'add');
  assert.equal(modeOf(rule(shared,a3),{}),'replace');
  assert.equal(modeOf(rule(shared,a3),{mode:'inherit'}),'inherit');
  assert.equal(modeOf(rule(shared,a3),{mode:'add'}),'add');
  assert.equal(rule(shared,{name:'Size',value:'A4'}).mode,undefined);
  assert.equal(rule({}, {name:'constructor',value:'prototype'}).mode,undefined);
});

test('replacement requires consent and changing modes leaves shared BOM and manual cost intact', () => {
  const editor = Object.create(Editor.prototype);
  const option = {name:'Size',value:'A3'};
  editor.value = {options:{Size:{A3:{mode:'add',lines:{insert:add(2)}}}},
    variants:{'SKU':{cost:{mode:'manual',amount:'0.00'},lines:{extra:add(3)}}}};
  editor.shared = {lines:{box:add(1)}};
  const notices = [];
  let confirm = false;
  window.confirm = (message) => { notices.push(message); return confirm; };
  editor.changed = () => {};
  assert.equal(editor.changeMode(option,'replace'),false);
  assert.equal(rule(editor.value,option).lines.insert.item_id,2);
  assert.match(notices[0],/packaging/);
  confirm = true;
  assert.equal(editor.changeMode(option,'replace'),true);
  assert.equal(rule(editor.value,option).mode,'replace');
  assert.equal(rule(editor.value,option).lines,undefined);
  assert.equal(editor.changeMode('SKU','replace'),true);
  assert.equal(editor.value.variants.SKU.cost.amount,'0.00');
  assert.equal(editor.shared.lines.box.item_id,1);
  assert.equal(editor.changeMode(option,'inherit'),true);
  assert.equal(rule(editor.value,option).mode,'inherit');
});

test('variant list defaults to authored exceptions or flagged variants, but keeps selected ones', () => {
  const editor = Object.create(Editor.prototype);
  editor.value = {variants:{OWN:{mode:'replace'}}};
  editor.shared = {variants:{SHARED:{lines:{paper:add(1)}}}};
  editor.previewData = {variants:{CLASH:{warnings:['Competing replacement BOMs']}, PLAIN:{warnings:[]}}};
  editor.selectedVariants = new Set(['FIND']);
  for (const sku of ['OWN','SHARED','CLASH','FIND']) assert.ok(editor.variantNeedsCard({sku}));
  assert.ok(!editor.variantNeedsCard({sku:'PLAIN'}));
  const source = readFileSync(loader,'utf8');
  assert.match(source,/Option Materials & Components/);
  assert.match(source,/Individual Variant Adjustments/);
  assert.match(source,/\[v\.sku,v\.label,v\.identity\]/);
  assert.match(source,/Inherited: .*Resolved replacement:/);
});
