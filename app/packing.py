"""Bounded deterministic rectangular packing, with full support and no bending.

Largest-face-first guillotine free cuboids. Each placement splits a free cuboid
into disjoint right/front/top spaces. A top space is limited to the supported
item footprint and is only available when package and item allow stacking.
Three bounded package-selection heuristics produce explainable feasible plans;
this is not a global optimum solver. Unknown geometry never proves fit.
"""
from copy import deepcopy
from decimal import Decimal
from itertools import permutations
from app.packaging_contract import AXES, number
from app.packaging_catalogue import readiness

MAX_ITEMS=200
MAX_PACKAGES=64
MAX_STEPS=60000


def shape(value):
    return tuple(number(value.get(k),k) for k in AXES)


def orientations(item):
    dims=item['size']
    if item.get('orientation')=='any': return sorted(set(permutations(dims)),reverse=True)
    if item.get('orientation')=='flat': return list(dict.fromkeys((dims,(dims[1],dims[0],dims[2]))))
    return [dims]


def postal_category(dimensions, weight):
    """Conservative format hints, not a service contract or postage quote."""
    a,b,c=sorted((Decimal(str(v)) for v in dimensions),reverse=True)
    weight=Decimal(str(weight))
    if a<=35.3 and b<=25 and c<=2.5 and weight<=750: return 'large_letter'
    if a<=45 and b<=35 and c<=16 and weight<=2000: return 'small_parcel'
    if a<=61 and b<=46 and c<=46 and weight<=20000: return 'medium_parcel'
    return 'outside_configured_formats'


def _eligible(item,package):
    if item.get('classification')=='rollable': return 'Rolled-item packing requires special handling; no automatic rolling.'
    if package['type']=='SOFT_PACK': return 'Soft packaging deformation is not modelled; use measured rigid packaging.'
    if package['type']=='ENVELOPE' and item.get('classification')!='flat': return 'Envelope requires explicitly flat contents.'
    if item.get('classification')=='flat' and not package.get('flat'): return 'Package is not approved for flat/rigid protection.'
    if item.get('fragile') and not package.get('fragile'): return 'Package lacks fragile-product approval.'
    if not set(item.get('requirements',[]))<=set(package.get('protection',[])): return 'Required protective materials are missing.'
    return None


def _fill(package,items,budget):
    usable=shape(package['internal_cm'])
    pad=number(package.get('clearance_cm'), 'Package clearance',zero=True)
    usable=tuple(v-2*pad for v in usable)
    if min(usable)<=0: return None
    free=[((Decimal(0),)*3,usable,0)]
    placed=[];mass=Decimal(0)
    tare=number(package['tare_g'],'tare',zero=True)
    protection=number(package['protection_weight_g'],'protection',zero=True)
    reasons={}
    for item in items:
        why=_eligible(item,package)
        if why: reasons[item['key']]=why;continue
        if placed and (not item.get('may_share',False) or item.get('ship_separately',False) or
                       any(not p['may_share'] or p['separate'] for p in placed)):
            reasons[item['key']]='Must ship separately / sharing not authorised.';continue
        if package.get('max_quantity') and len(placed)>=package['max_quantity']: continue
        added=item['mass']+item['protection_mass']
        if mass+added+protection>number(package['max_contents_g'],'contents limit'): continue
        if package.get('max_gross_g') and tare+protection+mass+added>number(package['max_gross_g'],'gross limit'): continue
        chosen=None
        clearance=item['clearance']
        for fi,(origin,space,level) in enumerate(free):
            if level and (not item.get('may_stack',False) or level>=item.get('stack_limit',1)
                or level>=min((p['stack_limit'] for p in placed),default=1)): continue
            for orient in orientations(item):
                budget[0]+=1
                if budget[0]>MAX_STEPS: raise ValueError('Packing search limit reached. Split the basket for controlled analysis; no automatic quote.')
                occupied=tuple(v+2*clearance for v in orient)
                if all(occupied[i]<=space[i] for i in range(3)):
                    chosen=fi,origin,space,level,occupied;break
            if chosen: break
        if chosen is None:
            reasons[item['key']]='Packed item/combined thickness exceeds usable geometry or stacking limit.';continue
        fi,origin,space,level,occupied=chosen
        free.pop(fi)
        x,y,z=origin;l,w,h=occupied;L,W,H=space
        # All resulting spaces are disjoint. Top is wholly supported, never an
        # unsupported bridge or arbitrary volume subtraction.
        if L>l: free.append(((x+l,y,z),(L-l,W,H),level))
        if W>w: free.append(((x,y+w,z),(l,W-w,H),level))
        if H>h and item.get('may_stack',False) and not item.get('fragile') and package.get('stackable'):
            free.append(((x,y,z+h),(l,w,H-h),level+1))
        free.sort(key=lambda f:(f[0][2],f[0][1],f[0][0],f[1]))
        placed.append({'key':item['key'],'identity':item['identity'],'sku':item.get('sku'),
            'title':item.get('title'),'position_cm':[str(v) for v in origin],
            'occupied_cm':[str(v) for v in occupied],'level':level,'may_share':item.get('may_share',False),
            'separate':item.get('ship_separately',False),'stack_limit':item.get('stack_limit',1)})
        mass+=added
    if not placed:return None
    gross=tare+protection+mass
    ext=shape(package['external_cm'])
    return {'package_id':package['id'],'package_version':package.get('version'),
        'package_name':package['name'],'package_snapshot':{k:deepcopy(v) for k,v in package.items() if k!='links'},
        'items':placed,'gross_weight_g':str(gross),'contents_weight_g':str(mass),
        'external_cm':dict(zip(AXES,map(str,ext))),'postal_category':postal_category(ext,gross),
        'material_cost_pence':package.get('cost_pence'),'fit':'verified' if readiness(package)[0]=='Ready' else 'estimated',
        'reasons':reasons}


def plan(lines,packages,*,allow_estimated=False,rate_configuration=None,destination='GB',at=None):
    """Pure, no DB/network. Every line carries its own durable local/order identity."""
    if not isinstance(lines,list) or len(lines)>MAX_ITEMS: raise ValueError('Use at most 200 basket lines.')
    if len(packages)>MAX_PACKAGES: raise ValueError('At most 64 package candidates can be evaluated.')
    units=[];issues=[];ignored=[]
    for index,line in enumerate(lines):
        qty=line.get('quantity',1)
        if type(qty) is not int or not 1<=qty<=MAX_ITEMS: raise ValueError('Quantity must be 1–200.')
        if line.get('requires_shipping') is False: ignored.append(line.get('identity',str(index)));continue
        if line.get('requires_shipping') is not True: issues.append(f'{line.get("sku") or index}: requires-shipping intent is unspecified.');continue
        try:
            item=deepcopy(line)
            item['size']=shape(item.get('packed_dimensions_cm',{}))
            item['mass']=number(item.get('weight_g'),'Item weight')
            item['clearance']=number(item.get('clearance_cm'),'Item protective clearance',zero=True)
            item['protection_mass']=number(item.get('protection_weight_g'),'Item protection weight',zero=True)
            if item.get('orientation') not in {'flat','fixed','any'}: raise ValueError('Orientation must be explicitly set.')
            if item.get('classification') not in {'flat','rigid','general','rollable'}: raise ValueError('Product classification must be explicitly set.')
            if not item.get('measured') and not allow_estimated: raise ValueError('Packed dimensions require measurement confirmation.')
        except ValueError as exc:issues.append(f'{line.get("sku") or index}: {exc}');continue
        for n in range(qty):
            units.append(dict(item,key=f'{index}:{n}',identity=str(item.get('identity',index)),stack_limit=item.get('stack_limit',1)))
    if len(units)>MAX_ITEMS: raise ValueError('Maximum 200 physical units per simulation; split a larger basket.')
    if issues: return {'schema_version':1,'state':'insufficient_information','warnings':issues,'parcels':[],'quotes':[],'ignored_nonphysical':ignored}
    if not units:return {'schema_version':1,'state':'no_shipping','warnings':[],'parcels':[],'quotes':[],'ignored_nonphysical':ignored}
    candidates=[];rejected=[]
    for package in sorted(packages,key=lambda p:p['id']):
        state,warnings=readiness(package)
        if package.get('archived') or state=='Manual only' or state=='Measurements needed' or state=='Estimated fit' and not allow_estimated:
            rejected.append({'package_id':package['id'],'name':package['name'],'reason':'; '.join(warnings) or state});continue
        candidates.append(package)
    units.sort(key=lambda i:(-max(i['size'][0],i['size'][1]),-min(i['size'][0],i['size'][1]),-i['size'][2],i['key']))
    plans=[];budget=[0]
    for strategy in ('consolidate','materials','smallest'):
        remaining=units[:];parcels=[]
        while remaining:
            options=[]
            for package in candidates:
                # A selected solo package is a constraint, not an automatic
                # combined-basket fit assertion. Never silently replace it.
                allowed=[i for i in remaining if not i.get('selected_package_id') or i['selected_package_id']==package['id']]
                parcel=_fill(package,allowed,budget)
                if parcel: options.append(parcel)
            if not options:break
            def score(p):
                volume=Decimal(1)
                for v in p['external_cm'].values():volume*=Decimal(v)
                cost=p['material_cost_pence'] if p['material_cost_pence'] is not None else 10**9
                if strategy=='materials': return (Decimal(cost)/len(p['items']),-len(p['items']),volume,p['package_id'])
                if strategy=='smallest': return (volume/len(p['items']),-len(p['items']),cost,p['package_id'])
                return (-len(p['items']),cost,volume,p['package_id'])
            chosen=min(options,key=score);parcels.append(chosen)
            packed={i['key'] for i in chosen['items']};remaining=[i for i in remaining if i['key'] not in packed]
        if not remaining:
            from app.shipping_rates import quote
            state='verified' if all(p['fit']=='verified' for p in parcels) and all(i.get('measured') for i in units) else 'estimated'
            quotes=quote(parcels,rate_configuration,destination=destination,at=at) if state=='verified' else []
            plans.append({'schema_version':1,'state':state,'parcels':parcels,'quotes':quotes,'warnings':[],
                'ignored_nonphysical':ignored,'rejected_packages':rejected,'strategy':strategy})
    if not plans:
        reasons=rejected[:]
        for package in candidates:
            reasons.append({'package_id':package['id'],'name':package['name'],
                'reason':'No complete basket arrangement fits its usable dimensions, approved protection, sharing and weight limits.'})
        return {'schema_version':1,'state':'no_suitable_package','warnings':['No safe complete packing plan. Measure suitable packaging or resolve special handling; no postage quoted.'],
            'parcels':[],'quotes':[],'rejected_packages':reasons,'search_steps':budget[0]}
    def rank(p):
        valid=[q['total_pence'] for q in p['quotes'] if q['available']]
        cost=sum(x['material_cost_pence'] or 0 for x in p['parcels'])
        return (p['state']!='verified',not bool(valid),min(valid) if valid else 10**12,len(p['parcels']),cost,p['strategy'])
    plans.sort(key=rank);result=plans[0]
    result['search_steps']=budget[0]
    result['alternatives']=[{'parcels':p['parcels'],'quotes':p['quotes'],'strategy':p['strategy']} for p in plans[1:3]]
    if any(p['material_cost_pence'] is None for p in result['parcels']):result['warnings'].append('Some packaging costs are unknown; cost ranking is incomplete.')
    return result
