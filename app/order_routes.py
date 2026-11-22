"""Session/admin protected local reads; the only POST queues read-only import."""
import json
import re
from uuid import uuid4
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import Blueprint, Response, abort, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from app import db
from app.models import ShopifyOrder, ShopifyOrderSync, Settings
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
    if not workspace.order_allowed(order):abort(403,'Test orders are disabled in Settings → Shopify Orders.')
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


@orders.get('/<int:order_id>/lines/<int:line_id>/photo/<field>')
def customer_photo(order_id, line_id, field):
    from app import customer_photos as photos
    from app.models import ShopifyOrderLine
    line=db.session.get(ShopifyOrderLine,line_id)
    if not photos.ID.fullmatch(field) or not line or line.order_id!=order_id or line.order.redacted_at:
        abort(404)
    personal=json.loads(line.personalisation_json)
    ref=photos.references(personal).get(field,'')
    if not photos.REF.fullmatch(ref) or ref not in personal.get('photo_verified',[]):
        abort(404)
    try: stored=photos.download(line.order,line,ref)
    except photos.Unavailable: abort(503,'Private photo storage is unavailable.')
    if stored is None: abort(404)
    data,mime=stored
    return Response(data,mimetype=mime,headers={
        'Content-Disposition':'attachment; filename="customer-photo.'+photos.MIMES[mime]+'"',
        'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})


@orders.get('/status')
def status():
    value=workspace.sync_status()
    # No PII, order payloads or credentials in polling responses.
    return jsonify(label=value['label'],active=bool(value['run_id']),message=value['message'],
        last_successful=workspace.uk_time(value['last_successful']),count=value['imported_orders'])


@orders.route('/settings',methods=['GET','POST'])
def settings():
    from app import order_work as work
    form=FlaskForm();error=None
    if request.method=='POST':work.stock.writer_lock()
    active=work.OrderWorkLine.query.join(ShopifyOrder,ShopifyOrder.id==work.OrderWorkLine.order_id).filter(
        ShopifyOrder.test.is_(True),work.OrderWorkLine.state.notin_(['cancelled','dispatched','needs_configuration'])).count()
    if request.method=='POST':
        if not form.validate_on_submit():abort(400)
        enabled=request.form.get('include_test')=='yes'
        if not enabled and active and request.form.get('acknowledge')!='yes':
            error='Unfinished test work will be hidden. Confirm that its reservations and historical stock movements will be retained.'
        else:
            work.stock.writer_lock()
            saved=Settings.query.order_by(Settings.id).first()
            if saved is None:saved=Settings();db.session.add(saved)
            saved.include_test_orders=enabled;db.session.flush()
            if enabled:
                for order in ShopifyOrder.query.filter_by(test=True).all():work.reconcile(order)
            db.session.commit();flash('Global order eligibility saved. No stock transactions were reversed and no Shopify setting was changed.','success')
            return redirect(url_for('orders.settings'),code=303)
    return render_template('orders/index.html',order_settings=True,include_test=enabled if error else workspace.include_test_orders(),active_test_work=active,form=form,error=error),400 if error else 200


def selected_orders(ids):
    try:identities=sorted(set(int(i) for i in ids))
    except (ValueError,TypeError):abort(400,'Choose valid saved orders.')
    if not identities or len(identities)>100:abort(400,'Choose between 1 and 100 visible orders.')
    rows=workspace.operational_query().filter(ShopifyOrder.id.in_(identities)).order_by(ShopifyOrder.created_at,ShopifyOrder.id).all()
    if len(rows)!=len(identities):abort(400,'Some selected orders are unavailable or excluded by the global test-order setting.')
    return rows


@orders.post('/print')
def bulk_print():
    if not FlaskForm().validate_on_submit():abort(400)
    from app import order_work as work
    rows=selected_orders(request.form.getlist('order_id'))
    selection=URLSafeTimedSerializer(current_app.secret_key,salt='order-print').dumps(dict(
        ids=[o.id for o in rows],request=uuid4().hex,actor=current_user.id))
    return render_template('order_work/sheet.html',sheets=[dict(order=o,lines=[work.line_view(l) for l in o.lines if l.is_current and l.current_quantity>0],info=workspace.production_information(o)) for o in rows],
        form=FlaskForm(),selection=selection,auto_print=True,qty=work.stock.quantity_text,labels=work.LABELS,workflow_types=work.TYPES)


@orders.post('/printed')
def printed():
    if not FlaskForm().validate_on_submit():abort(400)
    if request.form.get('confirmed')!='yes':abort(400,'Explicit physical print confirmation is required.')
    try:data=URLSafeTimedSerializer(current_app.secret_key,salt='order-print').loads(request.form.get('selection',''),max_age=7200)
    except BadSignature:abort(400,'Print selection expired or changed. Reopen the print preview.')
    if data['actor']!=current_user.id:abort(403)
    from app import order_work as work
    rows=selected_orders(data['ids'])
    try:work.confirm_print(rows,data['request'],f'{current_user.id}:{current_user.username}');db.session.commit()
    except ValueError as error:db.session.rollback();abort(400,str(error))
    flash('Selected orders marked Printed. Existing proof, picking and production stages are unchanged.','success')
    return redirect(url_for('orders.index'),code=303)
