"""Bulk entry only: save ordinary manufactured items in the caller's transaction."""
from . import service as stock


def save_group(values, parts, *, image=None):
    group = stock.clean(values.get('group_name'), 'Group name', 120, required=True)
    if not isinstance(parts, list) or not 1 <= len(parts) <= 30:
        raise ValueError('Add between 1 and 30 components before saving.')
    names, codes, created = set(), set(), []
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict) or set(part) - {'name', 'code', 'yield_qty', 'materials'}:
            raise ValueError(f'Component {index}: invalid component details.')
        name = stock.clean(part.get('name'), f'Component {index} name', 120, required=True)
        code = stock.identifier(part.get('code'))
        if name.casefold() in names or code in codes:
            raise ValueError('Each component needs its own name and unique barcode.')
        names.add(name.casefold()); codes.add(code)
        materials = part.get('materials')
        if not isinstance(materials, list) or not 1 <= len(materials) <= 30:
            raise ValueError(f'{name}: add between 1 and 30 recipe materials.')
        requirements = []
        for material in materials:
            if not isinstance(material, dict) or set(material) != {'item_id', 'quantity', 'unit'}:
                raise ValueError(f'{name}: each material needs its inventory reference, quantity and unit.')
            requirements.append((material['item_id'], material['quantity'], material['unit']))
        data = {key: values.get(key, '') for key in ('category', 'specification', 'description',
            'default_location_id', 'minimum', 'reorder', 'active')}
        data.update(name=f'{group} - {name}', code=code, recipe_quantity_basis='piece',
                    yield_qty=part.get('yield_qty') or values.get('yield_qty'))
        try:
            created.append(stock.save_item('manufactured', data, requirements=requirements, image=image))
        except ValueError as exc:
            raise ValueError(f'{name}: {exc}') from None
    return created
