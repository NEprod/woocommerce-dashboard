"""Read-only presentation of the existing reviewed public configuration snapshot.

Never used for authoring, signing, deciding writes, verification or recovery.
Production Time and customer answers are not part of this public field document.
"""
import json
import re

from app.storefront_customisations import FIELD_KEYS
from app.customisations import validate

TYPES = {'text': 'text', 'textarea': 'multi-line text', 'number': 'number',
         'dropdown': 'dropdown', 'radio': 'radio', 'checkbox': 'checkbox',
         'checkbox_group': 'multiple-choice', 'date': 'date', 'colour': 'colour'}
SETTINGS = {'label': 'Label', 'type': 'Input type', 'required': 'Required',
    'placeholder': 'Placeholder', 'help_text': 'Help text', 'choices': 'Allowed choices',
    'default': 'Default value', 'min_length': 'Minimum text length',
    'max_length': 'Maximum text length', 'minimum': 'Minimum number',
    'maximum': 'Maximum number', 'whole_number': 'Whole numbers only',
    'min_date': 'Earliest date', 'max_date': 'Latest date',
    'min_selections': 'Minimum selections', 'max_selections': 'Maximum selections'}
DEFAULTS = {'required': False, 'whole_number': False, 'placeholder': '', 'help_text': '',
            'min_length': 0, 'min_selections': 0}
UNAVAILABLE = ('Detailed previous customer customisation field settings are unavailable. '
               'No field-level changes are inferred; the existing review action and available revision evidence still apply.')


def display(value):
    if value is None:
        return 'Not set'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, str):
        return value or 'Empty'
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _fields(config):
    """Require identifiable complete public definitions, not counts or revisions."""
    if (not isinstance(config, dict) or type(config.get('schema_version')) is not int or config['schema_version'] != 1
            or not isinstance(config.get('enabled'), bool) or not isinstance(config.get('fields'), list)):
        return None
    fields = {}
    for field in config['fields']:
        if (not isinstance(field, dict) or not isinstance(field.get('id'), str)
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', field['id']) or field['id'] in fields
                or not isinstance(field.get('type'), str) or field['type'] not in TYPES or not isinstance(field.get('label'), str)
                or not field['label'].strip() or set(field) - (FIELD_KEYS | {'id', 'type', 'choices'})):
            return None
        if ('required' in field and not isinstance(field['required'], bool)
                or 'choices' in field and (not isinstance(field['choices'], list)
                    or any(not isinstance(c, dict) or not isinstance(c.get('id'), str)
                           or not isinstance(c.get('label'), str) for c in field['choices']))):
            return None
        fields[field['id']] = field
    if config['enabled'] and not fields:
        return None
    try:
        validate({'fields': {key: {k: v for k, v in field.items() if k != 'id'}
                             for key, field in fields.items()}}, resolved=True)
    except (ValueError, TypeError):
        return None
    return fields


def _summary(config, fallback=None):
    config = config if isinstance(config, dict) else fallback if isinstance(fallback, dict) else {}
    enabled = config.get('enabled')
    fields = config.get('fields')
    count = len(fields) if isinstance(fields, list) else fields if type(fields) is int else None
    return ('Enabled' if enabled is True else 'Disabled' if enabled is False else 'State unavailable') + (
        f" · {count} {'field' if count == 1 else 'fields'}" if count is not None else '')


def _revision(config, fallback):
    return (config if isinstance(config, dict) else fallback if isinstance(fallback, dict) else {}).get('revision')


def _settings(field):
    return [{'setting': label, 'value': display(field[key])}
            for key, label in SETTINGS.items() if key in field]


def _effective_setting(field, key):
    if key == 'default':
        empty = False if field['type'] == 'checkbox' else [] if field['type'] == 'checkbox_group' else ''
        return field[key] if field.get(key) is not None else empty
    return field.get(key, DEFAULTS.get(key))


def _setting_display(key, value):
    if key == 'choices' and isinstance(value, list):
        return '; '.join(f"{c['label']} [{c['id']}]" + (f" ({c['value']})" if 'value' in c else '') for c in value)
    return display(value)


def compare(spec, before_summary=None, after_summary=None):
    """Use only the full before/desired values already held in the signed review.

    Revision-only/unknown documents are not treated as empty configurations.
    List order is the renderer's field order; numeric order bookkeeping alone
    is not a customer-visible reorder. Added/removed fields do not imply that
    every remaining field was deliberately reordered.
    """
    spec = spec or {}
    before = spec.get('before') or {}
    known_absent = 'value' in before and before['value'] is None
    previous = None
    try:
        previous = json.loads(before['value']['value']) if before.get('value') else None
    except (ValueError, TypeError, KeyError):
        pass
    desired = spec.get('desired')
    result = {'before': 'Not published' if known_absent else _summary(previous, before_summary),
        'after': _summary(desired, after_summary), 'available': False, 'changes': [], 'matching': [],
        'enabled_change': None, 'message': UNAVAILABLE,
        'previous_revision': _revision(previous, before_summary),
        'reviewed_revision': _revision(desired, after_summary)}
    old = {} if known_absent else _fields(previous)
    new = _fields(desired)
    if old is None or new is None:
        return result
    result['available'] = True
    if not known_absent and previous['enabled'] != desired['enabled']:
        result['enabled_change'] = {'before': display(previous['enabled']), 'after': display(desired['enabled'])}
    old_common, new_common = [key for key in old if key in new], [key for key in new if key in old]
    reordered = old_common != new_common
    old_ranks, new_ranks = {key: i for i, key in enumerate(old_common)}, {key: i for i, key in enumerate(new_common)}
    old_positions, new_positions = {key: str(i + 1) for i, key in enumerate(old)}, {key: str(i + 1) for i, key in enumerate(new)}
    for key in [*new, *(key for key in old if key not in new)]:
        field = new.get(key, old.get(key))
        entry = {'id': key, 'label': field['label'], 'changes': [], 'settings': _settings(field)}
        if key not in old or key not in new:
            entry.update(kind='Added' if key in new else 'Removed',
                description=f"{'Required' if field.get('required') else 'Optional'} {TYPES[field['type']]} field")
        else:
            for setting, label in SETTINGS.items():
                a, b = _effective_setting(old[key], setting), _effective_setting(new[key], setting)
                # JSON numbers 1/1.0 behave alike; False is not numeric zero.
                if a != b or isinstance(a, bool) != isinstance(b, bool):
                    entry['changes'].append({'setting': label, 'before': _setting_display(setting, a), 'after': _setting_display(setting, b)})
            if reordered and old_ranks[key] != new_ranks[key] and old_positions[key] != new_positions[key]:
                entry['changes'].append({'setting': 'Position', 'before': old_positions[key],
                                         'after': new_positions[key]})
            if not entry['changes']:
                result['matching'].append(entry)
                continue
            entry.update(kind='Reordered' if all(c['setting'] == 'Position' for c in entry['changes']) else 'Changed',
                         description='')
        result['changes'].append(entry)
    result['message'] = '' if result['changes'] or result['enabled_change'] else (
        'No customer-facing field differences were found in the available definitions. '
        'Any technical configuration or revision difference still follows the existing review action.')
    return result
