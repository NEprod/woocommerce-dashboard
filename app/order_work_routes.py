"""One Order Fulfilment workspace attached to the existing imported orders."""
from io import BytesIO
from uuid import uuid4
import json
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, Response, url_for, current_app
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError, OperationalError
from app import db
from app.models import ShopifyOrder, ShopifyOrderLine
from app.order_work_models import WorkPlan, WorkOutbound
from app import order_work as work
from app import orders_workspace as workspace

fulfilment=Blueprint('fulfilment',__name__,url_prefix='/production/orders')


@fulfilment.before_request
@login_required
def admin():
    if not current_user.is_admin:abort(403)
    request.max_content_length=128*1024


@fulfilment.after_request
def private(response):
    response.headers.update({'Cache-Control':'private, no-store','X-Robots-Tag':'noindex, nofollow','Referrer-Policy':'no-referrer'})
    return response


@fulfilment.context_processor
def helpers():
    return dict(labels=work.LABELS,workflow_types=work.TYPES,qty=work.stock.quantity_text,
                action_key=lambda:uuid4().hex,form=FlaskForm())


@fulfilment.get('/')
def index():
    code=request.args.get('reference','').strip()
    if code:
        matches=work.scan(code)
        if len(matches)==1:return redirect(url_for('fulfilment.detail',order_id=matches[0].id))
    else:
        query=workspace.operational_query().filter(ShopifyOrder.cancelled_at.is_(None),ShopifyOrder.outstanding_quantity>0)
        matches=query.order_by(ShopifyOrder.created_at.desc()).limit(100).all()
    return render_template('order_work/index.html',orders=matches,reference=code,summary=work.summary)


@fulfilment.get('/<int:order_id>')
def detail(order_id):
    order=db.get_or_404(ShopifyOrder,order_id)
    if not workspace.order_allowed(order):abort(403,'Test orders are disabled in Settings → Shopify Orders.')
    # Older imported snapshots are prepared once; repeat scans never reset work.
    missing=[l for l in order.lines if l.is_current and l.current_quantity>0 and not db.session.get(work.OrderWorkLine,l.id)]
    if missing:
        work.stock.writer_lock()
        for line in missing:work.initialise(line,actor=f'{current_user.id}:{current_user.username}')
        work.reconcile(order)  # Existing external fulfilments must not restart physical work.
        db.session.commit()
    return render_template('order_work/detail.html',order=order,summary=work.summary(order),
        info=workspace.production_information(order),printed=work.print_history(order),
        lines=[work.line_view(line) for line in order.lines if line.is_current or db.session.get(work.OrderWorkLine,line.id)],
        deliveries=WorkOutbound.query.join(work.WorkEvent,WorkOutbound.event_id==work.WorkEvent.id).join(work.OrderWorkLine,
            work.WorkEvent.line_id==work.OrderWorkLine.line_id).filter(work.OrderWorkLine.order_id==order_id).order_by(WorkOutbound.id.desc()).limit(50).all())


@fulfilment.post('/<int:order_id>/line/<int:line_id>/<action>')
def action(order_id,line_id,action):
    if not FlaskForm().validate_on_submit():abort(400)
    line=db.get_or_404(ShopifyOrderLine,line_id)
    if line.order_id!=order_id:abort(404)
    data={k:v for k,v in request.form.items() if k not in {'csrf_token','request_id','version'}}
    try:
        result=work.act(line_id,action,data,request.form.get('request_id'),
            actor=f'{current_user.id}:{current_user.username}',version=request.form.get('version') or None)
        db.session.commit();flash(f'{work.LABELS[result.state]}. Local records saved; Shopify and stock change only as explicitly indicated.','success')
    except (ValueError,TypeError,IntegrityError,OperationalError) as error:
        db.session.rollback()
        message=str(error) if isinstance(error,(ValueError,TypeError)) else 'Concurrent update or database conflict. Reload and review; no partial operation was committed.'
        if request.accept_mimetypes.best=='application/json':return jsonify(error=message),400
        flash(message,'warning')
    return redirect(url_for('fulfilment.detail',order_id=order_id),code=303)


@fulfilment.get('/<int:order_id>/sheet')
def sheet(order_id):
    order=db.get_or_404(ShopifyOrder,order_id)
    if not workspace.order_allowed(order):abort(403)
    from itsdangerous import URLSafeTimedSerializer
    selection=URLSafeTimedSerializer(current_app.secret_key,salt='order-print').dumps(dict(ids=[order.id],request=uuid4().hex,actor=current_user.id))
    return render_template('order_work/sheet.html',sheets=[dict(order=order,info=workspace.production_information(order),
        lines=[work.line_view(l) for l in order.lines if l.is_current and l.current_quantity>0])],selection=selection,auto_print=False)


@fulfilment.get('/<int:order_id>/barcode.svg')
def barcode(order_id):
    order=db.get_or_404(ShopifyOrder,order_id)
    if not workspace.order_allowed(order):abort(403)
    from barcode import Code128
    from barcode.writer import SVGWriter
    output=BytesIO();Code128(work.token(order),writer=SVGWriter()).write(output,dict(write_text=False,module_width=.3,module_height=15,quiet_zone=4))
    return Response(output.getvalue(),mimetype='image/svg+xml',headers={'X-Content-Type-Options':'nosniff'})


@fulfilment.get('/replenishment')
def replenishment():
    data=work.stock_health();scope=request.args.get('filter','total')
    if scope not in {'total','healthy','low','out','manufacture','order'}:abort(400)
    rows=data['rows'] if scope=='total' else [r for r in data['rows'] if r['health']==scope or r['action']==scope and r['need']]
    return render_template('order_work/replenishment.html',rows=rows,counts=data['counts'],scope=scope,
        plan_details=lambda plan:json.loads(plan.document),
        plans=WorkPlan.query.order_by(WorkPlan.created_at.desc()).limit(80).all())


@fulfilment.post('/replenishment/<int:item_id>')
def plan(item_id):
    if not FlaskForm().validate_on_submit():abort(400)
    try:
        if request.form.get('confirmed')!='yes':raise ValueError('Confirm this replenishment plan explicitly.')
        row=work.plan_replenishment(item_id,request.form.get('request_id'),actor=f'{current_user.id}:{current_user.username}',
            requirement_id=int(request.form['requirement_id']) if request.form.get('requirement_id') else None)
        db.session.commit();flash('Plan saved. This has not received stock, consumed materials or placed a supplier order.','success')
    except (ValueError,TypeError,IntegrityError,OperationalError) as error:
        db.session.rollback();flash(str(error) if isinstance(error,(ValueError,TypeError)) else 'Concurrent planning conflict; reload and review.','warning')
    return redirect(url_for('fulfilment.replenishment'),code=303)


@fulfilment.post('/replenishment/plan/<plan_id>')
def purchase_receipt(plan_id):
    if not FlaskForm().validate_on_submit():abort(400)
    try:
        if request.form.get('confirmed')!='yes':raise ValueError('Confirm plan reconciliation explicitly.')
        work.reconcile_purchase(plan_id,request.form.get('receipt',''),request.form.get('request_id'),
            actor=f'{current_user.id}:{current_user.username}',reason=request.form.get('reason'),cancel=request.form.get('cancel')=='yes')
        db.session.commit();flash('Purchase plan reconciled against existing receipt evidence. No stock was posted by this action.','success')
    except (ValueError,IntegrityError,OperationalError) as error:
        db.session.rollback();flash(str(error) if isinstance(error,ValueError) else 'Concurrent reconciliation conflict; reload.','warning')
    return redirect(url_for('fulfilment.replenishment'),code=303)
