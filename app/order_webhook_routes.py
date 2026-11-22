"""Private administration of the existing app's order integration, never a receiver."""
import json
from datetime import timedelta
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, current_app
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from app import db
from app import order_integrations as service
from app.order_models import ShopifyOrderEvent, ShopifyOrderDelivery
from app.order_integration_models import OrderWebhookSetting, OrderIntegrationSettings, OrderStatusProjection
from app.order_webhook_security import TOPICS
from app.order_work import now

webhooks=Blueprint('webhooks',__name__,url_prefix='/settings/shopify-webhooks')


@webhooks.before_request
@login_required
def administrator():
    if not current_user.is_admin:abort(403)
    request.max_content_length=32768


@webhooks.after_request
def private(response):
    response.headers.update({'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow'})
    return response


def view(error=None):
    from app.order_webhooks import settings
    config=settings();store=service.connected_store();deployment=current_app.config.get('TLC_DEPLOYMENT_PROOF')
    rows=OrderWebhookSetting.query.filter_by(store_id=store.store_id).order_by(OrderWebhookSetting.topic).all() if store else []
    events=ShopifyOrderEvent.query.filter_by(store_id=store.store_id).order_by(ShopifyOrderEvent.received_at.desc()).limit(100).all() if store else []
    observations={}
    for row in rows:
        recent=[e for e in events if e.topic==row.topic]
        observations[row.id]=dict(received=recent[0].received_at if recent else None,
            processed=max((e.processed_at for e in recent if e.processed_at),default=None))
    return render_template('order_work/webhooks.html',rows=rows,topics=sorted(TOPICS),store=store,error=error,
        observations=observations,events=events,form=FlaskForm(),ready=all(config.values()),
        deployment_writes=not deployment or deployment[0].writes,
        deployment_automation=not deployment or deployment[0].automation,
        secret_present=bool(config['SECRET']),secret_source='Existing Shopify app client secret' if service.shopify_settings().get('client_secret') else 'Legacy app-signing-secret deployment setting' if config['SECRET'] else 'Unavailable — configure the existing app connection',
        receiver_configured=bool(config['URL']),channel=db.session.get(ShopifyOrderDelivery,'orders'),
        projection=db.session.get(OrderIntegrationSettings,store.store_id) if store else None,
        projections=OrderStatusProjection.query.order_by(OrderStatusProjection.order_id.desc()).limit(40).all(),
        pending=ShopifyOrderEvent.query.filter(ShopifyOrderEvent.state.in_(['pending','incomplete','dead_letter','expired'])).count())


@webhooks.get('')
def index():return view()


@webhooks.post('/save')
def save():
    if not FlaskForm().validate_on_submit():abort(400)
    try:
        service.save(request.form);db.session.commit();flash('Local webhook settings saved. No Shopify subscription was created/updated.','success')
        return redirect(url_for('webhooks.index'),code=303)
    except (ValueError,IntegrityError) as error:
        db.session.rollback();return view(str(error) if isinstance(error,ValueError) else 'Duplicate/concurrent configuration. Reload and review.'),400


@webhooks.post('/<int:record_id>/<action>')
def operation(record_id,action):
    if not FlaskForm().validate_on_submit():abort(400)
    service.stock.writer_lock();db.session.expire_all()
    row=db.get_or_404(OrderWebhookSetting,record_id)
    try:version=int(request.form.get('version','0'))
    except ValueError:return view('Invalid settings revision.'),400
    if row.version!=version:return view('Settings changed; reload before this operation.'),409
    if row.operation_until and row.operation_until>now():return view('A subscription operation is still running; wait before retrying.'),409
    if action not in {'verify','configuration','register','remove','disable'}:abort(404)
    if action in {'register','remove','disable'} and request.form.get('confirmed')!='yes':return view('Explicit confirmation is required.'),400
    try:
        if action=='configuration':
            from app.order_webhooks import settings, validate_configuration
            service.public_endpoint(row.endpoint)
            validate_configuration(settings())
            flash('Local configuration is present. Public endpoint reachability remains unverified until actual authenticated delivery.','info')
        elif action=='disable':row.enabled=False;row.version+=1;db.session.commit();flash('Disabled locally. Remote subscription is unchanged; polling remains separate.','info')
        else:
            if action=='register':
                from app.order_webhooks import settings, validate_configuration
                validate_configuration(settings())
            row.state='checking' if action=='verify' else 'operation_pending';row.error='Remote operation pending; verify before retrying an uncertain outcome.'
            row.operation_until=now()+timedelta(minutes=10);row.version+=1;pending_version=row.version;db.session.commit()
            service.subscription_action(row,action)
            # No other edit may race the network operation or adopt its outcome.
            service.stock.writer_lock()
            from sqlalchemy import select
            current_version=db.session.scalar(select(OrderWebhookSetting.version).where(OrderWebhookSetting.id==record_id).execution_options(autoflush=False))
            if current_version!=pending_version:raise ValueError('Operation lease changed; verify remote state before retrying.')
            row.operation_until=None;row.version+=1;db.session.commit()
            flash('Subscription observation saved. Registration, receipt, processing and reachability are separate conditions.','success')
        return redirect(url_for('webhooks.index'),code=303)
    except Exception as error:
        db.session.rollback();row=db.session.get(OrderWebhookSetting,record_id)
        row.operation_until=None
        row.state='uncertain' if action in {'register','remove'} else 'needs_attention'
        row.error=str(error)[:400] if isinstance(error,ValueError) else 'Existing app API operation failed or was not verified. Review permissions, app-managed subscriptions and readback before retrying.'
        db.session.commit();return view(row.error),400


@webhooks.post('/projection')
def projection():
    if not FlaskForm().validate_on_submit():abort(400)
    try:
        enabled=request.form.get('enabled')=='yes'
        if enabled and request.form.get('confirmed')!='yes':raise ValueError('Explicit status-only mutation authorisation is required.')
        service.enable_projection(enabled,actor=f'{current_user.id}:{current_user.username}');db.session.commit()
        flash('Production-status projection '+('authorised for this verified store only.' if enabled else 'disabled.'),'success')
    except Exception as error:
        db.session.rollback();return view(str(error) if isinstance(error,ValueError) else 'Connection/scope verification failed; no authority granted.'),400
    return redirect(url_for('webhooks.index'),code=303)


@webhooks.post('/projection/<int:order_id>/retry')
def retry_projection(order_id):
    if not FlaskForm().validate_on_submit():abort(400)
    if request.form.get('confirmed')!='yes':abort(400)
    row=db.get_or_404(OrderStatusProjection,order_id)
    if row.owner and row.lease_until and row.lease_until>now():return view('Projection worker is still running.'),409
    row.next_attempt=None;row.value=None;row.state='pending';db.session.commit()
    flash('Read-first status retry queued. No Shopify request occurred in this browser action.','info')
    return redirect(url_for('webhooks.index'),code=303)
