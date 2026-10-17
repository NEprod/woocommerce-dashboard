"""Local choices and separately reviewed saved-package acceptance evidence."""
from app import shopify_drafts as drafts
from app.taxonomy_assignments import product_document
from app.packaging_contract import effective


def snapshot(product, plan):
    authored = product_document(product)
    variants = plan.get('variants') or [{'identity': 'product', 'sku': product.sku, 'options': []}]
    values = {}
    for variant in variants:
        packing = effective(authored, {o['name']: o['value'] for o in variant['options']})
        values[variant['identity']] = {k: packing[k] for k in
            ('selected_package_id', 'assignment_kind', 'override_reason') if k in packing}
    return values


def attach(product, plan, row, config=None, shop=None):
    from app.packaging_models import Package
    from app.packaging_catalogue import view
    from app.packing import recommend
    packages = [view(p) for p in Package.query.all()]
    names = {p['id']: p['name'] for p in packages}
    current = snapshot(product, plan)
    baseline = drafts.evidence(row).get('plan', {}).get('optional_package_snapshot', {}) if row else {}
    plan['optional_package_snapshot'] = current
    entries = []
    authored = product_document(product)
    for variant in plan.get('variants') or [{'identity': 'product', 'sku': product.sku, 'options': [], 'managed': plan['managed'].get('variant', {})}]:
        key = variant['identity']
        value = current[key]
        before = baseline.get(key, {})
        selected = value.get('selected_package_id')
        packing = effective(authored, {o['name']: o['value'] for o in variant['options']})
        if 'weight_g' not in packing:
            weight = (variant.get('managed', {}).get('inventoryItem', {}).get('measurement', {}).get('weight') or {})
            if weight.get('unit') == 'GRAMS': packing['weight_g'] = str(weight['value'])
        recommendation = recommend(packing, packages)
        entries.append({'identity': key, 'sku': variant['sku'], 'selected_id': selected,
            'selected_name': names.get(selected, 'Saved local package ' + str(selected)) if selected else None,
            'before_name': names.get(before.get('selected_package_id'), before.get('selected_package_id')),
            'changed': value != before, 'state': 'Selected Locally — Remote Assignment Unsupported' if selected else
                'Explicitly Unassigned' if 'selected_package_id' in value else 'Recommended — Not Selected',
            'recommendation': recommendation['recommendation'], 'warnings': recommendation['warnings'],
            'remote_state': 'No verified assignment/clearing readback contract; no package mutation will be sent.'})
    plan['optional_packages'] = entries
    plan['local_package_changes'] = sum(e['changed'] for e in entries)
    if config is not None and shop is not None:
        from app.shopify_package_assignments import attach as attach_assignments
        attach_assignments(plan,row,config,shop)
    # Local snapshots alone never authorize a write. The assignment adapter adds
    # an actionable field only for an explicitly selected, centrally linked ID.
