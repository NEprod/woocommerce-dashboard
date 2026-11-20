import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, update

from app import db
from app.models import Collection, Product, ShopifyProductIdentity
from . import domain, publish
from .models import Promotion
from .shopify import Configuration, ShopifyPromotionError

promotions = Blueprint('promotions',__name__,url_prefix='/promotions')


@promotions.before_request
@login_required
def administrator():
    if not current_user.is_admin:
        abort(403)
    request.max_content_length = 128 * 1024


def default():
    return dict(name='',kind='exact',method='automatic',scope='product',intent='draft',starts=None,ends=None,
                targets=[],exclusions=[],rewards=[],combines={},tiers=[{'qty':2,'total':900}],repeat=True)


def _number(form,name,default=None):
    raw = form.get(name)
    if not raw:
        return default
    if not raw.isascii() or not raw.isdecimal() or len(raw)>8:
        raise ValueError(f'Enter a whole number for {name}.')
    return int(raw)


def _selection(raw):
    try:
        return json.loads(raw)
    except (ValueError,TypeError):
        raise ValueError('A selected target could not be read. Select it again.') from None


def _from_form(form):
    kind, method = form.get('kind'), form.get('method')
    data = {k:form.get(k) for k in ('name','kind','method','scope','intent','starts','ends','code','segment')}
    data.update(targets=[_selection(s) for s in form.getlist('targets')],
                exclusions=[_selection(s) for s in form.getlist('exclusions')],
                combines={k:k in form for k in domain.COMBINES})
    if method=='code':
        data.update(usage=_number(form,'usage'),once='once' in form)
    if kind=='exact':
        tiers = []
        quantities, prices = form.getlist('tier_qty'), form.getlist('tier_total')
        if quantities or prices:
            if len(quantities) != len(prices) or len(quantities) > 8:
                raise ValueError('Enter no more than eight complete quantity tiers.')
            for qty, price in zip(quantities, prices):
                if not price.strip():
                    continue
                if not qty.isascii() or not qty.isdecimal():
                    raise ValueError('Enter a whole-number tier quantity.')
                tiers.append({'qty':int(qty),'total':domain.minor(price,'Tier price')})
        else:
            for line in form.get('tiers','').splitlines():
                if not line.strip():
                    continue
                qty, sep, total = line.partition('=')
                if not sep or not qty.strip().isascii() or not qty.strip().isdecimal() or len(qty.strip())>2:
                    raise ValueError('Enter each tier as quantity=total, for example 2=9.00.')
                tiers.append({'qty':int(qty),'total':domain.minor(total,'Tier price')})
        data.update(tiers=tiers,repeat='repeat' in form)
    elif kind=='bxgy':
        data.update(buy=_number(form,'buy',2),get=_number(form,'get',1),limit=_number(form,'limit',1),
                    value=domain.minor(form.get('reward_percent','100'),'Reward percent',10000),
                    rewards=[_selection(s) for s in form.getlist('rewards')])
    elif kind in ('percent','fixed'):
        data.update(value=domain.minor(form.get('amount',''),'Discount amount',10000 if kind=='percent' else 1000000),
                    minimum=_number(form,'minimum',1))
    return domain.definition(data)


def _uk(value):
    return datetime.fromisoformat(value).astimezone(domain.UK).strftime('%Y-%m-%d %H:%M %z') if value else ''


def _local_status(data, row):
    if row.state in ('pending','uncertain','attention','migration_review'):
        return 'Needs attention'
    if data['intent'] != 'live':
        return data['intent'].title()
    return {'EXPIRED':'Expired','SCHEDULED':'Scheduled','ACTIVE':'Live'}[publish._phase(data)]


@promotions.get('/')
def index():
    page = request.args.get('page',1,type=int)
    promotions_page = Promotion.query.order_by(Promotion.updated_at.desc(),Promotion.id).paginate(page=max(1,page),per_page=30,error_out=False)
    rows = [(row,data,_local_status(data,row),publish.receipt(row)) for row in promotions_page.items if (data:=publish.document(row))]
    ready = False
    try:
        ready = Configuration.from_env() is not None
    except ShopifyPromotionError:
        pass
    return render_template('promotions/index.html',rows=rows,pagination=promotions_page,kinds=domain.KINDS,uk=_uk,money=domain.money,ready=ready)


def _render_edit(row, data, *, error=None, status=200):
    published = publish.receipt(row).get('published') if row else None
    choices = [('draft','Draft — local only'),('live','Live or Scheduled — review to publish'),
               ('disabled','Disabled — review to turn off'),('archived','Archived — retain history')]
    return render_template('promotions/edit.html', row=row,d=data,error=error,kinds=domain.KINDS,changes=choices,
        money=domain.money,uk=_uk,published=published),status


@promotions.route('/new',methods=['GET','POST'])
@promotions.route('/<ident>/edit',methods=['GET','POST'])
def edit(ident=None):
    row = db.get_or_404(Promotion,ident) if ident is not None else None
    data = publish.document(row) if row else default()
    if request.method=='GET':
        return _render_edit(row,data)
    if row and row.state in ('pending','uncertain'):
        return _render_edit(row,data,error='Resolve the retained publication attempt first.',status=409)
    if row and row.state == "migration_review" and request.form.get("review_detached_targets") != "yes":
        return _render_edit(row,data,error="Confirm the detached promotion selections have been reviewed.",status=409)
    try:
        data = _from_form(request.form)
    except ValueError as exc:
        # Keep entered fields and selections after a validation error.
        current = {**data, **{k:request.form.get(k,'') for k in ('name','kind','method','scope','intent','code','segment')}}
        for key in ('starts','ends','tiers','amount','minimum','buy','get','limit','reward_percent','usage'):
            current[key+'_text'] = request.form.get(key,'')
        for key in ('repeat','once'):
            current[key] = key in request.form
        current['tier_rows'] = list(zip(request.form.getlist('tier_qty'),request.form.getlist('tier_total')))
        current['combines'] = {key:key in request.form for key in domain.COMBINES}
        for key in ('targets','rewards','exclusions'):
            current[key] = []
            for value in request.form.getlist(key)[:domain.MAX_VARIANTS]:
                try:
                    current[key].extend(domain._targets([_selection(value)]))
                except ValueError:
                    continue
        return _render_edit(row,current,error=str(exc),status=400)
    if row:
        revision = request.form.get('revision',type=int)
        migrated = {"state": "local"} if row.state == "migration_review" else {}
        changed = db.session.execute(update(Promotion).where(Promotion.id==row.id,Promotion.revision==revision,
            Promotion.state.not_in(('pending','uncertain'))).values(document=domain.canonical(data),
            revision=Promotion.revision+1,updated_at=publish.stamp(),**migrated)).rowcount
        if changed!=1:
            db.session.rollback()
            return _render_edit(row,data,error='This promotion changed in another session. Reopen it before saving.',status=409)
    else:
        row = Promotion(id=uuid4().hex,document=domain.canonical(data),updated_at=publish.stamp())
        db.session.add(row)
    db.session.commit()
    flash('Saved locally. No Shopify changes were made.','success')
    return redirect(url_for('promotions.edit',ident=row.id),code=303)


@promotions.post('/<ident>/duplicate')
def duplicate(ident):
    row = db.get_or_404(Promotion,ident)
    if row.state == "migration_review":
        flash("Review the detached promotion selections before duplicating this promotion.", "warning")
        return redirect(url_for("promotions.edit", ident=row.id), code=303)
    data = publish.document(row)
    data.update(name=data['name'][:85] + ' (copy)',intent='draft')
    copy = Promotion(id=uuid4().hex,document=domain.canonical(data),updated_at=publish.stamp())
    db.session.add(copy)
    db.session.commit()
    return redirect(url_for('promotions.edit',ident=copy.id),code=303)


@promotions.get('/catalogue')
def search():
    query = request.args.get('q','').strip()[:90]
    if len(query)<2:
        return jsonify(products=[],collections=[])
    products = Product.query.filter(Product.catalogue_status=='active',Product.source_relpath.is_not(None),
        or_(Product.title.icontains(query,autoescape=True),Product.sku.icontains(query,autoescape=True),
            Product.variations.any(sku=query))).order_by(Product.title).limit(16).all()
    collections = Collection.query.filter(Collection.source_relpath.is_not(None),
        Collection.name.icontains(query,autoescape=True)).order_by(Collection.name).limit(8).all()
    try:
        config = Configuration.from_env()
    except ShopifyPromotionError:
        config = None
    results = []
    for product in products:
        base = {'id':product.id,'source':product.source_relpath}
        links = ShopifyProductIdentity.query.filter_by(product_id=product.id,state='verified',verification_state='verified').limit(3).all()
        store = config.store if config else links[0].store_id if len(links)==1 else None
        try:
            fixed = domain.product_choices(product,store) if store else []
        except ValueError:
            fixed = []
        options = sorted({(a.name,a.value) for v in product.variations if v.catalogue_status=='active' for a in v.attributes})
        results.append({'name':product.title,'sku':product.sku,'options':[
            {'label':'All resolved variants','target':dict(base,mode='product')},
            *({'label':f'{name} = {value}','target':dict(base,mode='option',name=name,value=value)} for name,value in options[:100]),
            *({'label':v['sku'] + (' · ' + ', '.join(v['options'].values()) if v['options'] else ''),
              'target':dict(base,mode='fixed',sku=v['sku'],gid=v['id'])} for v in fixed if v['id'])
        ]})
    return jsonify(products=results,collections=[{'name':c.name,'target':{'mode':'collection','id':c.id,'source':c.source_relpath}} for c in collections])


def _local_rows(d, adapter):
    if d['scope']!='product' or not d['targets']:
        return []
    if adapter:
        store = adapter.config.store
    else:
        # Offline local review never calls the primary application's connection.
        stores = db.session.query(ShopifyProductIdentity.store_id).filter_by(state='verified',verification_state='verified').distinct().limit(2).all()
        store = stores[0][0] if len(stores)==1 else None
    if not store:
        raise ValueError('A single verified Shopify store identity is needed to preview the qualifying variants.')
    return domain.resolve(d['targets'],store,d.get('exclusions',[]))


def _costs(rows):
    from app.inventory.product_bom import product_costs
    products = {}
    for r in rows:
        if r['local_id'] not in products:
            try:
                products[r['local_id']] = product_costs(db.session.get(Product,r['local_id']))
            except (ValueError,OSError):
                products[r['local_id']] = {}
        value = products[r['local_id']].get(r['sku'],{}).get('shopify')
        if value is not None:
            r['cost'] = int((Decimal(value)*100).quantize(Decimal(1),rounding=ROUND_HALF_UP))
    return rows


@promotions.route('/<ident>/review',methods=['GET','POST'])
def review(ident):
    row = db.get_or_404(Promotion,ident)
    d, proof = publish.document(row), publish.receipt(row)
    plan, token, error, rows, adapter = None,None,None,[],None
    try:
        adapter = publish.client()
        if request.method=='POST':
            action = request.form.get('action')
            if action=='confirm' and request.form.get('ack')=='yes' and adapter:
                publish.confirm(row,request.form.get('approval',''),current_user.id,adapter)
                flash('Shopify configuration verified. Checkout has not been tested by this operation.','success')
                return redirect(url_for('promotions.review',ident=ident),code=303)
            if action=='reconcile' and adapter:
                publish.reconcile(row,adapter)
                flash('Configuration verified by readback; no mutation was replayed.','success')
                return redirect(url_for('promotions.review',ident=ident),code=303)
            raise ShopifyPromotionError('Explicit approval and a verified Promotions development connection are required.')
        if adapter is None:
            raise ShopifyPromotionError('Development connection is not configured. Publish is blocked; local definitions remain available.')
        plan = publish.build(row,adapter)
        token = publish.approval(plan,current_user.id)
        rows = plan['rows']
    except ValueError as exc:
        error = str(exc)
        try:
            rows = _local_rows(d,adapter)
        except ValueError as local:
            error += ' ' + str(local)
        proof = publish.receipt(row)
    prior = proof.get('published')
    admin_url = 'https://' + adapter.config.domain + '/admin/discounts' if adapter and prior and prior['shop']==adapter.config.store else None
    return render_template('promotions/review.html',row=row,d=d,plan=plan,approval=token,error=error,rows=_costs([dict(r) for r in rows]),proof=proof,
        kinds=domain.KINDS,money=domain.money,uk=_uk,admin_url=admin_url), (409 if error and request.method=='POST' else 200)
