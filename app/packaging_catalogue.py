"""Simple local package catalogue; historical specification data is retained."""
from datetime import datetime
from decimal import Decimal
import json
import uuid
from urllib.parse import urlsplit
from app import db
from app.packaging_models import Package, PackageLink, PackagingEvidence
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
            'notes':'Proposed/advertised dimensions, not physically verified; no Shopify changes.'})
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
    for key in ('tare_g','max_contents_thickness_mm','external_diameter_cm','internal_diameter_cm'):
        amount=number(value.get(key),key,zero=key=='tare_g',optional=True)
        result[key]=str(amount) if amount is not None else None
    purchase=number(value.get('purchase_price'),'Purchase price',zero=True,optional=True)
    if purchase is not None and purchase != purchase.quantize(Decimal('.01')):
        raise ValueError('Purchase price supports at most two decimal places.')
    result['purchase_price']=str(purchase) if purchase is not None else None
    result['purchase_url']=str(value.get('purchase_url') or '').strip()
    if result['purchase_url']:
        try:
            url=urlsplit(result['purchase_url'])
            if len(result['purchase_url'])>2048 or url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or any(ord(c)<33 for c in result['purchase_url']): raise ValueError()
        except ValueError: raise ValueError('Use a complete http/https purchase URL without embedded credentials.') from None
    for key in AXES:
        if result['type']=='ENVELOPE' and key=='height': continue  # exterior envelope thickness is not contents capacity
        if result['type']=='TUBE' and key!='length': continue  # retained rectangular axes are not tube geometry
        outer,inner=result['external_cm'][key],result['internal_cm'][key]
        if inner and outer and Decimal(inner)>Decimal(outer): raise ValueError('Usable interior cannot exceed external dimensions.')
    if result['type']=='TUBE' and result['internal_diameter_cm'] and result['external_diameter_cm'] and Decimal(result['internal_diameter_cm'])>Decimal(result['external_diameter_cm']):
        raise ValueError('Usable diameter cannot exceed external diameter.')
    return result


def envelope_height(spec):
    """Only authored internal height is active; historical rules need review."""
    inner=number(spec.get('internal_cm',{}).get('height'),'Internal height',optional=True)
    legacy=number(spec.get('max_contents_thickness_mm'),'Legacy contents thickness',optional=True)
    legacy_cm=legacy/10 if legacy is not None else None
    warning=None
    if legacy_cm is not None:
        warning=f'Historical contents value: {legacy} mm ({legacy_cm} cm), not an active second rule. Transfer or confirm internal height explicitly.'
        if inner is not None and inner!=legacy_cm:
            return None,warning+f' Conflict: stored internal height is {inner} cm. Review before recommending; neither value is chosen automatically.'
    return inner,warning


def usable_bounds(spec):
    """Internal axes win; nominal exterior fallback is always an estimate."""
    keys=('length','diameter') if spec['type']=='TUBE' else AXES
    bounds=[];warnings=[]
    for axis in keys:
        inner=spec.get('internal_diameter_cm') if axis=='diameter' else spec.get('internal_cm',{}).get(axis)
        outer=spec.get('external_diameter_cm') if axis=='diameter' else spec.get('external_cm',{}).get(axis)
        value=inner
        if spec['type']=='ENVELOPE' and axis=='height':
            height,notice=envelope_height(spec)
            bounds.append(height)
            if notice: warnings.append(notice)
            if height is None: warnings.append('Internal usable envelope height / contents thickness is unknown; exterior height is not a contents limit.')
            continue
        if value is None:
            value=outer
            warnings.append('Usable '+axis+' unknown; external dimensions are an estimate, not proof of physical fit.')
        bounds.append(Decimal(value) if value is not None else None)
    return bounds,warnings


def readiness(spec):
    bounds,warnings=usable_bounds(spec)
    if spec['type']=='ENVELOPE' and spec.get('internal_cm',{}).get('height') is not None and envelope_height(spec)[0] is None:
        return 'Review historical thickness',warnings
    if any(v is None for v in bounds): return 'Measurements needed',['Package size is incomplete; manual selection remains available.']+warnings
    if warnings: return 'Estimated Fit',warnings
    return 'Internal size available',[]


def view(row):
    spec=json.loads(row.specification)
    state,warnings=readiness(spec)
    height,notice=envelope_height(spec) if spec['type']=='ENVELOPE' else (None,None)
    links=PackageLink.query.filter_by(package_id=row.id).all()
    return {'id':row.id,'name':row.name,'archived':row.archived,'version':row.version,**spec,
        'readiness':state,'warnings':warnings,'links':links,
        'effective_envelope_height_cm':str(height) if height is not None else None,'envelope_thickness_notice':notice}


def from_display_units(data):
    """Package-form units only; canonical package storage stays cm/g."""
    import copy
    result=copy.deepcopy(data)
    lengths={'cm':Decimal(1),'mm':Decimal('.1'),'in':Decimal('2.54')}
    weights={'g':Decimal(1),'kg':Decimal(1000),'oz':Decimal('28.349523125'),'lb':Decimal('453.59237')}
    try: lf=lengths[result.pop('dimension_unit','cm')];wf=weights[result.pop('weight_unit','g')]
    except (KeyError,TypeError):raise ValueError('Choose supported package measurement and weight units.') from None
    def convert(value,factor,label):
        return str(number(value,label,zero=True)*factor) if value not in (None,'') else None
    for group in ('external_cm','internal_cm'):
        if group in result:
            if not isinstance(result[group],dict):raise ValueError('Package dimensions must be an object.')
            result[group]={k:convert(v,lf,k) for k,v in result[group].items()}
    for key in ('external_diameter_cm','internal_diameter_cm'):
        if key in result:result[key]=convert(result[key],lf,key)
    for key in ('tare_g',):
        if key in result:result[key]=convert(result[key],wf,key)
    return result


def save(data, row=None, *, version=None, commit=True):
    data=from_display_units(data)
    name=str(data.get('name') or '').strip()
    if not 1<=len(name)<=191: raise ValueError('Package name is required (maximum 191 characters).')
    if any(p.id!=(row.id if row else None) and ' '.join(p.name.split()).casefold()==' '.join(name.split()).casefold() for p in Package.query.all()):
        raise ValueError('A package with this name already exists. Edit it or choose a distinct name.')
    # Retired controls remain historical JSON only, not active fitting settings.
    # Sparse forms preserve them; explicit empty measurements clear that axis.
    previous=json.loads(row.specification) if row else {}
    thickness_history=None
    merged={**previous,**data}
    if row: merged['max_contents_thickness_mm']=previous.get('max_contents_thickness_mm')  # historical key is read-only except reviewed retirement
    for group in ('external_cm','internal_cm'):
        if group in data and isinstance(data[group],dict): merged[group]={**previous.get(group,{}),**data[group]}
    # No automatic legacy transfer/default. A reviewed local action archives the
    # historical rule and makes the merchant's chosen internal height sole authority.
    if row and merged.get('type')=='ENVELOPE' and previous.get('max_contents_thickness_mm') is not None and 'height' in data.get('internal_cm',{}):
        old=number(previous.get('internal_cm',{}).get('height'),'Internal height',optional=True)
        proposed=number(data['internal_cm']['height'],'Internal height',optional=True)
        confirmed=data.get('confirm_envelope_thickness_change') is True
        if proposed!=old or confirmed:
            if not confirmed:
                raise ValueError('Review and confirm replacing the retained legacy envelope thickness rule before saving.')
            thickness_history=PackagingEvidence(key='package-thickness-history:'+uuid.uuid4().hex,
                checked_at=datetime.utcnow(),value=dumps({'package_id':row.id,'version':row.version,
                    'internal_height_cm':previous.get('internal_cm',{}).get('height'),
                    'max_contents_thickness_mm':previous['max_contents_thickness_mm'],
                    'replacement_internal_height_cm':str(proposed) if proposed is not None else None}))
            merged['max_contents_thickness_mm']=None
    spec={**previous,**clean(merged)}
    if row:
        # Compare-and-swap prevents concurrent tabs from silently losing edits.
        changed=db.session.query(Package).filter_by(id=row.id,version=version).update({
            Package.name:name,Package.specification:dumps(spec),Package.version:version+1,Package.updated_at:datetime.utcnow()},synchronize_session=False)
        if not changed: raise ValueError('Package changed in another session. Reload before saving.')
    else:
        row=Package(id=str(uuid.uuid4()),name=name,specification=dumps(spec))
        db.session.add(row)
    if thickness_history is not None: db.session.add(thickness_history)
    if commit: db.session.commit();db.session.expire_all()
    else: db.session.flush();db.session.refresh(row)
    return row
