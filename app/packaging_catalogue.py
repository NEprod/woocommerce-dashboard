"""Validated package catalogue. Units are cm, grams and integer GBP pence."""
from datetime import datetime
from decimal import Decimal
import json
import uuid
from app import db
from app.packaging_models import Package, PackageLink
from app.packaging_contract import AXES, number

INITIAL = (
 ('c5-plus','TLC C5+ Board Back Envelope','ENVELOPE','24','16.5',None),
 ('large-letter','TLC Large Letter Box','BOX','35.3','25','2.5'),
 ('small-parcel','TLC Small Parcel Box','BOX','45','35','16'),
 ('medium-parcel','TLC Medium Parcel Box','BOX','61','46','46'),
 ('a4-tube','TLC A4 Poster Tube','TUBE','24',None,None),
 ('a3-tube','TLC A3 Poster Tube','TUBE',None,None,None),
)


def dumps(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def initial_rows():
    for key,name,kind,l,w,h in INITIAL:
        spec=clean({'type':kind,'external_cm':dict(zip(AXES,(l,w,h))),
            'external_diameter_cm':'5' if key=='a4-tube' else None,
            'max_contents_thickness_mm':'3' if key=='c5-plus' else None,
            'notes':'Proposed/advertised dimensions, not physically verified. Measure usable interior, protection and tare; no Shopify changes.'})
        yield {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'tlcdashboard:packaging:'+key)),
               'seed_key':key,'name':name,'archived':False,'version':1,'specification':dumps(spec),'updated_at':datetime.utcnow()}


def clean(value):
    if not isinstance(value,dict): raise ValueError('Package must be an object.')
    result={}
    result['type']=value.get('type','BOX')
    if result['type'] not in {'BOX','ENVELOPE','SOFT_PACK','TUBE'}: raise ValueError('Choose Box, Envelope, Soft Pack or Tube.')
    for group in ('external_cm','internal_cm'):
        source=value.get(group) or {}
        if not isinstance(source,dict): raise ValueError('Dimensions must contain length, width and height.')
        result[group]={key:(str(number(source.get(key),f'{group} {key}',optional=True)) if source.get(key) not in (None,'') else None) for key in AXES}
    for key in ('tare_g','max_contents_g','max_gross_g','clearance_cm','protection_weight_g',
                'max_contents_thickness_mm','external_diameter_cm','internal_diameter_cm','end_cap_allowance_cm'):
        amount=number(value.get(key),key,zero=key in {'tare_g','clearance_cm','protection_weight_g','end_cap_allowance_cm'},optional=True)
        result[key]=str(amount) if amount is not None else None
    cost=number(value.get('cost_pence'), 'Packaging cost in pence',zero=True,optional=True)
    if cost is not None and cost!=cost.to_integral_value(): raise ValueError('Cost must be whole pence.')
    result['cost_pence']=int(cost) if cost is not None else None
    for key in ('dimensions_measured','tare_measured','automatic','flat','fragile','rolled'):
        raw=value.get(key,False)
        if type(raw) is not bool: raise ValueError(key+' must be true or false.')
        result[key]=raw
    protection=value.get('protection') or []
    if not isinstance(protection,list) or len(protection)>20 or any(not isinstance(v,str) or not 1<=len(v)<=80 for v in protection): raise ValueError('Protection must be a list of short material names.')
    result['protection']=list(dict.fromkeys(protection))
    result['notes']=str(value.get('notes') or '')
    if len(result['notes'])>2000: raise ValueError('Notes exceed 2000 characters.')
    for key in AXES:
        outer,inner=result['external_cm'][key],result['internal_cm'][key]
        if inner and outer and Decimal(inner)>Decimal(outer): raise ValueError('Usable interior cannot exceed external dimensions.')
    if result['max_gross_g'] and result['tare_g'] and Decimal(result['max_gross_g'])<=Decimal(result['tare_g']): raise ValueError('Gross capacity must exceed tare.')
    if result['internal_diameter_cm'] and result['external_diameter_cm'] and Decimal(result['internal_diameter_cm'])>Decimal(result['external_diameter_cm']):
        raise ValueError('Usable diameter cannot exceed external diameter.')
    return result


def readiness(spec):
    missing=[]
    if spec['type']=='TUBE':
        for group in ('external_cm','internal_cm'):
            if not spec[group].get('length'): missing.append(group.replace('_cm','')+' tube length')
        for key in ('internal_diameter_cm','external_diameter_cm','end_cap_allowance_cm'):
            if spec.get(key) is None: missing.append(key.replace('_cm','').replace('_',' '))
    else:
        for group in ('external_cm','internal_cm'):
            if any(spec.get(group,{}).get(k) is None for k in AXES): missing.append(group.replace('_cm','').replace('_',' ')+' dimensions')
    for key,label in [('tare_g','tare weight'),('max_contents_g','contents weight limit'),('clearance_cm','package clearance'),('protection_weight_g','protective material weight')]:
        if spec.get(key) is None: missing.append(label)
    if missing: return 'Measurements needed',missing
    if not spec['dimensions_measured'] or not spec['tare_measured']: return 'Estimated fit',['Confirm measured dimensions and tare weight']
    if not spec['automatic']: return 'Manual only',['Enable solo recommendations after measurement']
    return 'Ready',[]


def view(row):
    spec=json.loads(row.specification)
    state,warnings=readiness(spec)
    links=PackageLink.query.filter_by(package_id=row.id).all()
    return {'id':row.id,'name':row.name,'archived':row.archived,'version':row.version,**spec,
        'readiness':state,'warnings':warnings,'links':links}


def save(data, row=None, *, version=None):
    name=str(data.get('name') or '').strip()
    if not 1<=len(name)<=191: raise ValueError('Package name is required (maximum 191 characters).')
    spec=clean(data)
    if row:
        # Compare-and-swap prevents concurrent tabs from silently losing edits.
        changed=db.session.query(Package).filter_by(id=row.id,version=version).update({
            Package.name:name,Package.specification:dumps(spec),Package.version:version+1,Package.updated_at:datetime.utcnow()},synchronize_session=False)
        if not changed: raise ValueError('Package changed in another session. Reload before saving.')
    else:
        row=Package(id=str(uuid.uuid4()),name=name,specification=dumps(spec))
        db.session.add(row)
    db.session.commit();db.session.expire_all()
    return row
