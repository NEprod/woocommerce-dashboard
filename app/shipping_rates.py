"""Merchant-entered, versioned rates; never masquerade as live Royal Mail quotes."""
from datetime import datetime, timezone
from decimal import Decimal
from app.packaging_contract import number

SERVICES={'tracked_24':'Royal Mail Tracked 24','tracked_48':'Royal Mail Tracked 48'}


def validate(config):
    if not isinstance(config,dict):raise ValueError('Rate configuration must be an object.')
    if config.get('currency')!='GBP' or config.get('country')!='GB':raise ValueError('This milestone supports GBP rates for UK/GB destinations only.')
    for key in ('source','effective_from','effective_until'):
        if not isinstance(config.get(key),str) or not config[key].strip():raise ValueError(key+' is required.')
    start,end=(datetime.fromisoformat(config[k].replace('Z','+00:00')) for k in ('effective_from','effective_until'))
    if start.tzinfo is None or end.tzinfo is None or start>=end:raise ValueError('Use explicit timezone-aware ordered rate validity times.')
    if len(config['source'])>500:raise ValueError('Rate source is too long.')
    if type(config.get('handling_pence',0)) is not int or not 0<=config.get('handling_pence',0)<=100000:raise ValueError('Handling uses non-negative integer pence.')
    if type(config.get('include_packaging',False)) is not bool:raise ValueError('Include packaging must be boolean.')
    rows=config.get('bands')
    if not isinstance(rows,list) or not 1<=len(rows)<=100:raise ValueError('Enter 1–100 explicit service bands.')
    for r in rows:
        if not isinstance(r,dict):raise ValueError('Every rate band must be an object.')
        if r.get('service') not in SERVICES:raise ValueError('Use tracked_24 or tracked_48.')
        if r.get('format') not in {'large_letter','small_parcel','medium_parcel'}:raise ValueError('Unknown postal format.')
        for k in ('max_weight_g','max_length_cm','max_width_cm','max_height_cm'):number(r.get(k),k)
        if type(r.get('price_pence')) is not int or not 0<=r['price_pence']<=100000:raise ValueError('Prices use non-negative integer GBP pence.')
    return config


def quote(parcels,config,*,destination='GB',at=None):
    at=at or datetime.now(timezone.utc)
    output=[]
    for service,name in SERVICES.items():
        result={'service':service,'name':name,'available':False,'source':'No configured rates','reason':'Enter verified merchant rates before quoting.'}
        output.append(result)
        if not config:continue
        validate(config)
        result['source']=config['source'];result['rate_version']=config.get('version')
        result['kind']='merchant_rate_table'
        if destination!='GB':result['reason']='Destination is outside configured UK coverage.';continue
        start,end=(datetime.fromisoformat(config[k].replace('Z','+00:00')) for k in ('effective_from','effective_until'))
        if not start<=at<end:result['reason']='Rate table is future-dated or expired.';continue
        charges=[]
        for parcel in parcels:
            dims=sorted(map(Decimal,parcel['external_cm'].values()),reverse=True)
            rows=[r for r in config['bands'] if r['service']==service and r['format']==parcel['postal_category'] and
                Decimal(parcel['gross_weight_g'])<=Decimal(str(r['max_weight_g'])) and
                all(dims[i]<=Decimal(str(r[k])) for i,k in enumerate(('max_length_cm','max_width_cm','max_height_cm')))]
            if not rows:result['reason']='At least one parcel has no eligible dimensional/weight rate.';break
            charges.append(min(r['price_pence'] for r in rows))
        else:
            if config.get('include_packaging') and any(p['material_cost_pence'] is None for p in parcels):
                result['reason']='Packaging cost is missing.';continue
            total=sum(charges)+config.get('handling_pence',0)
            if config.get('include_packaging'):total+=sum(p['material_cost_pence'] for p in parcels)
            result.update(available=True,total_pence=total,parcel_charges_pence=charges,
                reason='Configured merchant table; not a live carrier/Shopify Shipping quote.')
    return output
