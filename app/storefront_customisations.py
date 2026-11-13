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


def public_configuration(definition, *, photo_upload=None):
    """Resolved template/inheritance input only. Unknown and operational keys never cross."""
    definition = definition or {}
    validate(definition, resolved=True)
    enabled = definition.get('storefront_enabled', False)
    result = {'schema_version': VERSION, 'enabled': enabled, 'fields': []}
    if enabled:
        # Existing local policy, projected through the same reviewed metafield.
        # Omit "none" so unchanged legacy configurations need no blanket rewrite.
        if definition.get('proof_policy', 'none') != 'none':
            result['proof_policy'] = definition['proof_policy']
        for key, field in ordered_fields(definition):
            if not field.get('enabled', True) or field.get('remove'):
                continue
            kind = TYPES[field['type']]
            if kind == 'file_upload':
                if not photo_upload:
                    raise CustomisationDefinitionError('Customer file/photo uploads require a configured secure upload service. Disable the upload field at its owner or configure private photo storage.')
                if field.get('enabled') is not True:
                    raise CustomisationDefinitionError('Explicitly enable this existing photo definition at its owner before publishing it.')
                if (field.get('allowed_media_categories',['image']) != ['image']
                        or not set(field.get('allowed_extensions',['.jpg','.jpeg','.png','.webp'])) <= {'.jpg','.jpeg','.png','.webp'}
                        or field.get('max_files',1) != 1 or field.get('max_file_size_bytes',10_000_000)>10_000_000):
                    raise CustomisationDefinitionError('Private photo uploads accept one JPEG, PNG or WebP up to 10 MB. Correct this field at its owner.')
                result['photo_upload'] = photo_upload
            public = {k: deepcopy(v) for k, v in field.items() if k in FIELD_KEYS}
            public.update(id=key, type=kind)
            if kind == 'file_upload':
                mimes = {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}
                public.update(max_bytes=field.get('max_file_size_bytes',10_000_000),
                              image_types=sorted({mimes[x] for x in field.get('allowed_extensions',mimes)}))
            if 'choices' in field:
                public['choices'] = [({'id': c, 'label': c, **({'value': c} if kind == 'colour' else {})}
                    if isinstance(c, str) else {k: c[k] for k in ('id', 'label', 'value') if k in c})
                    for c in field['choices']]
            result['fields'].append(public)
        if not result['fields'] and 'proof_policy' not in result:
            raise CustomisationDefinitionError('Storefront customisations are enabled but no supported enabled fields remain. Add an enabled supported field, or disable storefront customisations at their owning source.')
    result['revision'] = hashlib.sha256(canonical(result).encode()).hexdigest()
    return result
