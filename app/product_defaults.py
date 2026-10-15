"""Effective shipping/inventory policy; authored inheritance stays in json_utils."""
import os

DEFAULTS = {
    'country_of_origin': ('DEFAULT_COUNTRY_OF_ORIGIN', None),
    'requires_shipping': ('DEFAULT_REQUIRES_SHIPPING', None),
    'manage_stock': ('DEFAULT_MANAGE_STOCK', False),
    'backorders': ('DEFAULT_BACKORDERS', 'yes'),
}


def effective(authored):
    values, sources = {}, {}
    for key, (environment, fallback) in DEFAULTS.items():
        if key in authored:
            value, source = authored[key], 'authored'
        elif os.environ.get(environment, '') != '':
            value, source = os.environ[environment], 'deployment_default'
            if key in {'requires_shipping', 'manage_stock'}:
                if value.lower() not in {'true', 'false'}:
                    raise ValueError(f'{environment} must be true or false, or blank to remain unset.')
                value = value.lower() == 'true'
        else:
            if key == 'backorders':
                fallback = 'no' if values.get('manage_stock') else 'yes'
            value, source = fallback, 'application_fallback' if fallback is not None else 'unset'
        if key in {'requires_shipping','manage_stock'} and value is not None and type(value) is not bool:
            raise ValueError(f'{key} must be an explicit boolean; remove the key to inherit.')
        if key == 'manage_stock' and value is None:
            raise ValueError('manage_stock cannot be null; remove the key to inherit.')
        if key == 'backorders' and value not in {'no','yes','notify'}:
            raise ValueError('backorders must be no, yes or notify; remove the key to inherit.')
        if key == 'country_of_origin' and value is not None:
            import re
            if not isinstance(value, str) or not re.fullmatch('[A-Z]{2}', value):
                raise ValueError('Country of origin must be an uppercase two-letter code; remove the key to inherit.')
        values[key], sources[key] = value, source
    return values, sources


def display(shared, override, resolved):
    values, sources = effective(resolved)
    return {key: {'value': values[key], 'source': 'Product override' if key in override else
        'Shared collection' if key in shared else 'Unraid/environment default' if sources[key] == 'deployment_default'
        else 'Application fallback' if sources[key] == 'application_fallback' else 'Unknown / unmanaged'} for key in DEFAULTS}
