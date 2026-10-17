"""Strict REST-shaped webhook adapter to the existing complete order contract.

Never fetches missing data. Ambiguous edited/refunded/discounted snapshots require
explicit API reconciliation rather than guessed GraphQL-only financial values.
"""
from decimal import Decimal
from app.shopify_connection import ShopifyError, _gid
from app.shopify_orders import normalise, amount_bag, quantity


def gid(value,kind):
    if isinstance(value,str) and value.startswith('gid:'):return _gid(value,kind)
    if type(value) is int and value>0:return _gid(f'gid://shopify/{kind}/{value}',kind)
    raise ShopifyError('Webhook identity is incomplete.')


def identity(raw,kind):
    value=gid(raw.get('admin_graphql_api_id',raw.get('id')),kind)
    if raw.get('id') is not None and gid(raw['id'],kind)!=value:
        raise ShopifyError('Webhook identity is inconsistent.')
    return value


def bag(value):
    return amount_bag({target:{'amount':value[source]['amount'],'currencyCode':value[source]['currency_code']}
        for source,target in [('shop_money','shopMoney'),('presentment_money','presentmentMoney')]})


def scale(value,factor):
    return {k:{**v,'amount':str(Decimal(v['amount'])*factor)} for k,v in value.items()}


def adapt(raw,topic):
    if topic=='refunds/create':raise ShopifyError('Refund events require complete order reconciliation.')
    if not isinstance(raw,dict) or not isinstance(raw.get('line_items'),list) or len(raw['line_items'])>1000:
        raise ShopifyError('Webhook order is incomplete.')
    # Webhook refunds, order edits, order-level allocations and tax adjustments do
    # not always carry the equivalent GraphQL financial/refundable quantities.
    if raw['refunds'] or raw.get('edited',False):raise ShopifyError('Webhook financial evidence requires reconciliation.')
    discounts=bag(raw['current_total_discounts_set'])
    if any(Decimal(m['amount'])!=0 for m in discounts.values()):
        raise ShopifyError('Webhook discount allocation requires reconciliation.')
    lines=[]
    for line in raw['line_items']:
        q=quantity(line['quantity'])
        if q!=quantity(line['current_quantity']) or line.get('discount_allocations') or Decimal(line['total_discount'])!=0:
            raise ShopifyError('Edited or discounted line requires reconciliation.')
        price=bag(line['price_set']);zero=scale(price,0);total=scale(price,q)
        lines.append({'id':identity(line,'LineItem'),'title':line['title'],'variantTitle':line['variant_title'],
            'sku':line['sku'],'quantity':q,'currentQuantity':q,'unfulfilledQuantity':line['fulfillable_quantity'],
            'refundableQuantity':q,'product':{'id':gid(line['product_id'],'Product')} if line['product_id'] else None,
            'variant':{'id':gid(line['variant_id'],'ProductVariant')} if line['variant_id'] else None,
            'customAttributes':[{'key':p['name'],'value':p['value']} for p in line['properties']],
            'originalUnitPriceSet':price,'originalTotalSet':total,'discountedUnitPriceAfterAllDiscountsSet':price,
            'discountedTotalSet':total,'totalDiscountSet':zero})
    shipping=[{'id':identity(s,'ShippingLine'),'title':s['title'],'code':s['code'],
        'originalPriceSet':bag(s['price_set']),'discountedPriceSet':bag(s['discounted_price_set'])} for s in raw['shipping_lines']]
    # No refunds/edits/discounts: original and current shipping are identical.
    money={target:bag(raw[source]) for source,target in [
        ('total_price_set','totalPriceSet'),('current_total_price_set','currentTotalPriceSet'),
        ('subtotal_price_set','subtotalPriceSet'),('current_subtotal_price_set','currentSubtotalPriceSet'),
        ('total_discounts_set','totalDiscountsSet'),('current_total_discounts_set','currentTotalDiscountsSet'),
        ('total_tax_set','totalTaxSet'),('current_total_tax_set','currentTotalTaxSet'),
        ('total_shipping_price_set','totalShippingPriceSet')]}
    for original,current in [('totalPriceSet','currentTotalPriceSet'),('subtotalPriceSet','currentSubtotalPriceSet'),
                             ('totalTaxSet','currentTotalTaxSet'),('totalDiscountsSet','currentTotalDiscountsSet')]:
        if money[original]!=money[current]:raise ShopifyError('Adjusted order requires complete reconciliation.')
    money['currentShippingPriceSet']=money['totalShippingPriceSet']
    money['totalRefundedSet']=scale(money['currentTotalPriceSet'],0)
    customer=raw['customer']
    if customer is not None:
        customer={'id':gid(customer['id'],'Customer'),
            'displayName':' '.join(x for x in (customer['first_name'],customer['last_name']) if x) or raw.get('email') or 'Customer'}
    address=raw['shipping_address']
    if address is not None:
        address={**{k:address[k] for k in ('name','company','address1','address2','city','province','country','zip','phone')},
                 'provinceCode':address['province_code'],'countryCodeV2':address['country_code']}
    status=raw['fulfillment_status']
    mapping={None:'UNFULFILLED','fulfilled':'FULFILLED','partial':'PARTIALLY_FULFILLED','restocked':'RESTOCKED'}
    if status not in mapping:raise ShopifyError('Webhook fulfilment status is unsupported.')
    result={'id':identity(raw,'Order'),'name':raw['name'],'createdAt':raw['created_at'],'updatedAt':raw['updated_at'],
        'test':raw['test'],'currencyCode':raw['currency'],'displayFinancialStatus':raw['financial_status'].upper(),
        'displayFulfillmentStatus':mapping[status],'cancelledAt':raw['cancelled_at'],
        'cancelReason':raw['cancel_reason'].upper() if raw['cancel_reason'] else None,
        'email':raw['email'],'phone':raw['phone'],'customer':customer,'shippingAddress':address,
        'shippingLines':{'nodes':shipping,'pageInfo':{'hasNextPage':False}},'_lines':lines,**money}
    return normalise(result,api_version='2026-10')
