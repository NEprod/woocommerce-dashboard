"""Existing administrator/CSRF conventions; GET never grants write authority."""
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from app import db, automatic_publishing as service
from app.models import Product, ShopifyPublishJob, ShopifyPublishingEvent

publishing = Blueprint('publishing', __name__)


@publishing.before_request
@login_required
def administrator():
    if not current_user.is_admin:
        abort(403)


@publishing.route('/settings/publishing', methods=['GET', 'POST'])
def index():
    error = None
    if request.method == 'POST':
        data = (request.get_json(silent=True) if request.is_json else request.form) or {}
        if request.is_json and not isinstance(data, dict):
            return jsonify(error='A publishing settings object is required.'), 400
        try:
            service.configure(enabled=data.get('enabled') if request.is_json else data.get('enabled') == 'yes',
                actor_id=current_user.id, confirmation=data.get('confirmation'),
                required_fields=data.get('required_fields') if request.is_json else data.getlist('required_fields'))
            return jsonify(ok=True) if request.is_json else redirect(url_for('publishing.index'))
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
            if request.is_json:
                return jsonify(error=error), 400
    policy = service.policy()
    import json
    return render_template('automatic_publishing.html', policy=policy,
        required=service.REQUIRED_FIELDS, selected=json.loads(policy.required_fields_json), error=error, form=FlaskForm()), 400 if error else 200


@publishing.get('/publishing/queue')
def queue():
    from app.operations_views import queue, local_time
    return render_template('publishing_queue.html', workspace=queue(), local_time=local_time)


@publishing.get('/operations/events')
def events():
    from app.operations_views import events, local_time
    return render_template('publishing_events.html', workspace=events(), local_time=local_time, detail=None)


@publishing.get('/operations/events/<operation_id>')
def event_detail(operation_id):
    from app.operations_views import event_query, event_row, local_time
    from app.models import CatalogueOperation
    query, _ = event_query()
    pair = query.filter(CatalogueOperation.id == operation_id).first()
    if not pair: abort(404)
    return render_template('publishing_events.html', workspace=None, local_time=local_time, detail=event_row(*pair))


@publishing.route('/publishing/scan/<int:product_id>', methods=['GET', 'POST'])
def scan_guidance(product_id):
    from app.metadata_save import source
    from app.metadata_workspace import _catalogue_root
    from app.utils.operation_control import acquire_catalogue_operation, CatalogueOperationActive
    from app.utils.scan_runner import start_scan
    from flask import current_app
    import uuid
    product = db.session.get(Product, product_id)
    if not product: abort(404)
    try:
        shared, _, _ = source(product, 'shared')
        root = _catalogue_root().resolve(strict=True)
        collection = shared.parent.resolve(strict=True)
        relative = collection.relative_to(root).as_posix()
        folder = (root / product.source_relpath).resolve(strict=True)
        folder.relative_to(collection)
        if relative == '.' or not shared.is_file(): raise ValueError()
    except (ValueError, OSError, RuntimeError, TypeError, AttributeError):
        return render_template('publishing_scan.html', product=product, relative=None,
            error='Containing-collection context is missing or unsafe. Restore its source mapping; no full-catalogue fallback is permitted.', form=FlaskForm()), 409
    if request.method == 'POST':
        if request.form.get('confirmed') != 'yes': abort(400)
        scope = {'scope_kind': 'collection', 'collection_relpath': relative, 'exhaustive': True, 'product_id': product.id}
        try:
            operation = acquire_catalogue_operation('shared_collection_update', scope)
        except CatalogueOperationActive:
            return render_template('publishing_scan.html', product=product, relative=relative,
                error='Another catalogue operation already owns the scan slot. Wait for its receipt; no duplicate scan was started.', form=FlaskForm()), 409
        start_scan(current_app._get_current_object(), uuid.uuid4().hex, scan_mode='shared_collection',
            operation_id=operation.id, scope=scope, collection_relpath=relative)
        return redirect(url_for('main.operation_detail', operation_id=operation.id))
    return render_template('publishing_scan.html', product=product, relative=relative, error=None, form=FlaskForm())


@publishing.post('/products/<int:product_id>/sync-now')
def sync_now(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.catalogue_status != 'active':
        abort(404)
    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
    if request.is_json and not isinstance(data, dict):
        return jsonify(error='A Sync Now acknowledgement object is required.'), 400
    try:
        job = service.sync_now(product, actor_id=current_user.id,
            acknowledged=data.get('acknowledged') is True if request.is_json else data.get('acknowledged') == 'yes')
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 409
    return (jsonify(product_id=product.id, state=job.state, queue_url=url_for('publishing.queue')), 202) if request.is_json else redirect(url_for('publishing.queue'))


@publishing.post('/publishing/checkpoint/<int:product_id>')
def validate_checkpoint(product_id):
    from app.publishing_checkpoint import validate
    from app.utils.operation_control import CatalogueOperationActive
    product = db.session.get(Product, product_id)
    if not product: abort(404)
    try:
        operation_id = validate(product, actor_id=current_user.id,
            acknowledged=request.form.get('confirmed') == 'yes')
    except (ValueError, RuntimeError, OSError, CatalogueOperationActive) as exc:
        return render_template('publishing_scan.html', product=product, relative=None,
            error=str(exc), form=FlaskForm()), 409
    return redirect(url_for('main.operation_detail', operation_id=operation_id))
