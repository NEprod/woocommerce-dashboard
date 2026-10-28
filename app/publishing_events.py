"""Human-readable immutable publishing events; delivery stays in existing receipts."""
import json
from app import db
from app.models import ShopifyPublishingEvent
from app.utils.redaction import redact_diagnostic


def changes(plan):
    result = []
    for field in plan.get('fields', []):
        if not field.get('changed'):
            continue
        name = str(field.get('field', ''))
        if any(word in name.lower() for word in ('token', 'secret', 'password', 'nonce', 'publish_attempt', 'personalisation_snapshot')):
            continue
        if name == 'customisations.configuration':
            from app.customisation_review import compare
            details = compare(plan.get('customisations'), field.get('before'), field.get('after'))
            if details['enabled_change']:
                result.append({'field': name, 'label': 'Customer customisations enabled', **details['enabled_change']})
            for item in details['changes']:
                if item['kind'] in {'Added', 'Removed'}:
                    result.append({'field': name + '.' + item['id'], 'label': item['label'],
                        'before': 'Not present' if item['kind'] == 'Added' else item['description'],
                        'after': item['description'] if item['kind'] == 'Added' else 'Removed'})
                for setting in item['changes']:
                    result.append({'field': name + '.' + item['id'], 'label': item['label'] + ' · ' + setting['setting'],
                        'before': setting['before'], 'after': setting['after']})
            if not details['changes'] and not details['enabled_change']:
                result.append({'field': name, 'label': 'Customer customisations', 'before': details['before'],
                    'after': details['after'], 'detail': details['message']})
            continue
        result.append({'field': name, 'label': field.get('label') or name,
                       'before': field.get('before'), 'after': field.get('after')})
    return result


def record(operation, product, identity, evidence):
    from app.shopify_notifications import scope_of, operation_facts
    scope = scope_of(operation)
    execution = scope.get('automatic_publishing') or {}
    plan = evidence.get('plan', {})
    if scope.get('mode') == 'publication':
        plan = evidence.get('publication_update', {}).get('plan', plan)
    items = changes(plan)
    facts = operation_facts(operation, identity, evidence)
    kind = 'verified' if operation.status == 'succeeded' else 'processing' if identity and identity.state in {'processing','uploading'} else 'recovery_required' if operation.recovery_state not in {None, 'none'} else 'partial' if operation.status == 'partial' else 'failed'
    reviewed_items = items
    if kind != 'verified':
        # A later package failure must not hide conclusively verified product
        # work. Never promote reviewed intent or package input to readback proof.
        from app.shopify_publication import complete as publication_complete
        def verified(change):
            if not facts['readback_complete']:
                return False
            field = change['field']
            if field.startswith(('package_assignment.', 'native.')):
                return False
            if field.startswith('publication.'):
                return facts['readback_complete'] and publication_complete(evidence)
            group = 'media' if field.startswith('media.') else 'attributes' if field.startswith('attributes.') else 'memberships' if field.startswith(('categories.', 'collections.')) else 'fields'
            return group in facts['verified_stages'] and 'identity' in facts['verified_stages']
        items = [change for change in items if verified(change)]
    event_id = operation.id + ':' + kind
    if not db.session.get(ShopifyPublishingEvent, event_id):
        data = {'sku': product.sku if product else identity.sku if identity else None,
            'title': product.title if product else plan.get('title'), 'changes': items,
            'reviewed_changes': reviewed_items if kind != 'verified' else [],
            'status': operation.status, 'recovery_state': operation.recovery_state,
            'verified_stages': facts['verified_stages'], 'diagnostic': facts['diagnostic'],
            'package_stage': facts['package'], 'package_attempted': facts['package_attempted'],
            'historical_package_accepted': facts['historical_package_accepted'],
            'reason': redact_diagnostic(operation.error or '', limit=1000),
            'started_at': operation.started_at.isoformat(), 'finished_at': operation.finished_at.isoformat(),
            'verification_scope': {'publication': 'status/channels only',
                'customisations': 'customer configuration only', 'automatic_preflight': 'local preflight; no remote write',
                'sale_activate': 'scheduled price only', 'sale_end': 'scheduled price only'}.get(scope.get('mode'),
                    'inventory only' if scope.get('scope') == 'inventory' else 'acknowledged uploads only; no ProductCreate attempted' if identity and identity.state == 'uploading' else 'managed product'),
            'mode': scope.get('mode', 'create'),
            'processing_operation': execution.get('processing_operation'),
            'notification_receipt': operation.id}
        if facts['package'] == 'accepted':
            data['verification_scope'] += '; package assignment Shopify Accepted, independent package readback unavailable'
        elif facts['historical_package_accepted']:
            data['verification_scope'] += '; historical package acknowledgement retained (not written by this operation)'
        # Existing publisher bounds the plan; preserve full safe field values here.
        db.session.add(ShopifyPublishingEvent(id=event_id, operation_id=operation.id,
            product_id=product.id if product else None, event_type=kind, trigger=execution.get('trigger', 'manual_review'),
            local_revision=execution.get('revision'), occurred_at=operation.finished_at,
            receipt_json=json.dumps(data, ensure_ascii=False, default=str)))
    return items
