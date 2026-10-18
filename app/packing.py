"""Basic, advisory solo size checks only; no protection, basket or postage engine."""
from decimal import Decimal
from itertools import permutations
from app.packaging_contract import AXES, number, packed_cm
from app.packaging_catalogue import usable_bounds


def recommend(item, packages):
    """Use independently authored packed size; never assign a package."""
    result={'state':'no_suitable_package','recommendation':None,'alternatives':[],
        'rejected_packages':[],'warnings':[],
        'notice':'No suitable package found. You can still save this product and select a package manually with an unverified fit.'}
    try:
        size=packed_cm(item)
        dims=tuple(number(size.get(k),k) for k in AXES)
    except ValueError as error:
        result.update(state='insufficient_information',warnings=[str(error)])
        return result
    fits=[]
    for package in sorted(packages,key=lambda p:(p['name'],p['id'])):
        if package.get('archived'): continue
        bounds,warnings=usable_bounds(package)
        reason=None
        limit=package.get('max_contents_thickness_mm')
        if limit is not None and dims[2]*10>Decimal(limit):
            reason=f'Packed thickness exceeds {limit} mm contents limit.'
        if any(v is None for v in bounds):
            reason=reason or 'Package size is incomplete; unknown measurements are not zero. Manual selection has unverified fit.'
        elif package['type']=='TUBE':
            if item.get('method')!='rolled':
                reason=reason or 'A tube requires an explicitly prepared rolled item; flat items are not automatically rolled.'
            elif dims[1]!=dims[2]:
                reason=reason or 'A cylindrical roll needs equal width and height diameters.'
            elif dims[0]>bounds[0] or dims[1]>bounds[1]:
                reason=reason or 'Prepared roll exceeds the stated tube length or diameter.'
            volume=bounds[0]*bounds[1]**2
        else:
            orientations=[dims]
            if item.get('orientation','flat')=='flat': orientations.append((dims[1],dims[0],dims[2]))
            if item.get('orientation')=='any': orientations=sorted(set(permutations(dims)))
            if not any(all(a<=b for a,b in zip(position,bounds)) for position in orientations):
                reason=reason or 'Prepared item exceeds the stated size bounds.'
            volume=bounds[0]*bounds[1]*bounds[2]
        if reason:
            result['rejected_packages'].append({'id':package['id'],'name':package['name'],'reason':reason})
            continue
        weight=number(item.get('weight_g'),'Shipping weight',zero=True,optional=True)
        tare=number(package.get('tare_g'),'Empty package weight',zero=True,optional=True)
        gross=weight+tare if weight is not None and tare is not None else None
        estimated=bool(warnings)
        fit={'package_id':package['id'],'name':package['name'],
            'fit':'Estimated Fit (unverified)' if estimated else 'Internal size check',
            'gross_weight_g':format(gross,'f') if gross is not None else None,'warnings':warnings,
            'reason':'Basic size comparison only, not physical fit, protection or carrier approval. Check the actual package before use.'}
        fits.append(((estimated,volume,package['name'],package['id']),fit))
    if fits:
        ordered=[f for _,f in sorted(fits)]
        result.update(state='estimated' if ordered[0]['fit']=='Estimated Fit (unverified)' else 'size_checked',
            recommendation=ordered[0],alternatives=ordered[1:3],warnings=ordered[0]['warnings'],
            notice='Advisory solo size check only. Accept, choose another package (unverified fit), or leave unassigned. Shopify checkout is unchanged.')
    return result
