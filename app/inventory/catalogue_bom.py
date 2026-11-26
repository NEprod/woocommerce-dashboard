"""Lossless catalogue adapters for the shared planner. No writes or remote calls.

Authoring stays in production JSON; stock and Shopify cost projections retain
their existing independent contracts. Standalone snapshots are never changed.
"""
import copy
import json
import uuid

from app import db
from . import service as stock
from .models import CostingSettings, Equipment, Item
from .profitability import decimal, estimate, process_runtime

COSTING_FIELDS = {'equipment', 'labour_minutes', 'labour_rate', 'buffer',
                  'print_waste_rate', 'markup', 'other', 'fee_profile', 'price_rounding', 'etsy_price'}
PROCESS_FIELDS = {'equipment_id', 'minutes', 'timing_mode', 'usable_units', 'allocation', 'share_percent'}
INVENTORY_FIELDS = {'item_id', 'quantity', 'unit', 'purpose', 'print_material'}


def settings():
    row = db.session.get(CostingSettings, 1)
    return json.loads(row.document) if row else {}


def validate_costing(value):
    if not isinstance(value, dict) or set(value) - COSTING_FIELDS:
        raise ValueError('Catalogue costing contains unsupported fields; selling prices are not BOM inputs.')
    for key, number in value.items():
        if key not in {'equipment', 'fee_profile', 'price_rounding'}:
            decimal(number, key, maximum='100' if key == 'print_waste_rate' else '1000000000')
    if value.get('price_rounding', 'exact') not in {'exact', 'half', 'whole', '95', '49_99'}:
        raise ValueError('Choose a supported price presentation.')
    if 'fee_profile' in value and not isinstance(value['fee_profile'], str):
        raise ValueError('Choose a saved payment fee profile.')
    equipment = value.get('equipment', [])
    if not isinstance(equipment, list) or len(equipment) > 40:
        raise ValueError('Use at most 40 production processes.')
    for line in equipment:
        if not isinstance(line, dict) or set(line) - PROCESS_FIELDS:
            raise ValueError('Production process contains unsupported fields.')
        if not {'equipment_id', 'minutes'} <= line.keys() or type(line.get('equipment_id')) is not int:
            raise ValueError('Choose a registered equipment profile.')
        # Arithmetic validation is shared; missing/archived equipment is surfaced
        # by preview and changed-reference checks, not by rewriting old records.
        process_runtime(line)


def effective_costing(shared, local, sku=None):
    """Merge processing requirements, but preserve scalar override semantics."""
    from .product_bom import rule_mode
    result = {}
    if local.get('mode') == 'inherit':
        local = {}
    elif local.get('mode') == 'replace':
        shared = {}
    layers = [(False, shared), (local.get('mode') == 'replace', local)]
    if sku is not None:
        parent_rule, local_rule = shared.get('variants', {}).get(sku, {}), local.get('variants', {}).get(sku, {})
        mode = rule_mode(parent_rule, local_rule)
        if mode != 'inherit':
            if local_rule.get('mode') != 'replace':
                layers.extend([(mode == 'replace', parent_rule), (False, local_rule)])
            else:
                layers.append((True, local_rule))
    for reset, layer in layers:
        if reset:
            result = {}
        costing = layer.get('costing', {})
        equipment = result.get('equipment', []) + costing.get('equipment', [])
        # Labour and additional direct costs are requirements, so Inherit Plus
        # adds them; rates/allowances/markup remain explicit scalar overrides.
        totals = {k: str(decimal(result.get(k, '0'), k) + decimal(costing[k], k))
                  for k in ('labour_minutes', 'other') if k in costing}
        result.update(copy.deepcopy(costing))
        result.update(totals)
        if equipment:
            result['equipment'] = copy.deepcopy(equipment)
    return result


def check_costing_references(before, after):
    """New references must resolve; unchanged historical evidence is retained."""
    config = settings()
    if after.get('fee_profile') != before.get('fee_profile') and after.get('fee_profile'):
        if not any(p['id'] == after['fee_profile'] for p in config.get('fee_profiles', [])):
            raise ValueError('Choose an existing payment fee profile.')
    for line in after.get('equipment', []):
        if line in before.get('equipment', []):
            continue
        profile = db.session.get(Equipment, line['equipment_id'])
        if not profile or not profile.active:
            raise ValueError('Choose an active registered equipment/profile.')


def document(projection, shared, local, sku=None, *, legacy=None, price=''):
    assumptions = effective_costing(shared, local, sku)
    # Legacy DB profiles are fallback-only, not silently copied into JSON.
    if local.get('mode') != 'inherit' and not any('costing' in layer for layer in [shared, local,
            shared.get('variants', {}).get(sku, {}), local.get('variants', {}).get(sku, {})]):
        assumptions = {k: copy.deepcopy(v) for k, v in (legacy or {}).items() if k in COSTING_FIELDS}
        if legacy and 'selling_price' in legacy:
            price = legacy['selling_price']  # retain the legacy what-if comparison
    return {**assumptions, 'selling_price':price,
            'lines':[{k:r[k] for k in INVENTORY_FIELDS if k in r}
                     for r in projection['rows'] if not r['excluded']]}


def import_scenario(saved, *, current=None, append=False):
    """All-or-nothing mapping; caller applies only after explicit confirmation."""
    from .product_bom import validate, check_changed_references
    source = json.loads(saved.document)
    if not isinstance(source, dict):
        raise ValueError('Saved test BOM must be an object.')
    problems = []
    allowed = COSTING_FIELDS | {'lines', 'selling_price', '_snapshot', '_archived'}
    unknown = set(source) - allowed
    if unknown:
        problems.append('Unsupported scenario fields: ' + ', '.join(sorted(unknown)))
    lines = source.get('lines')
    if not isinstance(lines, list) or not lines:
        problems.append('The scenario has no inventory requirements.')
        lines = []
    mapped = {}
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            problems.append(f'Line {index + 1}: incomplete inventory requirement.')
            continue
        label = f'Line {index + 1} ({line.get("name") or line.get("item_id") or "unnamed"})'
        # Inventory packaging saved by the standalone form can retain a display
        # name; purchase/custom fields are never silently discarded.
        unsupported = set(line) - INVENTORY_FIELDS - {'name'}
        if line.get('source') == 'manual':
            problems.append(label + ': hypothetical materials/packaging cannot be imported.')
            continue
        if unsupported:
            problems.append(label + ': unsupported fields: ' + ', '.join(sorted(unsupported)))
            continue
        try:
            item = stock.item_for(line.get('item_id'))
            stock.converted(stock.number(line.get('quantity'), 'Quantity', positive=True), line.get('unit'), item.unit)
            mapped['line-' + uuid.uuid4().hex[:24]] = {'op':'add', **{k:v for k,v in line.items() if k in INVENTORY_FIELDS}}
        except (ValueError, TypeError, KeyError) as exc:
            problems.append(label + ': ' + str(exc))
    costing = {k:copy.deepcopy(v) for k,v in source.items() if k in COSTING_FIELDS}
    try:
        validate_costing(costing)
        config = settings()
        if costing.get('fee_profile') and not any(p['id'] == costing['fee_profile'] for p in config.get('fee_profiles', [])):
            raise ValueError('Payment fee profile is missing.')
        for process in costing.get('equipment', []):
            equipment = db.session.get(Equipment, process['equipment_id'])
            if not equipment or not equipment.active:
                raise ValueError('Equipment/profile is missing or archived.')
    except (ValueError, TypeError, KeyError) as exc:
        problems.append(str(exc))
    target = copy.deepcopy(current or {})
    if target.get('mode') == 'inherit':
        problems.append('Inherit All is read-only; select Inherit Plus or Rewrite first.')
    if append:
        existing = {r.get('item_id') for r in target.get('lines', {}).values() if r.get('op') != 'remove'}
        duplicates = existing & {r['item_id'] for r in mapped.values()}
        if duplicates:
            problems.append('Review duplicate inventory requirements before importing: ' + ', '.join(map(str, sorted(duplicates))))
        old_equipment = {r['equipment_id'] for r in target.get('costing', {}).get('equipment', [])}
        if old_equipment & {r['equipment_id'] for r in costing.get('equipment', [])}:
            problems.append('Duplicate equipment processes require review; import is blocked to avoid double charging.')
        for key in COSTING_FIELDS - {'equipment', 'labour_minutes', 'other'}:
            if key in costing and key in target.get('costing', {}) and costing[key] != target['costing'][key]:
                problems.append(f'Conflicting costing override: {key}.')
        target['lines'] = {**target.get('lines', {}), **mapped}
        target['costing'] = effective_costing(target, {'costing':costing})
    else:
        # Exceptions and explicit Shopify Cost source are not part of a scenario
        # and remain untouched when replacing the working main BOM.
        target.update(lines=mapped, costing=costing)
    if not problems:
        try:
            validate(target)
            check_changed_references({}, target)
            preview_document = {**costing, 'lines':[{k:v for k,v in r.items() if k != 'op'} for r in mapped.values()], 'selling_price':''}
            result = estimate(preview_document, settings(), direct_materials=True)
            if not result['complete']:
                problems.extend(result['missing'])
        except (ValueError, TypeError, KeyError) as exc:
            problems.append(str(exc))
    return dict(eligible=not problems, problems=problems, production=target if not problems else None,
                scenario=dict(id=saved.id, name=saved.name, version=saved.version),
                inputs=source, result=result if not problems else None,
                notice='Current inventory costs are recalculated. Historical scenario snapshots and catalogue selling prices remain unchanged.')
