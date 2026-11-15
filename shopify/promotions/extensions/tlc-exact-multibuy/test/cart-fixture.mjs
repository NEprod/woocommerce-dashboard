// Print a non-customer fixture suitable for piping to Shopify's local Function runner.
const count=Number(process.argv[2] || '2');
const variant=i=>'gid://shopify/ProductVariant/'+String(i+1).padStart(30,'1');
const lines=Array.from({length:count},(_,i)=>({id:'gid://shopify/CartLine/'+(i+1),
  quantity:count===2?1:2,merchandise:{id:variant(i)},
  cost:{amountPerQuantity:{amount:'4.95',currencyCode:'GBP'}},sellingPlanAllocation:null}));
const tiers=count===2?[{qty:2,total:900}]:
  Array.from({length:8},(_,i)=>({qty:i+2,total:(i+2)*400+100}));
process.stdout.write(JSON.stringify({localization:{country:{isoCode:'GB'}},
  cart:{deliveryGroups:[],lines},
  discount:{discountClasses:['PRODUCT'],metafield:{jsonValue:{version:1,currency:'GBP',country:'GB',
    variants:Array.from({length:count===2?2:100},(_,i)=>variant(i)),tiers,repeat:true}}}}));
