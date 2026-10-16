"""Private local packaging tools. No shipping profile, carrier or order mutations."""
from datetime import datetime
import json
import uuid
from flask import Blueprint, abort, jsonify, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from sqlalchemy import or_
from app import db
from app.models import Product, Variation, ShopifyOrder
from app.packaging_models import Package, ShippingRates, PackingRecord, PackagingEvidence
from app import packaging_catalogue as catalogue, packaging_workspace as workspace
from app import shopify_packaging

packaging=Blueprint('packaging',__name__,url_prefix='/packaging')


@packaging.before_request
@login_required
def administrator():
    if not current_user.is_admin:abort(403)
    request.max_content_length=256*1024


@packaging.after_request
def private(response):
    response.headers['Cache-Control']='no-store, private'
    response.headers['X-Robots-Tag']='noindex, nofollow'
    return response


def body():
    if not FlaskForm().validate_on_submit():abort(400)
    value=request.get_json(silent=True)
    if not isinstance(value,dict):raise ValueError('Send a JSON object.')
    return value


@packaging.errorhandler(ValueError)
def invalid(error):return jsonify(error=str(error)),400


@packaging.get('/')
def index():
    q=request.args.get('q','')[:191].casefold()
    rows=[p for p in workspace.packages() if q in p['name'].casefold()]
    sort=request.args.get('sort','name')
    if sort=='readiness':rows.sort(key=lambda p:(p['readiness'],p['name']))
    if sort=='weight':rows.sort(key=lambda p:(p['tare_g'] is None,float(p['tare_g'] or 0),p['name']))
    return render_template('packaging/index.html',packages=rows,capability=shopify_packaging.current(),form=FlaskForm(),rates=workspace.rates())


@packaging.get('/choices')
def choices():
    return jsonify(packages=[{k:v for k,v in p.items() if k!='links'} for p in workspace.packages() if not p['archived']])


@packaging.route('/new',methods=['GET','POST'])
def new():
    if request.method=='GET':return render_template('packaging/edit.html',package=None)
    row=catalogue.save(body())
    return jsonify(id=row.id,name=row.name,url=url_for('packaging.edit',package_id=row.id)),201


@packaging.route('/<package_id>/edit',methods=['GET','POST'])
def edit(package_id):
    row=db.session.get(Package,package_id)
    if not row:abort(404)
    if request.method=='GET':return render_template('packaging/edit.html',package=catalogue.view(row))
    value=body();version=value.get('version')
    if type(version) is not int:raise ValueError('Reload this package before saving.')
    catalogue.save(value,row,version=version)
    return jsonify(id=row.id,name=row.name,url=url_for('packaging.edit',package_id=row.id))


@packaging.post('/<package_id>/duplicate')
def duplicate(package_id):
    body();row=db.session.get(Package,package_id)
    if not row:abort(404)
    value=catalogue.view(row);value['name']+=' (copy)'
    copy=catalogue.save(value)
    return jsonify(id=copy.id,url=url_for('packaging.edit',package_id=copy.id))


@packaging.post('/<package_id>/archive')
def archive(package_id):
    value=body();row=db.session.get(Package,package_id)
    if not row:abort(404)
    if value.get('confirm') is not True:raise ValueError('Confirm archive after inspecting assigned products. Shopify packages are not changed.')
    if value.get('version')!=row.version:raise ValueError('Package changed; review again.')
    count=db.session.query(Package).filter_by(id=row.id,version=row.version).update({Package.archived:True,Package.version:row.version+1})
    if count!=1:raise ValueError('Concurrent package change; reload.')
    db.session.commit();return jsonify(archived=True)


@packaging.get('/<package_id>/usage')
def usage(package_id):
    row=db.session.get(Package,package_id)
    if not row:abort(404)
    assignments=workspace.dependencies(row.id)
    history=PackingRecord.query.order_by(PackingRecord.created_at.desc()).limit(100).all()
    plans=[{'id':r.id,'created_at':r.created_at} for r in history if any(p['package_id']==row.id for p in json.loads(r.result).get('parcels',[]))]
    return render_template('packaging/usage.html',package=catalogue.view(row),assignments=assignments,plans=plans)


@packaging.post('/capabilities')
def capabilities():
    if not FlaskForm().validate_on_submit():abort(400)
    try:
        shopify_packaging.refresh()
        flash('Read-only capability evidence refreshed. No shipping settings changed.','success')
    except (shopify_packaging.ShopifyError,ValueError):
        # No credentials or response payloads in flash/logging.
        flash('Capability check unavailable. Check the existing Shopify connection and permissions. No shipping settings changed.','warning')
    return redirect(url_for('packaging.index'))


@packaging.route('/rates',methods=['GET','POST'])
def rate_table():
    if request.method=='GET':return render_template('packaging/rates.html',rates=workspace.rates())
    from app.shipping_rates import validate
    value=body();value.pop('version',None)
    validate(value)
    row=ShippingRates(id=str(uuid.uuid4()),configuration=catalogue.dumps(value))
    db.session.add(row);db.session.commit()
    return jsonify(id=row.id,message='New local rate revision saved. Shopify rates unchanged.')


@packaging.get('/products')
def product_search():
    q=request.args.get('q','')[:100]
    products=Product.query.filter_by(catalogue_status='active')
    if q:products=products.filter(or_(Product.sku.ilike('%'+q+'%'),Product.title.ilike('%'+q+'%'),
        Product.id.in_(db.session.query(Variation.product_id).filter(Variation.sku.ilike('%'+q+'%')))))
    rows=[]
    for p in products.order_by(Product.title,Product.id).limit(20):
        if p.product_type=='variable':
            variants=Variation.query.filter_by(product_id=p.id,catalogue_status='active').order_by(Variation.id).limit(100).all()
        else:variants=[None]
        for v in variants:
            rows.append({'product_id':p.id,'variant_id':v.id if v else None,'title':p.title,
                'sku':v.sku if v else p.sku,'options':dict((a.name,a.value) for a in v.attributes) if v else {}})
    return jsonify(products=rows)


@packaging.get('/simulator')
def simulator():return render_template('packaging/simulator.html')


@packaging.post('/simulate')
def simulate():
    value=body();estimated=value.get('allow_estimated',False)
    if type(estimated) is not bool:raise ValueError('Estimated analysis must be explicitly selected.')
    return jsonify(workspace.simulate(workspace.selected(value.get('lines')),allow_estimated=estimated))


@packaging.post('/recommend')
def recommend():
    value=body();document=value.get('document')
    if not isinstance(document,dict):raise ValueError('Enter the current local draft.')
    from app.packaging_contract import effective
    from app.product_defaults import effective as defaults
    item=effective(document,value.get('attributes'))
    item.update(identity='unsaved-draft',sku='Local draft',quantity=value.get('quantity',1),title=document.get('title','Local draft'),
        requires_shipping=defaults(document)[0]['requires_shipping'])
    item.setdefault('weight_g',document.get('weight'))
    from app.packing import plan
    # An explicit recommendation ignores the solo selection for comparison only.
    item.pop('selected_package_id',None)
    return jsonify(plan([item],workspace.packages(),rate_configuration=workspace.rates()))


@packaging.post('/orders/<int:order_id>/suggest')
def order_suggestion(order_id):
    body();order=db.session.get(ShopifyOrder,order_id)
    if not order or order.redacted_at:abort(404)
    lines,warnings=workspace.order_lines(order)
    if warnings:return jsonify(state='insufficient_information',warnings=warnings,parcels=[])
    return jsonify(workspace.simulate(lines,order_id=order.id))
