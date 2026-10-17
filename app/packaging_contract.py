"""One unit-safe authored packaging contract; never infers packed size from artwork."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

AXES = ('length','width','height')
BOOLS = ('measured','fragile','rollable')
NUMBERS = ('weight_g','clearance_cm','protection_weight_g')
PACKAGING_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    **{k:{'type':'boolean'} for k in BOOLS},
    **{k:{'anyOf':[{'type':'number','minimum':0},{'type':'string','pattern':r'^(?:\d+(?:\.\d+)?|\.\d+)$'}]} for k in NUMBERS},
    'packed_dimensions_cm':{'type':'object','additionalProperties':False,'properties':{
        k:{'anyOf':[{'type':'number','exclusiveMinimum':0},{'type':'string','pattern':r'^(?:\d+(?:\.\d+)?|\.\d+)$'}]} for k in AXES}},
    'packed_dimensions':{'type':'object','additionalProperties':False,'properties':{
        **{k:{'anyOf':[{'type':'number','exclusiveMinimum':0},{'type':'string','pattern':r'^(?:\d+(?:\.\d+)?|\.\d+)$'}]} for k in AXES},
        'unit':{'enum':['mm','cm','in']}},'required':['unit']},
    'orientation':{'enum':['fixed','flat','any']},
    'classification':{'enum':['flat','rigid','general','rollable']},
    'method':{'enum':['flat','rolled','rigid','protected']},
    'assignment_kind':{'enum':['accepted','manual']},
    'requirements':{'type':'array','maxItems':20,'items':{'type':'string','maxLength':80},'uniqueItems':True},
    'selected_package_id':{'anyOf':[{'type':'string','format':'uuid'},{'type':'null'}]},
    'override_reason':{'type':'string','maxLength':500},
}}

# Original `dimensions` is the finished product in millimetres. Retain the
# optional explicit-unit alias introduced in0017 without rewriting either key.
FINISHED_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    **{k:PACKAGING_SCHEMA['properties']['packed_dimensions_cm']['properties'][k] for k in AXES},
    'unit':{'enum':['mm','cm','in']}, 'label':{'type':'string','maxLength':100}}}


def finished(document, attributes=None):
    from app.utils.json_utils import apply_variation_modifiers
    original = apply_variation_modifiers(document, attributes, log=lambda *a,**k:None)['dimensions'] if attributes is not None else document.get('dimensions', {})
    value = deepcopy(document.get('finished_dimensions', {}))
    if attributes is not None and original != document.get('dimensions',{}):
        value={}  # original size-changing modifier beats a parent-only alias
    modifier = selected_modifier(document, attributes, field='finished_dimensions')
    if modifier and 'finished_dimensions' in modifier:
        value = deepcopy(modifier['finished_dimensions'])
    from jsonschema import Draft202012Validator
    errors = list(Draft202012Validator(FINISHED_SCHEMA).iter_errors(value))
    if errors: raise ValueError('Finished dimensions: '+errors[0].message)
    if value:
        if 'unit' not in value: raise ValueError('Finished dimensions require an explicit unit.')
        for key in AXES:
            if key in value: number(value[key], key)
    if value: return value
    # Historical empty/missing axes stay absent, never become zero.
    original = {k:v for k,v in (original or {}).items() if k in AXES and v not in (None,'')}
    return dict(original,unit='mm') if original else {}


def finished_mm(document, attributes=None):
    value = finished(document, attributes)
    if not value: return None
    factor = {'mm':Decimal(1),'cm':Decimal(10),'in':Decimal('25.4')}[value['unit']]
    return {k:format(number(value[k],k,zero=True)*factor,'f') if k in value else None for k in AXES}


def selected_modifier(document, attributes=None, *, field=None):
    if attributes is None: return None
    from app.utils.json_utils import json_key
    modifiers={k:v for k,v in document.get('variation_modifiers',{}).items() if field is None or field in v}
    mod=modifiers.get(json_key(attributes))
    if mod is not None: return mod
    matches=[]
    for key,value in modifiers.items():
        try: parts=dict(part.split('=') for part in key.split('|'))
        except ValueError: continue
        if all(attributes.get(k)==v for k,v in parts.items()): matches.append((len(parts),value))
    return max(enumerate(matches),key=lambda pair:(pair[1][0],-pair[0]))[1][1] if matches else None


def number(value, name, *, zero=False, optional=False):
    if optional and value in (None,''): return None
    try:
        if isinstance(value,bool): raise ValueError()
        result=Decimal(str(value))
        if not result.is_finite() or result < 0 or (not zero and result==0) or result>1000000: raise ValueError()
        return result
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError(f'{name}: enter a finite {"non-negative" if zero else "positive"} number.') from None


def merge(base, patch):
    result=deepcopy(base or {})
    for key,value in (patch or {}).items():
        if key=='packed_dimensions':
            result.pop('packed_dimensions_cm',None)
            result[key]=deepcopy(value)  # units and axes form one authored value
        elif key=='packed_dimensions_cm':
            result.pop('packed_dimensions',None)
            result[key]={**result.get(key,{}),**deepcopy(value)}
        elif isinstance(value,dict) and isinstance(result.get(key),dict): result[key]=merge(result[key],value)
        else: result[key]=deepcopy(value)
    return result


def validate(value):
    from jsonschema import Draft202012Validator, FormatChecker
    errors=list(Draft202012Validator(PACKAGING_SCHEMA,format_checker=FormatChecker()).iter_errors(value))
    if errors: raise ValueError('Packaging: '+errors[0].message)
    if 'packed_dimensions' in value and 'packed_dimensions_cm' in value:
        raise ValueError('Packaging: author one packed size, not both unit-explicit and centimetre forms.')
    for key in NUMBERS:
        if key in value: number(value[key],key,zero=True)
    for key,amount in value.get('packed_dimensions_cm',{}).items(): number(amount,key)
    for key,amount in value.get('packed_dimensions',{}).items():
        if key in AXES: number(amount,key)


def packed_cm(value):
    """Canonical shipping geometry only. Never consult finished dimensions."""
    validate(value)
    if 'packed_dimensions' not in value: return deepcopy(value.get('packed_dimensions_cm',{}))
    size=value['packed_dimensions']
    factor={'mm':Decimal('.1'),'cm':Decimal(1),'in':Decimal('2.54')}[size['unit']]
    return {k:format(number(size[k],k)*factor,'f') for k in AXES if k in size}


def effective(document, attributes=None):
    """Sparse shipping rules inherit broad→specific, independently of pricing."""
    result=deepcopy(document.get('packaging') or {})
    matches=[]
    if attributes is not None:
        for index,(key,mod) in enumerate(document.get('variation_modifiers',{}).items()):
            if 'packaging' not in mod: continue
            try: parts=dict(part.split('=') for part in key.split('|'))
            except ValueError: continue
            if all(attributes.get(k)==v for k,v in parts.items()): matches.append((len(parts),-index,mod['packaging']))
    for _,_,patch in sorted(matches,key=lambda m:m[:2]): result=merge(result,patch)
    validate(result)
    return result


def dimension_configurations(document):
    """Group the existing generator's configurations by finished geometry.

    Shared option identities, not new variant IDs, name each group. Colour-only
    combinations share one size; exceptions remain ordinary modifier overrides.
    """
    from app.utils.scanner import build_variations
    from app.utils.json_utils import json_key
    from app.taxonomy_assignments import variation_drivers
    drivers=[] if document.get('collection_type')=='Simple' else variation_drivers(document)
    count=1
    for name in drivers:
        count*=len(document.get('attributes',{}).get(name,[]))
        if count>256: raise ValueError('Review at most256 existing size combinations at a time.')
    selections=list(build_variations(document)) if drivers else [{}]
    groups={}; all_selections=[]
    for attrs in selections:
        size=finished(document,attrs)
        canonical=finished_mm(document,attrs)
        if not canonical: continue
        if any(canonical[k] is None or Decimal(canonical[k])<=0 for k in AXES):
            raise ValueError('Complete positive finished length, width and thickness are required for a safe copy; missing measurements are not zero.')
        signature=tuple(str(Decimal(str(canonical[k])).normalize()) if canonical[k] is not None else None for k in AXES)
        all_selections.append((attrs,signature))
        group=groups.setdefault(signature,{'finished':size,'selections':[]})
        group['selections'].append(attrs)
    result=[]
    for signature,group in groups.items():
        selections=group['selections']
        common={k:v for k,v in selections[0].items() if all(a.get(k)==v for a in selections)} if selections else {}
        common={k:v for k,v in common.items() if any(a.get(k)!=v for a,_ in all_selections)}
        value=group['finished']
        # A non-factorable geometry group (e.g. crossed size/design exceptions)
        # cannot safely become a broad rule that also matches another size.
        partitions=[[a] for a in selections] if any(other!=signature and all(attrs.get(k)==v for k,v in common.items()) for attrs,other in all_selections) else [selections]
        for selected in partitions:
            key_attrs=selected[0] if len(partitions)>1 else common
            result.append({'key':json_key(key_attrs) if key_attrs else '', 'attributes':key_attrs,
                'finished':value,'selections':selected,
                'copy':{k:value[k] for k in (*AXES,'unit') if k in value},
                'packed':effective(document,selected[0])})
    return result
