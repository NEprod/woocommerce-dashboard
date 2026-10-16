"""One unit-safe authored packaging contract; never infers packed size from artwork."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

AXES = ('length','width','height')
BOOLS = ('measured','fragile','may_share','ship_separately','may_stack','rollable')
NUMBERS = ('weight_g','clearance_cm','protection_weight_g')
PACKAGING_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    **{k:{'type':'boolean'} for k in BOOLS},
    **{k:{'anyOf':[{'type':'number','minimum':0},{'type':'string','pattern':r'^(?:\d+(?:\.\d+)?|\.\d+)$'}]} for k in NUMBERS},
    'packed_dimensions_cm':{'type':'object','additionalProperties':False,'properties':{
        k:{'anyOf':[{'type':'number','exclusiveMinimum':0},{'type':'string','pattern':r'^(?:\d+(?:\.\d+)?|\.\d+)$'}]} for k in AXES}},
    'orientation':{'enum':['fixed','flat','any']},
    'classification':{'enum':['flat','rigid','general','rollable']},
    'stack_limit':{'type':'integer','minimum':1,'maximum':200},
    'requirements':{'type':'array','maxItems':20,'items':{'type':'string','maxLength':80},'uniqueItems':True},
    'selected_package_id':{'anyOf':[{'type':'string','format':'uuid'},{'type':'null'}]},
    'override_reason':{'type':'string','maxLength':500},
}}


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
        if isinstance(value,dict) and isinstance(result.get(key),dict): result[key]=merge(result[key],value)
        else: result[key]=deepcopy(value)
    return result


def validate(value):
    from jsonschema import Draft202012Validator, FormatChecker
    errors=list(Draft202012Validator(PACKAGING_SCHEMA,format_checker=FormatChecker()).iter_errors(value))
    if errors: raise ValueError('Packaging: '+errors[0].message)
    for key in NUMBERS:
        if key in value: number(value[key],key,zero=True)
    for key,amount in value.get('packed_dimensions_cm',{}).items(): number(amount,key)


def effective(document, attributes=None):
    """Use the existing exact/longest modifier selection, not price calculations."""
    result=deepcopy(document.get('packaging') or {})
    if attributes is not None:
        from app.utils.json_utils import json_key
        modifiers=document.get('variation_modifiers',{})
        mod=modifiers.get(json_key(attributes))
        if mod is None:
            matches=[]
            for key,value in modifiers.items():
                try: parts=dict(part.split('=') for part in key.split('|'))
                except ValueError: continue
                if all(attributes.get(k)==v for k,v in parts.items()): matches.append((len(parts),value))
            if matches: mod=max(enumerate(matches),key=lambda pair:(pair[1][0],-pair[0]))[1][1]
        if mod: result=merge(result,mod.get('packaging'))
    validate(result)
    return result
