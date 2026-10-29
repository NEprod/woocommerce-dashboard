"""Explicit public allowlist. Neither source documents nor customer answers are published."""
from copy import deepcopy
import hashlib
import json

from app.customisations import CustomisationDefinitionError, TYPES, ordered_fields, validate

VERSION = 1
FIELD_KEYS = frozenset(('label', 'required', 'order', 'help_text', 'placeholder',
    'min_length', 'max_length', 'minimum', 'maximum', 'whole_number', 'min_date',
    'max_date', 'min_selections', 'max_selections', 'default'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def public_configuration(definition):
    """Resolved template/inheritance input only. Unknown and operational keys never cross."""
    definition = definition or {}
    validate(definition, resolved=True)
    enabled = definition.get('storefront_enabled', False)
    result = {'schema_version': VERSION, 'enabled': enabled, 'fields': []}
    if enabled:
        for key, field in ordered_fields(definition):
            if not field.get('enabled', True) or field.get('remove'):
                continue
            kind = TYPES[field['type']]
            if kind == 'file_upload':
                raise CustomisationDefinitionError('Customer file/photo uploads require a verified secure upload service. Disable the upload field at its owner before reviewing storefront publication.')
            public = {k: deepcopy(v) for k, v in field.items() if k in FIELD_KEYS}
            public.update(id=key, type=kind)
            if 'choices' in field:
                public['choices'] = [({'id': c, 'label': c, **({'value': c} if kind == 'colour' else {})}
                    if isinstance(c, str) else {k: c[k] for k in ('id', 'label', 'value') if k in c})
                    for c in field['choices']]
            result['fields'].append(public)
        if not result['fields']:
            raise CustomisationDefinitionError('Storefront customisations are enabled but no supported enabled fields remain. Add an enabled supported field, or disable storefront customisations at their owning source.')
    result['revision'] = hashlib.sha256(canonical(result).encode()).hexdigest()
    return result
