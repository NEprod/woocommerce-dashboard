"""Existing Intake screens, independently accessible, with reactive proposals."""
import json
from flask import request, render_template, redirect, url_for, flash, abort
from app import db
from app import intake_direct as service
from app.image_preparation import configured_intake_root, browse_intake
from app.models import CatalogueOperation


def intake_readiness():
    # The same readiness provider is used by preview, acceptance and display.
    return service.images.intake_readiness()


def options(stage):
    values = request.form if request.method == 'POST' else request.args
    if values.get('options'):
        parsed = json.loads(values['options'])
        if not isinstance(parsed, dict): raise ValueError('Invalid Intake settings')
        return parsed
    if stage == 'folders':
        result = {'renames': dict(zip(values.getlist('folder_current'), values.getlist('folder_proposed'))),
            'created': values.get('created_folders', '').splitlines(), 'remove_empty': values.getlist('remove_empty'),
            'preview_prefix': values.get('preview_prefix', 'preview')}
        if values.get('root_name'): result['root_name'] = values['root_name']
        prefix, suffix = values.get('folder_prefix', ''), values.get('folder_suffix', '')
        if prefix or suffix:
            result['renames'] = {a: str(service.PurePosixPath(b).parent / (prefix + service.PurePosixPath(b).name + suffix))
                                 for a, b in result['renames'].items()}
        return result
    if stage == 'rename': return {'prefix': values.get('prefix', '')}
    if stage == 'metadata':
        return {'document': values['document']} if 'document' in values else {}
    return {}


def index():
    selected = request.args.get('path', '')
    readiness, browser, error = intake_readiness(), None, None
    if readiness['readable']:
        try: browser = browse_intake(configured_intake_root(), selected)
        except ValueError as exc: error = str(exc)
    pending = []
    # Bounded history, not a recursive filesystem discovery or a new job source.
    for row in CatalogueOperation.query.filter(CatalogueOperation.operation_type.in_(
            [v[1] for v in service.STAGES.values()])).order_by(CatalogueOperation.started_at.desc()).limit(50):
        scope = json.loads(row.scope or '{}')
        transaction = scope.get(service.JOURNAL)
        if transaction and not service._transaction_complete(transaction):
            pending.append(service.operation_result(row.id))
    return render_template('image_preparation/direct_index.html', readiness=readiness,
        browser=browser, intake_error=error, stages=service.STAGES, pending=pending)


def page(stage):
    values = request.form if request.method == 'POST' else request.args
    relative = values.get('path', '')
    if not relative:
        return redirect(url_for('main.image_preparation', stage=stage))
    preview, error = None, None
    try: preview = service.plan(stage, relative, options(stage))
    except (ValueError, OSError, TypeError) as exc: error = str(exc)
    if request.args.get('live') == '1':
        html = render_template('image_preparation/_direct_plan.html', proposal=preview, intake_error=error)
        return {'html': html, 'ready': bool(preview and preview['ready']),
                'digest': preview['digest'] if preview else '',
                'options': preview['options'] if preview else {}}, 200
    return render_template('image_preparation/direct_stage.html', proposal=preview,
        stage=stage, title=service.STAGES[stage][0], source=relative,
        readiness=intake_readiness(), intake_error=error, stages=service.STAGES)


def confirm(stage):
    try:
        operation_id = service.accept(stage, request.form.get('path', ''), options(stage), request.form.get('digest', ''))
        return redirect(url_for('main.image_preparation_direct_result', operation_id=operation_id))
    except Exception as exc:
        from app.intake_grouping import IntakeOperationActive
        from app.utils.operation_control import CatalogueOperationActive
        if not isinstance(exc, (ValueError, OSError, IntakeOperationActive, CatalogueOperationActive)): raise
        flash(str(exc), 'danger')
        return redirect(url_for('main.image_preparation_direct_stage', stage=stage, path=request.form.get('path', '')))


def result(operation_id):
    try: value = service.operation_result(operation_id)
    except ValueError: abort(404)
    return render_template('image_preparation/direct_result.html', result=value, stages=service.STAGES)


def recover(operation_id):
    try:
        continued = service.recover(operation_id)
        row = db.session.get(CatalogueOperation, continued)
        if row and service.JOURNAL in json.loads(row.scope or '{}'):
            operation_id = continued
    except (ValueError, OSError, service.GroupingRejected) as exc: flash(str(exc), 'danger')
    return redirect(url_for('main.image_preparation_direct_result', operation_id=operation_id))
