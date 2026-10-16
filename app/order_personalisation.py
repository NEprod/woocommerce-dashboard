"""Parse the existing storefront schema, never today's mutable catalogue template."""
import html
import json
import math
import re

TYPES = {'text','textarea','number','dropdown','radio','checkbox','checkbox_group','date','colour'}
IDENTIFIER = re.compile(r'[a-z][a-z0-9_]{0,63}')
REVISION = re.compile(r'[a-f0-9]{64}')


def parse(properties, product_id=None):
    warnings, fields = [], []
    private = {}
    public = []
    for prop in properties:
        key, value = prop['key'], prop['value']
        if key in {'_tlc_personalisation','_tlc_configuration'}:
            if key in private: warnings.append('Duplicate TLC snapshot property; review original properties.')
            private[key] = value
        elif not key.startswith('_'):
            public.append({'label':key, 'value':value})
    snapshot = None
    if '_tlc_personalisation' in private:
        try:
            snapshot = json.loads(private['_tlc_personalisation'])
            if not isinstance(snapshot,dict) or snapshot.get('schema_version') != 1 or type(snapshot.get('schema_version')) is not int:
                raise ValueError()
            if not isinstance(snapshot.get('fields'),list) or len(snapshot['fields']) > 100:
                raise ValueError()
        except (ValueError,TypeError,RecursionError):
            snapshot = None
            warnings.append('TLC private snapshot is malformed or uses an unsupported schema; original properties retained.')
    elif public or private:
        warnings.append('No TLC private snapshot: use the original customer properties; stable field identities are unavailable.')
    revision = private.get('_tlc_configuration')
    if snapshot:
        if not isinstance(snapshot.get('revision'),str) or not REVISION.fullmatch(snapshot['revision']):
            warnings.append('Snapshot configuration revision is invalid.')
        if revision != snapshot.get('revision'):
            warnings.append('Configuration and answer revisions do not agree; review before production.')
        revision = snapshot.get('revision')
        expected = product_id.rsplit('/',1)[-1] if product_id else None
        if expected and snapshot.get('product_id') not in (expected,product_id):
            warnings.append('Snapshot product identity differs from the purchased line; review before production.')
        if snapshot.get('visible_encoding') == 'html_entities':
            public = [{'label':html.unescape(p['label']), 'value':html.unescape(p['value'])} for p in public]
        seen = set()
        for f in snapshot['fields']:
            if not isinstance(f,dict):
                warnings.append('An invalid field snapshot was retained in original properties.'); continue
            ident, kind, value = f.get('id'), f.get('type'), f.get('value')
            valid = (isinstance(ident,str) and IDENTIFIER.fullmatch(ident) and ident not in seen
                     and isinstance(kind,str) and kind in TYPES and isinstance(f.get('label'),str) and bool(f['label'].strip()))
            valid = valid and ((type(value) is int or type(value) is float and math.isfinite(value)) if kind=='number'
                else type(value) is bool if kind=='checkbox'
                else isinstance(value,list) and all(isinstance(v,str) for v in value) if kind=='checkbox_group'
                else isinstance(value,str))
            choices = f.get('choice_ids',[])
            # Legacy authored choices use their string as stable identity; they
            # need not match the stricter *field* ID convention.
            valid = valid and isinstance(choices,list) and all(isinstance(c,str) and 0<len(c)<=256 for c in choices)
            if not valid:
                warnings.append('A field has inconsistent identity/type data; original customer answers remain available.'); continue
            seen.add(ident)
            display = f.get('display')
            if not isinstance(display,(str,int,float,bool)) or isinstance(display,float) and not math.isfinite(display):
                display = ', '.join(value) if isinstance(value,list) else 'Yes' if value is True else 'No' if value is False else str(value)
            fields.append({'id':ident,'label':f['label'],'type':kind,'value':value,'display':str(display),
                           'choice_ids':choices,'property_key':f.get('property_key')})
            # Preserve/flag public answers not represented by valid typed fields.
            # Do not silently hide additional or contradictory customer input.
            if isinstance(f.get('property_key'),str):
                key=html.unescape(f['property_key']) if snapshot.get('visible_encoding')=='html_entities' else f['property_key']
                matching=[p for p in public if p['label']==key]
                if not matching or any(p['value']!=str(display) for p in matching):
                    warnings.append('Customer-visible and typed answers differ; review original customer properties.')
        keys={html.unescape(f['property_key']) if snapshot.get('visible_encoding')=='html_entities' else f['property_key']
              for f in fields if isinstance(f.get('property_key'),str)}
        if keys and any(p['label'] not in keys for p in public):
            warnings.append('Additional customer-visible properties are retained below; review before production.')
    return {'schema_version':1,'revision':revision,'fields':fields,'public':public,
            'warnings':list(dict.fromkeys(warnings)), 'has_personalisation':bool(public or private)}
