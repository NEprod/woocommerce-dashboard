"""Fixed read-only Admin 2026-10 queries; no order mutation is registered."""
MONEY = 'shopMoney { amount currencyCode } presentmentMoney { amount currencyCode }'
ORDER_MONEY = ('totalPriceSet','currentTotalPriceSet','subtotalPriceSet','currentSubtotalPriceSet',
               'totalDiscountsSet','currentTotalDiscountsSet','totalTaxSet','currentTotalTaxSet',
               'totalShippingPriceSet','currentShippingPriceSet','totalRefundedSet')
LINE_MONEY = ('originalUnitPriceSet','originalTotalSet','discountedUnitPriceAfterAllDiscountsSet',
              'discountedTotalSet','totalDiscountSet')
INDEX = '''query TLCOrdersIndex($after: String, $query: String!, $first: Int!) {
 orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
 nodes { id updatedAt } pageInfo { hasNextPage endCursor } } }'''
REVISION = '''query TLCOrderRevision($id: ID!) { order(id: $id) { id updatedAt } }'''
DETAIL = '''query TLCOrderRead($id: ID!, $after: String) { order(id: $id) {
 id name createdAt updatedAt test currencyCode displayFinancialStatus displayFulfillmentStatus
 cancelledAt cancelReason email phone customer { id displayName }
 shippingAddress { name company address1 address2 city province provinceCode country countryCodeV2 zip phone }
 ''' + '\n'.join(k+' { '+MONEY+' }' for k in ORDER_MONEY) + '''
 shippingLines(first: 10) { nodes { id title code originalPriceSet { '''+MONEY+''' }
 discountedPriceSet { '''+MONEY+''' } } pageInfo { hasNextPage } }
 lineItems(first: 25, after: $after) { nodes {
 id title variantTitle sku quantity currentQuantity unfulfilledQuantity refundableQuantity
 product { id } variant { id } customAttributes { key value }
 ''' + '\n'.join(k+' { '+MONEY+' }' for k in LINE_MONEY) + '''
 } pageInfo { hasNextPage endCursor } }
} }'''
READ_QUERIES = frozenset((INDEX, DETAIL, REVISION))
