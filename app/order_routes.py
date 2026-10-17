"""Session/admin protected local reads; the only POST queues read-only import."""
import json
import re
from flask import Blueprint, abort, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from app import db
from app.models import ShopifyOrder, ShopifyOrderSync
from app import orders_workspace as workspace

orders=Blueprint('orders',__name__,url_prefix='/orders')


@orders.before_request
@login_required
def administrator():
    if not current_user.is_admin: abort(403)


@orders.after_request
def protect(response):
    response.headers['Cache-Control']='no-store, private'
    response.headers['X-Robots-Tag']='noindex, nofollow'
    return response


@orders.app_template_filter('order_uk_time')
def order_uk_time(value):
    return workspace.uk_time(value)


@orders.get('/')
def index():
    return render_template('orders/index.html',**workspace.listing(request.args),form=FlaskForm())


@orders.get('/<int:order_id>')
def detail(order_id):
    order=db.session.get(ShopifyOrder,order_id)
    if not order: abort(404)
    destination=None
    # This domain was validated against this authenticated Shop at import, not
    # taken from a customer property, current browser or mutable product title.
    source=db.session.get(ShopifyOrderSync,order.store_id)
    if source and re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com',source.domain):
        destination='https://'+source.domain+'/admin/orders/'+order.remote_id.rsplit('/',1)[-1]
    from app.order_models import ShopifyOrderEvent
    incomplete=ShopifyOrderEvent.query.filter(ShopifyOrderEvent.store_id==order.store_id,
        ShopifyOrderEvent.order_gid==order.remote_id,ShopifyOrderEvent.state.in_(['incomplete','dead_letter','expired'])).count()
    return render_template('orders/detail.html',order=order,reconciliation_needed=bool(incomplete),
        lines=[workspace.line_view(l) for l in order.lines],money=json.loads(order.money_json),
        shipping=json.loads(order.shipping_json),address=json.loads(order.delivery_json),shopify_url=destination)


@orders.post('/sync')
def sync_now():
    if not FlaskForm().validate_on_submit(): abort(400)
    from app.scheduled_tasks import enqueue, ORDERS_KEY
    enqueue(ORDERS_KEY,manual=True,orders_rescan=request.form.get('rescan')=='recent')
    flash('Read-only Orders sync queued. Import continues even if this page closes.','info')
    return redirect(url_for('orders.index'))


@orders.get('/status')
def status():
    value=workspace.sync_status()
    # No PII, order payloads or credentials in polling responses.
    return jsonify(label=value['label'],active=bool(value['run_id']),message=value['message'],
        last_successful=workspace.uk_time(value['last_successful']),count=value['imported_orders'])
