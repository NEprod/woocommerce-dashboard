"""Local definition validation/resolution. Never customer answers or Shopify writes."""
from copy import deepcopy
from datetime import date
import json
import re

from jsonschema import Draft202012Validator

ID = r"^[a-z][a-z0-9_]{0,63}$"
TYPES = {"text": "text", "multiline": "textarea", "textarea": "textarea",
         "number": "number", "choice": "dropdown", "dropdown": "dropdown",
         "radio": "radio", "checkbox": "checkbox", "checkbox_group": "checkbox_group",
         "date": "date", "colour": "colour", "photo": "file_upload", "file_upload": "file_upload"}
CHOICE = {"oneOf": [{"type": "string", "minLength": 1, "maxLength": 191},
    {"type": "object", "required": ["id", "label"], "properties": {
        "id": {"type": "string", "pattern": ID}, "label": {"type": "string", "minLength": 1, "maxLength": 191},
        "value": {"type": "string", "maxLength": 191}}}]}
FIELD_SCHEMA = {"type": "object", "required": ["type", "label"], "properties": {
    "type": {"enum": list(TYPES)}, "label": {"type": "string", "minLength": 1, "maxLength": 191},
    "required": {"type": "boolean"}, "enabled": {"type": "boolean"},
    "order": {"type": "integer", "minimum": 0},
    "help_text": {"type": "string", "maxLength": 4000}, "placeholder": {"type": "string", "maxLength": 1000},
    "choices": {"type": "array", "minItems": 1, "uniqueItems": True, "items": CHOICE},
    "min_length": {"type": "integer", "minimum": 0}, "max_length": {"type": "integer", "minimum": 1},
    "minimum": {"type": "number"}, "maximum": {"type": "number"}, "whole_number": {"type": "boolean"},
    "min_date": {"type": "string"}, "max_date": {"type": "string"},
    "min_selections": {"type": "integer", "minimum": 0}, "max_selections": {"type": "integer", "minimum": 1},
    "allowed_media_categories": {"type": "array", "minItems": 1, "uniqueItems": True,
        "items": {"enum": ["image", "document"]}},
    "allowed_extensions": {"type": "array", "minItems": 1, "uniqueItems": True,
        "items": {"type": "string", "pattern": r"^\.[a-z0-9]{1,10}$"}},
    "max_file_size_bytes": {"type": "integer", "minimum": 1}, "max_files": {"type": "integer", "minimum": 1},
    "default": {}}}
SCHEMA = {"type": "object", "properties": {
    "storefront_enabled": {"type": "boolean"},
    "schema_version": {"enum": [1, 2]}, "proof_policy": {"enum": ["none", "required", "optional"]},
    "template": {"type": "object", "required": ["id", "revision"],
        "additionalProperties": False, "properties": {"id": {"type": "string", "pattern": ID},
        "revision": {"type": "integer", "minimum": 1}}},
    "fields": {"type": "object", "propertyNames": {"pattern": ID}, "additionalProperties": FIELD_SCHEMA}}}


def choices(field):
    return [item if isinstance(item, str) else item['id'] for item in field.get('choices', [])]


def ordered_fields(definition):
    return sorted(definition.get('fields', {}).items(), key=lambda item: (item[1].get('order', 0), item[0]))


class CustomisationDefinitionError(ValueError):
    """Invalid canonical definitions; source data is never repaired implicitly."""


def validate(value, *, product_override=False, resolved=False):
    # The existing document/request limit is the resource bound, not an arbitrary
    # business field-count cap. Reject nonfinite values, including in opaque keys.
    if len(json.dumps(value, allow_nan=False).encode()) > 1024 * 1024:
        raise CustomisationDefinitionError('customisation exceeds the 1 MiB definition limit.')
    if isinstance(value, dict):
        if product_override and 'template' in value:
            raise CustomisationDefinitionError('customisation.template is obsolete in Product Overrides. Remove this key manually; reusable templates belong to Shared Collection/owning JSON. Add only distinct customisation.fields.')
        if 'template' in value and value['template'] is None:
            raise CustomisationDefinitionError('customisation.template:null is obsolete. Remove the null template key manually; template exclusion is not supported.')
        fields = value.get('fields')
        for key, field in fields.items() if isinstance(fields, dict) else ():
            if not isinstance(key, str) or not re.fullmatch(ID, key):
                raise CustomisationDefinitionError('customisation.fields: field IDs must start with a lowercase letter and use only lowercase letters, digits and underscores (e.g. dogs_name), up to 64 characters. No hyphens or spaces.')
            if isinstance(field, dict) and 'remove' in field:
                raise CustomisationDefinitionError(f'customisation.fields.{key}.remove is obsolete. Remove this instruction manually; inherited fields cannot be excluded. No JSON was changed.')
    error = next(Draft202012Validator(SCHEMA).iter_errors(value), None)
    if error:
        raise CustomisationDefinitionError('customisation.' + '.'.join(map(str, error.path)) + ': ' + error.message)
    for key, field in value.get('fields', {}).items():
        prefix = f'customisation.fields.{key}: '
        kind = TYPES.get(field.get('type'))
        if not field.get('label', '').strip():
            raise CustomisationDefinitionError(prefix + 'a field needs a nonblank label.')
        for low, high in [('minimum', 'maximum'), ('min_length', 'max_length'),
                          ('min_selections', 'max_selections'), ('min_date', 'max_date')]:
            if low in field and high in field and field[low] > field[high]:
                raise CustomisationDefinitionError(prefix + low + ' exceeds ' + high + '.')
        for prop in ('min_date', 'max_date'):
            if prop in field:
                try:
                    if date.fromisoformat(field[prop]).isoformat() != field[prop]:
                        raise ValueError()
                except ValueError:
                    raise CustomisationDefinitionError(prefix + prop + ' must be YYYY-MM-DD.') from None
        allowed = {'min_length': {'text', 'textarea'}, 'max_length': {'text', 'textarea'},
            'minimum': {'number'}, 'maximum': {'number'}, 'whole_number': {'number'},
            'min_date': {'date'}, 'max_date': {'date'}, 'min_selections': {'checkbox_group'},
            'max_selections': {'checkbox_group'}, 'choices': {'dropdown', 'radio', 'checkbox_group', 'colour'},
            **{k: {'file_upload'} for k in ('allowed_media_categories', 'allowed_extensions', 'max_file_size_bytes', 'max_files')}}
        for prop, kinds in allowed.items():
            if kind and prop in field and kind not in kinds:
                raise CustomisationDefinitionError(prefix + prop + ' is unsupported for this input type.')
        ids = choices(field)
        if any(not (c if isinstance(c, str) else c['label']).strip() for c in field.get('choices', [])):
            raise CustomisationDefinitionError(prefix + 'choice labels must not be blank.')
        if len(set(ids)) != len(ids):
            raise CustomisationDefinitionError(prefix + 'choice identities must be unique.')
        if field.get('enabled', True) and kind in {'dropdown', 'radio', 'checkbox_group'} and not ids:
            raise CustomisationDefinitionError(prefix + 'a choice field needs choices.')
        if kind == 'checkbox_group' and ids and field.get('min_selections', 0) > len(ids):
            raise CustomisationDefinitionError(prefix + 'minimum selections exceeds available choices.')
        if kind == 'colour' and any(not re.fullmatch(r'#[0-9a-fA-F]{6}', c.get('value', '') if isinstance(c, dict) else c)
                                    for c in field.get('choices', [])):
            raise CustomisationDefinitionError(prefix + 'palette choices need six-digit hex colours.')
        if 'default' in field and kind:
            if kind == 'file_upload':
                raise CustomisationDefinitionError(prefix + 'file defaults are not permitted.')
            problem = answer_error(field, field['default'], check_required=False)
            if problem:
                raise CustomisationDefinitionError(prefix + 'invalid default: ' + problem)
    if value.get('template') and not resolved:
        _definition(value)  # verify the pinned dependency and distinct additions


def answer_error(field, value, *, check_required=True):
    """Authoring preview/default checks, not storefront/server upload enforcement."""
    kind = TYPES[field['type']]
    if kind == 'checkbox' and value is not None and type(value) is not bool:
        return 'Boolean required.'
    empty = value is None or value == '' or value == [] or kind == 'checkbox' and value is False
    if empty:
        return 'Required.' if check_required and field.get('required') else None
    if kind in {'text', 'textarea'}:
        if not isinstance(value, str): return 'Text required.'
        if len(value) < field.get('min_length', 0): return 'Too short.'
        if len(value) > field.get('max_length', float('inf')): return 'Too long.'
    elif kind == 'number':
        if type(value) not in {int, float}: return 'Number required.'
        if field.get('whole_number') and value != int(value): return 'Whole number required.'
        if not field.get('minimum', float('-inf')) <= value <= field.get('maximum', float('inf')): return 'Outside numeric bounds.'
    elif kind in {'dropdown', 'radio'}:
        if value not in choices(field): return 'Choose an allowed option.'
    elif kind == 'checkbox_group':
        if not isinstance(value, list) or any(v not in choices(field) for v in value) or len(set(value)) != len(value): return 'Invalid choices.'
        if not field.get('min_selections', 0) <= len(value) <= field.get('max_selections', float('inf')): return 'Outside selection limits.'
    elif kind == 'checkbox':
        if type(value) is not bool: return 'Boolean required.'
    elif kind == 'date':
        try:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value: return 'Invalid date.'
        except ValueError: return 'Invalid date.'
        if value < field.get('min_date', value) or value > field.get('max_date', value): return 'Outside date bounds.'
    elif kind == 'colour':
        if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value): return 'Invalid colour.'
        palette = [v.get('value') if isinstance(v, dict) else v for v in field.get('choices', [])]
        if palette and value not in palette: return 'Choose an approved colour.'
    elif kind == 'file_upload': return 'File defaults are not permitted; upload is not implemented.'
    return None


def _append_fields(inherited, local):
    conflicts = sorted(set(inherited) & set(local))
    if conflicts:
        raise CustomisationDefinitionError('Duplicate effective customisation field IDs: ' + ', '.join(conflicts) +
            '. Product/collection additions must have distinct IDs. Review the authored definitions manually; no JSON was changed.')
    result = {key: deepcopy(field) for key, field in ordered_fields({'fields': inherited})}
    previous = max(((field.get('order', 0), key) for key, field in result.items()), default=None)
    for key, field in ordered_fields({'fields': local}):
        value = deepcopy(field)
        # The publisher already consumes numeric order. Adjust only resolved
        # additions when necessary; never rewrite an authored order or parent.
        if previous is not None and (value.get('order', 0), key) <= previous:
            value['order'] = previous[0] + 1
        result[key] = value
        previous = (value.get('order', 0), key)
    return result


def _definition(layer):
    validate(layer, resolved=True)
    result = {}
    if layer.get('template'):
        from app.customisation_templates import definition
        result = definition(layer['template'])
    fields = _append_fields(result.get('fields', {}), layer.get('fields', {}))
    result.update({key: deepcopy(value) for key, value in layer.items() if key not in {'fields', 'template'}})
    result['fields'] = fields
    validate(result, resolved=True)
    return result


def merge(shared, override):
    """Protect template/collection fields; append distinct local definitions only."""
    inherited = _definition(shared)
    validate(override, product_override=True)
    fields = _append_fields(inherited['fields'], override.get('fields', {}))
    result = deepcopy(inherited)
    # Other customisation properties retain their existing scalar precedence.
    for key, value in override.items():
        if key != 'fields': result[key] = deepcopy(value)
    result['fields'] = fields
    validate(result, resolved=True)
    return result
