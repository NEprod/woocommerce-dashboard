"""Conservative single-item fit only. No basket, parcel splitting or postage engine."""
from decimal import Decimal
from itertools import permutations
from app.packaging_contract import AXES, number
from app.packaging_catalogue import readiness


def recommend(item, packages):
    """Return advisory fits. A selection is never changed or required."""
    result={'state':'no_suitable_package','recommendation':None,'alternatives':[],
        'rejected_packages':[],'warnings':[],
        'notice':'No suitable package found. You can still save this product without assigning a package.'}
    try:
        dims=tuple(number(item.get('packed_dimensions_cm',{}).get(k),k) for k in AXES)
        weight=number(item.get('weight_g'),'Shipping weight',zero=True)
        clearance=number(item.get('clearance_cm'),'Product protection allowance',zero=True)
        protection=number(item.get('protection_weight_g'),'Product protection weight',zero=True)
        if item.get('method') not in {'flat','rolled','rigid','protected'}: raise ValueError('Choose the prepared packing method.')
        if item.get('method')=='rolled' and item.get('rollable') is not True: raise ValueError('Rolling requires explicit permission and measured prepared roll dimensions.')
        if item.get('measured') is not True: raise ValueError('Confirm the prepared packed dimensions are measured.')
        if item.get('orientation') not in {'fixed','flat','any'}: raise ValueError('Set permitted orientation.')
        if type(item.get('fragile')) is not bool: raise ValueError('Confirm whether the product is fragile.')
    except ValueError as error:
        result.update(state='insufficient_information',warnings=[str(error)])
        return result
    fits=[]
    for package in sorted(packages,key=lambda p:(p['name'],p['id'])):
        if package.get('archived'): continue
        reason=None
        ready,missing=readiness(package)
        if ready!='Ready': reason='; '.join(missing) or ready
        elif package['type']=='SOFT_PACK': reason='Soft-pack deformation requires manual verification.'
        elif item['fragile'] and not package['fragile']: reason='Not approved for fragile contents.'
        elif item['method'] in {'flat','rigid'} and not package['flat']: reason='Not approved for flat/rigid protection.'
        elif not set(item.get('requirements',[])).issubset(package['protection']): reason='Required protective materials are not approved.'
        if not reason and package.get('max_contents_thickness_mm') is not None and dims[2]*10>Decimal(package['max_contents_thickness_mm']):
            reason=f'Packed thickness exceeds {package["max_contents_thickness_mm"]} mm contents limit.'
        if not reason:
            gap=clearance+Decimal(package['clearance_cm'])
            padded=tuple(d+2*gap for d in dims)
            if package['type']=='TUBE':
                if item['method']!='rolled' or not package['rolled']: reason='Only explicitly rolled, measured items may use a tube.'
                elif dims[1]!=dims[2]: reason='Confirm a circular prepared roll with equal measured width/height diameters; an arbitrary rectangle is not proven to fit a tube.'
                else:
                    # length is the roll axis; width/height bound its measured
                    # circular diameter. Never derive it from a flat print.
                    usable=Decimal(package['internal_cm']['length'])-Decimal(package['end_cap_allowance_cm'])
                    if padded[0]>usable or max(padded[1:])>Decimal(package['internal_diameter_cm']): reason='Measured roll exceeds usable tube length/diameter including end-cap and protection allowances.'
                    volume=Decimal(package['external_cm']['length'])*Decimal(package['external_diameter_cm'])**2
            else:
                if item['method']=='rolled' and not package['rolled']: reason='Not approved for rolled contents.'
                internal=tuple(Decimal(package['internal_cm'][k]) for k in AXES)
                orientations=[padded]
                if item['orientation']=='flat': orientations.append((padded[1],padded[0],padded[2]))
                if item['orientation']=='any': orientations=sorted(set(permutations(padded)))
                if not any(all(a<=b for a,b in zip(position,internal)) for position in orientations): reason='Prepared item and protection exceed verified usable dimensions.'
                volume=Decimal(package['external_cm']['length'])*Decimal(package['external_cm']['width'])*Decimal(package['external_cm']['height'])
            contents=weight+protection+Decimal(package['protection_weight_g'])
            gross=contents+Decimal(package['tare_g'])
            if contents>Decimal(package['max_contents_g']): reason='Contents exceed safe weight capacity.'
            if package.get('max_gross_g') and gross>Decimal(package['max_gross_g']): reason='Gross weight exceeds package capacity.'
        if reason:
            result['rejected_packages'].append({'id':package['id'],'name':package['name'],'reason':reason})
        else:
            fit={'package_id':package['id'],'name':package['name'],'fit':'Measured',
                'gross_weight_g':format(gross,'f'),'reason':'Measured prepared item and protection fit verified usable space and weight limits.'}
            fits.append(((volume,package.get('cost_pence') is None,package.get('cost_pence') or 0,package['name'],package['id']),fit))
    if fits:
        ordered=[f for _,f in sorted(fits)]
        result.update(state='verified',recommendation=ordered[0],alternatives=ordered[1:3],
            notice='Advisory solo recommendation only. Use Recommendation to accept, choose another package, or leave unassigned. Shopify checkout is unchanged.')
    return result
