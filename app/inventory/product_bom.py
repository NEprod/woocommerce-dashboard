"""Resolve local product requirements without moving stock or sending BOMs remotely."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re

from app import db
from . import service as stock
from .models import Item

KEY = 'production'
LINE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}\Z')
MODES = {'unconfigured', 'calculated', 'manual'}
BOM_MODES = {'inherit', 'add', 'replace'}
LINE_FIELDS = {'item_id', 'quantity', 'unit'}
ROW_FIELDS = LINE_FIELDS | {'purpose', 'print_material'}
CENT = Decimal('.01')


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f'{label} must contain only supported fields.')


def _layer(layer, *, rule=False, costing=True):
    _object(layer, {'lines'} | ({'cost', 'costing', 'workflow'} if costing else set()) | {'mode'}, 'Production layer')
    if 'workflow' in layer:
        from app.order_work import validate_policy
        validate_policy(layer['workflow'])
    if 'costing' in layer:
        from .catalogue_bom import validate_costing
        validate_costing(layer['costing'])
    if 'cost' in layer:
        cost = layer['cost']
        _object(cost, {'mode', 'amount'}, 'Production cost')
        mode = cost.get('mode')
        if not isinstance(mode, str) or mode not in MODES or ('amount' in cost) != (mode == 'manual'):
            raise ValueError('Choose an unconfigured, calculated or manual production cost; only manual costs include an amount.')
        if mode == 'manual':
            stock.cost_number(cost['amount'], 'Manual production cost', optional=False)
    lines = layer.get('lines', {})
    if not isinstance(lines, dict) or len(lines) > 80:
        raise ValueError('A production layer supports at most 80 identified line changes.')
    mode = layer.get('mode', 'add')
    if not isinstance(mode, str) or mode not in BOM_MODES:
        raise ValueError('Choose Inherit, Add to BOM or Replace entire BOM.')
    if mode == 'inherit' and (lines or layer.get('costing')):
        raise ValueError('An Inherit rule must not contain BOM line instructions.')
    for key, row in lines.items():
        if not LINE_ID.fullmatch(key):
            raise ValueError('Each BOM line needs a stable line ID.')
        _object(row, {'op'} | ROW_FIELDS, 'BOM line')
        op = row.get('op')
        fields = set(row) - {'op'}
        if mode == 'replace' and op != 'add':
            raise ValueError('A replacement BOM contains only independent requirements, not inherited-line changes.')
        if (op == 'add' and not {'item_id', 'quantity', 'unit'} <= fields or op == 'modify' and not fields
                or op == 'remove' and fields or op not in ('add', 'modify', 'remove')):
            raise ValueError('Use an explicit add, modify or remove instruction for each BOM line.')
        if 'item_id' in row and (type(row['item_id']) is not int or not 0 < row['item_id'] < 10**13):
            raise ValueError('Select an existing inventory item ID for each BOM requirement.')
        if 'unit' in row and (not isinstance(row['unit'], str) or row['unit'] not in stock.UNITS):
            raise ValueError('Choose a supported inventory unit.')
        if 'quantity' in row:
            stock.number(row['quantity'], 'BOM quantity', positive=True)
        if 'purpose' in row and row['purpose'] not in ('material', 'packaging'):
            raise ValueError('Choose material or packaging purpose.')
        if 'print_material' in row and row['print_material'] not in ('', 'yes'):
            raise ValueError('Print waste eligibility must be explicitly marked.')


def option_rules(layer):
    return {(name, value):rule for name, values in layer.get('options', {}).items() for value, rule in values.items()}


def validate(value):
    _object(value, {'lines', 'cost', 'variants', 'options', 'mode', 'costing', 'workflow'}, 'Production & Costing')
    _layer({k: v for k, v in value.items() if k not in {'variants', 'options'}})
    variants = value.get('variants', {})
    if not isinstance(variants, dict) or len(variants) > 160:
        raise ValueError('Variant BOM changes must be keyed by retained variant SKU (up to 160 per metadata owner).')
    for sku, layer in variants.items():
        if not sku.strip() or len(sku) > 191:
            raise ValueError('A variant BOM requires its retained SKU.')
        _layer(layer, rule=True)
    options = value.get('options', {})
    if not isinstance(options, dict) or len(options) > 160:
        raise ValueError('Option BOMs must be grouped by exact option name and value.')
    count = 0
    for name, choices in options.items():
        if not name.strip() or len(name) > 255 or not isinstance(choices, dict):
            raise ValueError('Use an exact, valid option name for each option BOM.')
        count += len(choices)
        if count > 160:
            raise ValueError('At most 160 option value BOMs may be stored per metadata owner.')
        for label, layer in choices.items():
            if not label.strip() or len(label) > 255:
                raise ValueError('An option BOM needs its exact option value.')
            _layer(layer, rule=True, costing=False)


def authored(product):
    from app.metadata_workspace import metadata_source
    shared = metadata_source(product, 'shared')
    override = metadata_source(product, 'override')
    if override['reference'] == shared['reference']:
        override = {'data': {}}
    return shared['data'], override['data']


def _amount(value):
    return format(value, 'f') if value is not None else None


def item_rate(item):
    rate = stock.rate(item)
    return stock.recipe_estimate(item) if rate is None and item.kind == 'manufactured' else rate


class _Recipe:
    def __init__(self):
        self.lines, self.conflicts = {}, defaultdict(list)
        self.configured = False

    def conflict(self, key, fields, message):
        self.conflicts[key].append((fields, message))

    def replace(self):
        self.lines.clear()
        self.conflicts.clear()
        self.configured = True

    def apply(self, key, change, origin, *, exception=False):
        action = change['op']
        previous = self.lines.get(key)
        issues = self.conflicts.get(key, [])
        self.configured = True
        if exception and issues:
            if action == 'remove' or action == 'add' and previous is None or (
                    action == 'modify' and previous is not None and
                    all(fields <= change.keys() for fields, _ in issues)):
                self.conflicts.pop(key)
                if previous is None and action == 'remove':
                    return  # a deliberate variant exclusion resolves a missing option target
            else:
                return
        if (previous is not None) == (action == 'add'):
            self.conflict(key, LINE_FIELDS, f'{origin}: BOM line {key} conflicts with its inherited source.')
            return
        self.lines[key] = dict(previous or {}, **{k:v for k,v in change.items() if k != 'op'},
                               line_id=key, origin=origin, action=action, excluded=action == 'remove',
                               origins=list(dict.fromkeys([*((previous or {}).get('origins') or []), origin])))


def rule_mode(shared, override):
    if 'mode' in override:
        return override['mode']
    if shared.get('mode') == 'replace':
        return 'replace'
    if override.get('lines') or override.get('costing'):
        return 'add'
    return shared.get('mode', 'add' if shared.get('lines') or shared.get('costing') else 'inherit')


def _rule(shared, override, label):
    """Compile one exact option value's two authored layers into one independent rule."""
    mode = rule_mode(shared, override)
    patches, conflicts = {}, []
    if mode == 'inherit':
        return mode, patches, conflicts
    sources = [] if override.get('mode') == 'replace' else [(shared, 'Shared ' + label)]
    sources.append((override, label))
    for layer, origin in sources:
        for key, change in layer.get('lines', {}).items():
            prior = patches.get(key)
            if prior is None:
                patches[key] = (change, origin)
                continue
            old = prior[0]
            if change['op'] == 'add':
                conflicts.append((key, f'{origin}: duplicate requirement ID {key} in this option rule.'))
            elif old['op'] == 'add':
                if change['op'] == 'remove':
                    del patches[key]
                else:
                    patches[key] = ({**old, **change, 'op':'add'}, prior[1] + '; ' + origin)
            else:
                patches[key] = (({**old, **change} if change['op'] == 'modify' else change), origin)
    return mode, patches, conflicts


def _option_state(recipe, shared, override, matches):
    source, local = option_rules(shared), option_rules(override)
    rules = []
    for choice in sorted(set(matches) & (source.keys() | local.keys())):
        label = f'{choice[0]} = {choice[1]}'
        mode, patches, errors = _rule(source.get(choice, {}), local.get(choice, {}), label)
        if mode != 'inherit':
            rules.append((label, mode, patches, errors))
    replacements = [label for label, mode, _, _ in rules if mode == 'replace']
    if len(replacements) > 1:
        recipe.conflict(None, set(), 'Competing replacement BOMs: ' + '; '.join(replacements) +
                        '. Set this individual variant to Replace entire BOM or change the option rules.')
    if len(replacements) == 1:
        recipe.replace()
    additions, targeted = defaultdict(list), defaultdict(list)
    for label, mode, patches, errors in rules:
        if mode == 'replace':
            if len(replacements) != 1:
                continue
            for key, (change, origin) in patches.items():
                recipe.apply(key, change, origin)
        else:
            for key, (change, origin) in patches.items():
                (additions if change['op'] == 'add' else targeted)[key].append((change, origin))
        for key, message in errors:
            recipe.conflict(key, LINE_FIELDS, message)
    for key, entries in targeted.items():
        labels = '; '.join(origin for _, origin in entries)
        row = recipe.lines.get(key)
        target = f'requirement {key}' if not row else f'inventory item #{row["item_id"]} (line {key})'
        if not row:
            recipe.conflict(key, LINE_FIELDS, f'{labels}: target {target} is absent from the inherited recipe.')
            continue
        if any(change['op'] == 'remove' for change, _ in entries):
            if not all(change['op'] == 'remove' for change, _ in entries):
                recipe.conflict(key, LINE_FIELDS, f'{labels}: disagree about excluding or changing {target}.')
            else:
                recipe.apply(key, {'op':'remove'}, labels)
            continue
        patch, disagreement = {}, set()
        for change, _ in entries:
            for field in ROW_FIELDS & change.keys():
                value = change[field]
                if field == 'quantity':
                    value = format(Decimal(str(value)).normalize(), 'f')
                if field in patch and patch[field] != value:
                    disagreement.add(field)
                else:
                    patch[field] = value
        if disagreement:
            compatible = {field:value for field, value in patch.items() if field not in disagreement}
            if compatible:
                recipe.apply(key, {'op':'modify', **compatible}, labels)
            recipe.conflict(key, disagreement, f'{labels}: incompatible changes to {target} ({", ".join(sorted(disagreement))}).')
        else:
            recipe.apply(key, {'op':'modify', **patch}, labels)
    for key, entries in additions.items():
        if len(entries) != 1 or key in targeted:
            labels = '; '.join(origin for _, origin in entries + targeted.get(key, []))
            recipe.conflict(key, LINE_FIELDS, f'{labels}: duplicate or competing BOM line ID {key}.')
        else:
            change, origin = entries[0]
            recipe.apply(key, change, origin)


def _requirements(shared, override):
    if override.get('mode') == 'inherit':
        override = {}
    elif override.get('mode') == 'replace':
        # A main-product rewrite is independent of collection requirements,
        # including collection option/SKU recipes. Cost-source selection remains
        # the established separate, inheritable projection.
        shared = {'cost':shared['cost']} if 'cost' in shared else {}
    return shared, override


def _resolved(shared, override, variant_sku, matches):
    shared, override = _requirements(shared, override)
    recipe, selected = _Recipe(), None
    for layer, label in [(shared, 'Shared'), (override, 'Product')]:
        if layer is override and layer.get('mode') == 'inherit':
            continue
        if layer is override and layer.get('mode') == 'replace':
            recipe.replace()
        for key, change in layer.get('lines', {}).items():
            recipe.apply(key, change, label)
        if 'cost' in layer:
            selected = (label, layer['cost'])
    if matches and (shared.get('options') or override.get('options')):
        _option_state(recipe, shared, override, matches)
    if variant_sku is not None:
        parent_rule = shared.get('variants', {}).get(variant_sku, {})
        local_rule = override.get('variants', {}).get(variant_sku, {})
        mode = rule_mode(parent_rule, local_rule)
        if mode == 'replace':
            recipe.replace()
        for layer, label in [(parent_rule, 'Shared variant'), (local_rule, 'Variant')]:
            if 'cost' in layer:
                selected = (label, layer['cost'])
            if mode != 'inherit' and (layer is not parent_rule or local_rule.get('mode') != 'replace'):
                for key, change in layer.get('lines', {}).items():
                    recipe.apply(key, change, label, exception=True)
    return recipe, selected


def resolve(shared, override=None, variant_sku=None, *, matches=(), rates=None):
    """Resolve one sellable recipe; option rules are combined, not applied in display order."""
    recipe, selected = _resolved(shared or {}, override or {}, variant_sku, matches)
    return _cost(recipe, selected, rates)


def _cost(recipe, selected, rates):
    rates = {} if rates is None else rates
    conflicts = [dict(line_id=key, fields=sorted(fields), message=message)
                 for key, issues in recipe.conflicts.items() for fields, message in issues]
    warnings = [issue['message'] for issue in conflicts]
    rows, total, incomplete = [], Decimal(0), bool(conflicts)
    with localcontext() as ctx:
        ctx.prec = 48
        for record in recipe.lines.values():
            row = dict(record, name=None, kind=None, cost=None, problem=None)
            item_id = row['item_id']
            if row['excluded']:
                item = rates[item_id][0] if item_id in rates else db.session.get(Item, item_id)
                row['name'] = item.name if item else None
            else:
                if item_id not in rates:
                    item = db.session.get(Item, item_id)
                    rates[item_id] = (item, item_rate(item) if item is not None and item.active else None)
                item, rate = rates[item_id]
                if item is None:
                    row['problem'] = f'Inventory item #{item_id} is missing.'
                else:
                    row.update(name=item.name, kind=item.kind)
                    try:
                        amount = stock.converted(stock.number(row['quantity'], 'BOM quantity', positive=True), row['unit'], item.unit)
                    except ValueError as exc:
                        row['problem'] = str(exc)
                    else:
                        if not item.active:
                            row['problem'] = 'Inventory item is archived.'
                        elif rate is None:
                            row['problem'] = 'Inventory unit cost is missing.'
                        else:
                            line_cost = Decimal(amount) * rate / stock.SCALE
                            row['cost'] = _amount(line_cost)
                            total += line_cost
                if row['problem']:
                    incomplete = True
                    warnings.append(f"{row['name'] or '#' + str(item_id)}: {row['problem']}")
            rows.append(row)
        count = sum(not row['excluded'] for row in rows)
        configured = bool(recipe.configured or conflicts)
        state = 'incomplete' if incomplete else 'complete' if count else 'empty' if configured else 'not_configured'
        origin, choice = selected or ('Default', {'mode': 'calculated' if configured else 'unconfigured'})
        estimate = total if state == 'complete' else None
        effective = (Decimal(stock.cost_number(choice['amount'], optional=False)) / stock.SCALE if choice['mode'] == 'manual'
                     else estimate if choice['mode'] == 'calculated' else None)
        if effective is not None and effective > Decimal(stock.MAX_COST) / stock.SCALE:
            warnings.append('Calculated cost is outside the supported GBP range.')
            state, effective = 'incomplete', None
        if state == 'empty':
            warnings.append('The resolved recipe has no requirements; this does not establish zero cost.')
        return dict(rows=rows, count=count, state=state, warnings=warnings, conflicts=conflicts,
                    picking_ready=state == 'complete', estimated=_amount(estimate),
                    mode=choice['mode'], mode_source=origin,
                    shopify=_amount(effective.quantize(CENT, rounding=ROUND_HALF_UP)) if effective is not None else None)


def option_groups(product, document):
    from app.taxonomy_assignments import variation_drivers
    if product.product_type != 'variable':
        return {}
    attributes = document.get('attributes', {})
    return {name: tuple(dict.fromkeys(v for v in attributes.get(name, []) if isinstance(v, str) and v.strip()))
            for name in variation_drivers(document) if isinstance(attributes.get(name), list)}


def _matches(variant, choices):
    return [(a.name, a.value) for a in variant.attributes if a.value in choices.get(a.name, ())]


def _variants(product):
    return [v for v in product.variations if v.catalogue_status == 'active']


def _choices(groups):
    return {(name, value) for name, values in groups.items() for value in values}


def _orphan_options(shared, override, choices):
    return [{'name':name, 'value':value, 'local':(name,value) in option_rules(override)}
            for name,value in sorted((option_rules(shared).keys() | option_rules(override).keys()) - choices)]


def _apply_orphan_variant_checks(result, override):
    missing = set(override.get('variants', {})) - result.keys()
    if missing:
        for value in result.values():
            value['warnings'].append('Unmatched variant BOM overrides (retained, not reassigned): ' + ', '.join(sorted(missing)))
            if value['mode'] == 'calculated':
                value.update(state='incomplete', shopify=None, picking_ready=False)


def product_costs(product, documents=None):
    """Per sellable SKU for editors, durable review plans and future fulfilment."""
    shared_doc, override_doc = documents if documents is not None else authored(product)
    if KEY not in shared_doc and KEY not in override_doc:
        return {}
    shared, override = shared_doc.get(KEY, {}), override_doc.get(KEY, {})
    validate(shared)
    validate(override)
    shared, override = _requirements(shared, override)
    rates = {}
    if product.product_type != 'variable':
        return {product.sku: resolve(shared, override, rates=rates)}
    groups = {}
    if shared.get('options') or override.get('options'):
        from app.metadata_workspace import resolved_metadata
        groups = option_groups(product, resolved_metadata(product, shared_doc, override_doc))
    result = {v.sku: resolve(shared, override, v.sku, matches=_matches(v, groups), rates=rates) for v in _variants(product)}
    _apply_orphan_variant_checks(result, override)
    orphans = _orphan_options(shared, override, _choices(groups))
    if orphans:
        message = 'Unmatched option BOM rules (retained, not applied): ' + '; '.join(f'{r["name"]} = {r["value"]}' for r in orphans)
        for value in result.values():
            value['warnings'].append(message)
    return result


def contract(product, documents=None):
    """Small, private plan projection; never pass inventory/BOM records to Shopify."""
    from .profitability import finished_product_estimate
    documents = documents if documents is not None else authored(product)
    result = {}
    for sku, projection in product_costs(product, documents).items():
        if projection['mode'] == 'unconfigured':
            continue
        value, problems = projection['shopify'], projection['warnings']
        if projection['mode'] == 'calculated':
            try:
                full = finished_product_estimate(product, sku, documents=documents, projection=projection)
                value = full.get('cost') if full['complete'] else None
                problems = full['missing']
            except (ValueError, OSError) as exc:
                value, problems = None, [str(exc)]
        result[sku] = {'mode': projection['mode'], 'value': value}
        if projection['mode'] == 'calculated' and value is None:
            result[sku]['problem'] = (problems[0] if problems else 'Full BOM production cost is incomplete.')[:220]
    return result


def editor_options(product, *, document=None):
    if document is None:
        from app.taxonomy_assignments import product_document
        document = product_document(product)
    try:
        groups = option_groups(product, document)
    except ValueError:
        groups = {}  # keep the existing metadata editor available to repair invalid drivers
    rows = Item.query.filter(Item.active.is_(True)).order_by(Item.name, Item.id).all()
    return {'items': [{'id': i.id, 'kind':i.kind, 'name': i.name, 'code': i.identity.code, 'supplier_sku': i.supplier_sku,
                       'unit': i.unit, 'units': [u for u, spec in stock.UNITS.items() if spec[0] == stock.UNITS[i.unit][0]]}
                      for i in rows],
            'option_groups': groups,
            'variants': [{'sku':v.sku, 'identity':v.source_identity,
                          'label':' · '.join(f'{a.name}: {a.value}' for a in v.attributes)}
                         for v in _variants(product)] if product.product_type == 'variable' else []}


def editor_preview(product, shared_document, override_document, *, shared_editor):
    from app.metadata_workspace import resolved_metadata
    groups = option_groups(product, resolved_metadata(product, shared_document, override_document))
    shared, override = shared_document.get(KEY, {}), override_document.get(KEY, {})
    validate(shared)
    validate(override)
    shared, override = _requirements(shared, override)
    rates = {}
    parent = resolve(shared, override, rates=rates)
    local = shared if shared_editor else override
    without_local = {**local, 'options':{}, 'variants':{}}
    shared_before, override_before = (without_local, override) if shared_editor else (shared, without_local)
    variants, options = {}, []
    for v in _variants(product):
        matches = _matches(v, groups)
        # Retain the edited option rules in the baseline for individual variants.
        no_variant = {**local, 'variants':{}}
        earlier = (no_variant, override) if shared_editor else (shared, no_variant)
        variants[v.sku] = resolve(shared, override, v.sku, matches=matches, rates=rates)
        variants[v.sku]['before'] = resolve(*earlier, v.sku, matches=matches, rates=rates)
    _apply_orphan_variant_checks(variants, override)
    shared_rules, override_rules = option_rules(shared), option_rules(override)
    authored_choices = shared_rules.keys() | override_rules.keys()
    for name, values in groups.items():
        for value in values:
            choice = (name,value)
            if choice not in authored_choices:
                continue
            result = resolve(shared, override, matches=[choice], rates=rates)
            before = resolve(shared_before, override_before, matches=[choice], rates=rates)
            _, patches, _ = _rule(shared_rules.get(choice, {}), override_rules.get(choice, {}), name)
            impact = None
            if result['estimated'] is not None and parent['estimated'] is not None:
                with localcontext() as ctx:
                    ctx.prec = 48
                    impact = _amount(Decimal(result['estimated']) - Decimal(parent['estimated']))
            options.append({'name':name, 'value':value,
                            'result':result, 'before':before, 'impact':impact, 'own_count':len(patches)})
    orphan_shared, orphan_local = ({}, shared) if shared_editor else (shared, override)
    return {'parent':parent, 'variants':variants, 'options':options,
            'orphans':_orphan_options(orphan_shared, orphan_local, _choices(groups))}


def check_changed_references(before, after, *, inherited=None, product=None, document=None):
    """Reject invalid newly authored references without trapping old broken BOMs."""
    if before == after:
        return
    shared, local = (inherited, after) if inherited is not None else (after, {})
    groups = option_groups(product, document or {}) if product is not None and (
        shared.get('options') or local.get('options')) else {}
    variants = {v.sku: v for v in _variants(product)} if product is not None else {}
    old_variants, old_options = before.get('variants', {}), option_rules(before)
    changes = [(None, (), before, after)]
    for sku, rule in after.get('variants', {}).items():
        current, previous = variants.get(sku), old_variants.get(sku, {})
        if previous != rule:
            if product is not None and current is None:
                raise ValueError(f'Variant {sku} no longer matches a retained sellable SKU. Reset the orphan or restore its variant.')
            changes.append((sku, _matches(current, groups) if current else (), previous, rule))
    declared = _choices(groups)
    for choice, rule in option_rules(after).items():
        previous = old_options.get(choice, {})
        if previous != rule:
            if product is not None and choice not in declared:
                raise ValueError(f'{choice[0]} = {choice[1]} is not a current variant option value. Reset the orphan or restore its option.')
            changes.append((None, [choice], previous, rule))
    for sku, matches, old, new in changes:
        if new.get('costing') != old.get('costing'):
            from .catalogue_bom import check_costing_references
            check_costing_references(old.get('costing', {}), new.get('costing', {}))
        recipe, _ = _resolved(shared, local, sku, matches)
        if old.get('lines', {}) != new.get('lines', {}):
            purposes = defaultdict(set)
            for row in recipe.lines.values():
                if not row['excluded']:
                    purposes[row['item_id']].add(row.get('purpose', 'material'))
            if any(len(scopes) > 1 for scopes in purposes.values()):
                raise ValueError('Packaging inventory is also present in materials/components. Combine its quantity in one section to avoid double-counting.')
        for line_id, line in new.get('lines', {}).items():
            if line == old.get('lines', {}).get(line_id) or line['op'] == 'remove':
                continue
            resolved = recipe.lines.get(line_id)
            if not resolved or (line['op'] == 'add' and resolved['item_id'] != line['item_id']):
                raise ValueError(f'BOM line {line_id} no longer has an inherited source. Reset it or add a separate line.')
            item = stock.item_for(resolved['item_id'])
            stock.converted(stock.number(resolved['quantity'], 'BOM quantity', positive=True), resolved['unit'], item.unit)
            if resolved.get('purpose') == 'packaging' and item.kind != 'purchased':
                raise ValueError('Packaging must reference a registered purchased component.')
            if resolved.get('print_material') == 'yes' and (item.kind != 'material' or resolved.get('purpose') == 'packaging'):
                raise ValueError('Additional 3D-print waste applies only to explicitly marked direct raw materials.')
