import {test} from 'node:test';
import assert from 'node:assert/strict';
import {exactQuote,cents} from '../../../../../app/static/assets/js/promotion-quote.mjs';
import {cartLinesDiscountsGenerateRun as run} from '../src/index.js';

const variant = n => 'gid://shopify/ProductVariant/'+n;
const line = (id,n,quantity,pence) => ({id,variant:variant(n),quantity,pence});
const promo = (ids,qty,total,repeat=true) => ({version:1,currency:'GBP',country:'GB',variants:ids.map(variant),tiers:[{qty,total}],repeat});

test('mixed designs qualify, separate dumpling groups never share a unit',() => {
  const standard = promo([1,2],2,900);
  const keyring = promo([3,4],2,1100);
  const s = line('s',1,1,495), s2 = line('s2',2,1,495), k = line('k',3,1,595), k2 = line('k2',4,1,595);
  for (let q=1;q<=4;q++) {
    assert.equal(q*495-exactQuote([{...s,quantity:q}],standard).savings,[495,900,1395,1800][q-1]);
    assert.equal(q*595-exactQuote([{...k,quantity:q}],keyring).savings,[595,1100,1695,2200][q-1]);
  }
  assert.equal(exactQuote([s,k],standard).savings+exactQuote([s,k],keyring).savings,0);
  assert.equal(2180-exactQuote([s,s2,k,k2],standard).savings-exactQuote([s,s2,k,k2],keyring).savings,2000);
});

test('selected ornaments, size groups, repeated tiers and mixed-price choices',() => {
  const offer = promo([1,2,3],3,1995);
  const a=line('a',1,1,699),b=line('b',2,1,749),c=line('c',3,1,799);
  assert.equal(2247-exactQuote([a,b,c,line('other',4,2,2000)],offer).savings,1995);
  for (const [quantity, savings] of [[3,102],[4,102],[6,204]]) {
    assert.equal(exactQuote([{...a,quantity}],offer).savings,savings);
  }
  assert.equal(exactQuote([{...a,quantity:6}],{...offer,repeat:false}).groups,1);
  assert.equal(exactQuote([line('a',1,2,600),line('b',2,1,600)],offer).savings,0);
  const tiers={...promo([1,2],2,900),tiers:[{qty:2,total:900},{qty:3,total:1200}]};
  assert.equal(exactQuote([line('a',1,5,500)],tiers).savings,400);
  assert.equal(exactQuote([line('a',1,1,495),line('b',2,1,595)],promo([1],2,900)).savings,0);
  assert.equal(exactQuote([a,b,c],offer).savings,exactQuote([c,b,a],offer).savings);
});

test('A5, A4 and A3 mixes only qualify within their own size group',() => {
  const offers=[promo([1,2],2,800),promo([3,4],2,1200),promo([5,6],2,1700)];
  const mixed=[line('a5',1,1,500),line('a4',3,1,750),line('a3',5,1,1000)];
  assert.deepEqual(offers.map(o=>exactQuote(mixed,o).savings),[0,0,0]);
  const pairs=[...mixed,line('a5-other',2,1,500),line('a4-other',4,1,750),line('a3-other',6,1,1000)];
  assert.deepEqual(offers.map(o=>exactQuote(pairs,o).savings),[200,300,300]);
});

test('odd-penny allocation, duplicate lines and bounds',() => {
  const ids=[line('b',1,1,333),line('a',1,1,334),line('c',1,1,335)];
  const offer=promo([1],3,999);
  const result=exactQuote(ids,offer);
  assert.equal(result.savings,3);
  assert.equal(result.allocations.reduce((acc,a)=>acc+a.pence,0),3);
  assert.deepEqual(result,exactQuote(ids.toReversed(),offer));
  assert.equal(exactQuote([ids[0],ids[0]],offer),null);
  assert.equal(exactQuote([line('too-many',1,49,495)],offer),null);
  assert.equal(exactQuote([ids[0]],{...offer,tiers:[{qty:0,total:3}]}),null);
  assert.equal(cents('4.950000'),495);
  assert.equal(cents('4.951'),null);
});

function input(currency='GBP',country='GB') {
  return {
    localization:{country:{isoCode:country}},
    discount:{discountClasses:['PRODUCT'],metafield:{jsonValue:promo([1,2],2,900)}},
    cart:{deliveryGroups:[],lines:[{id:'gid://shopify/CartLine/0',quantity:2,merchandise:{id:variant(1)},
      cost:{amountPerQuantity:{currencyCode:currency,amount:'4.95'}}}]}
  };
}

test('operation targets the awarded quantity and rejects wrong currency or country',() => {
  const data=input();
  const candidate=run(data).operations[0].productDiscountsAdd.candidates[0];
  assert.deepEqual(candidate.targets,[{cartLine:{id:'gid://shopify/CartLine/0',quantity:2}}]);
  assert.deepEqual(candidate.value.fixedAmount,{amount:'0.90',appliesToEachItem:false});
  for (const invalid of [input('USD'),input('GBP','US'),{}]) assert.deepEqual(run(invalid),{operations:[]});
  data.cart.deliveryGroups.push({deliveryAddress:{countryCode:'FR'}});
  assert.deepEqual(run(data),{operations:[]});
  data.cart.deliveryGroups=[];
  data.cart.lines[0].sellingPlanAllocation={sellingPlan:{id:'1'}};
  assert.deepEqual(run(data),{operations:[]});
});
