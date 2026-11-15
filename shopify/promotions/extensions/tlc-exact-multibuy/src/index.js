import { cents, exactQuote, MAX_LINES } from '../../../../../app/static/assets/js/promotion-quote.mjs';

const zero = () => ({operations:[]});

export function cartLinesDiscountsGenerateRun(input) {
  const configuration = input?.discount?.metafield?.jsonValue;
  if (!configuration || configuration.version !== 1 || configuration.currency !== 'GBP' ||
      configuration.country !== 'GB' || input?.localization?.country?.isoCode !== 'GB' ||
      !input.discount.discountClasses?.includes('PRODUCT') || !Array.isArray(input?.cart?.lines) || input.cart.lines.length > MAX_LINES ||
      !Array.isArray(input.cart.deliveryGroups) ||
      input.cart.deliveryGroups.some(group => group.deliveryAddress?.countryCode && group.deliveryAddress.countryCode !== 'GB')) return zero();
  const lines = [];
  for (const line of input.cart.lines) {
    if (line.sellingPlanAllocation) continue;
    const price = line.cost?.amountPerQuantity;
    if (price?.currencyCode !== 'GBP') return zero();
    lines.push({id:line.id, variant:line.merchandise?.id, quantity:line.quantity, pence:cents(price.amount)});
  }
  const quote = exactQuote(lines, configuration);
  if (!quote?.savings) return zero();
  return {operations:[{productDiscountsAdd:{
    selectionStrategy:'ALL',
    candidates:quote.allocations.map(({id,quantity,pence}) => ({
      message:'TLC multi-buy',
      targets:[{cartLine:{id,quantity}}],
      value:{fixedAmount:{amount:(pence / 100).toFixed(2),appliesToEachItem:false}}
    }))
  }}]};
}
