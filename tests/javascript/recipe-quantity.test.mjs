import {test} from 'node:test';
import assert from 'node:assert/strict';
import {batchMaterial} from '../../app/static/assets/js/recipe-quantity.mjs';

test('per-piece batch totals and independent materials retain fixed-point precision',()=>{
  assert.equal(batchMaterial('13','10','g'),'130');
  assert.equal(batchMaterial('2','10','g'),'20');
  assert.equal(batchMaterial('0.013','10','kg'),'0.13');
  assert.equal(batchMaterial('0.000001','3','g'),'0.000003');
});
test('unchanged legacy rounded display retains exact stored batch total',()=>{
  assert.equal(batchMaterial('0.333333','3','g',{piece:'0.333333',good:3,unit:'g',total:'1'}),'1');
  assert.equal(batchMaterial('13','10','g',{piece:'0.333333',good:3,unit:'g',total:'1'}),'130');
});
test('invalid or missing inputs cannot appear as valid batch quantities',()=>{
  for(const [qty, good, unit] of [['','10','g'],['13','0','g'],['-1','10','g'],['13','1.5','g'],['0','10','g'],['13','10','']])
    assert.equal(batchMaterial(qty,good,unit),null);
});
