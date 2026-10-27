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
    query = ShopifyPublishJob.query
    state = request.args.get('state', '')
    if state:
        query = query.filter_by(state=state)
    page = query.order_by(ShopifyPublishJob.priority.desc(), ShopifyPublishJob.updated_at.desc()).paginate(
        page=request.args.get('page', 1, type=int), per_page=50, error_out=False)
    products = {p.id: p for p in Product.query.filter(Product.id.in_([j.product_id for j in page.items])).all()}
    import json
    from app.sale_editor import uk_time
    event_page = ShopifyPublishingEvent.query.order_by(ShopifyPublishingEvent.occurred_at.desc()).paginate(
        page=request.args.get('event_page', 1, type=int), per_page=20, error_out=False)
    return render_template('automatic_publishing.html', policy=policy, jobs=page, products=products,
        required=service.REQUIRED_FIELDS, selected=json.loads(policy.required_fields_json), error=error, form=FlaskForm(),
        events=event_page, receipts={event.id: json.loads(event.receipt_json) for event in event_page.items},
        uk_time=lambda stamp: uk_time(stamp.isoformat()) if stamp else 'Not yet'), 400 if error else 200


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
    return (jsonify(product_id=product.id, state=job.state, queue_url=url_for('publishing.index')), 202) if request.is_json else redirect(url_for('publishing.index'))
