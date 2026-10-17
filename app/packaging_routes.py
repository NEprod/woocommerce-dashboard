"""Private optional solo-packaging tools; no shipping profile or order mutations."""
from flask import Blueprint, abort, jsonify, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from app import db
from app.packaging_models import Package
from app import packaging_catalogue as catalogue, packaging_workspace as workspace, shopify_packaging
from app import package_reconciliation as reconciliation

packaging=Blueprint('packaging',__name__,url_prefix='/packaging')


@packaging.before_request
@login_required
def administrator():
    if not current_user.is_admin: abort(403)
    request.max_content_length=256*1024


@packaging.after_request
def private(response):
    response.headers['Cache-Control']='no-store, private'
    response.headers['X-Robots-Tag']='noindex, nofollow'
    return response


def body():
    if not FlaskForm().validate_on_submit(): abort(400)
    value=request.get_json(silent=True)
    if not isinstance(value,dict): raise ValueError('Send a JSON object.')
    return value


@packaging.errorhandler(ValueError)
def invalid(error): return jsonify(error=str(error)),400


@packaging.get('/')
def index():
    q=request.args.get('q','')[:191].casefold()
    rows=[p for p in workspace.packages() if q in p['name'].casefold()]
    if request.args.get('sort')=='readiness': rows.sort(key=lambda p:(p['readiness'],p['name']))
    if request.args.get('sort')=='weight': rows.sort(key=lambda p:(p['tare_g'] is None,float(p['tare_g'] or 0),p['name']))
    capability=shopify_packaging.current()
    for row in rows:row['shopify_status']=reconciliation.status(row,capability)
    return render_template('packaging/index.html',packages=rows,capability=capability,form=FlaskForm())


@packaging.get('/choices')
def choices():
    return jsonify(packages=[{k:v for k,v in p.items() if k!='links'} for p in workspace.packages() if not p['archived']])


@packaging.route('/new',methods=['GET','POST'])
def new():
    if request.method=='GET': return render_template('packaging/edit.html',package=None,capability=shopify_packaging.current())
    row=catalogue.save(body())
    return jsonify(id=row.id,name=row.name,url=url_for('packaging.edit',package_id=row.id)),201


@packaging.route('/<package_id>/edit',methods=['GET','POST'])
def edit(package_id):
    row=db.session.get(Package,package_id)
    if not row: abort(404)
    if request.method=='GET': return render_template('packaging/edit.html',package={k:v for k,v in catalogue.view(row).items() if k!='links'},capability=shopify_packaging.current())
    value=body(); version=value.get('version')
    if type(version) is not int: raise ValueError('Reload this package before saving.')
    catalogue.save(value,row,version=version)
    return jsonify(id=row.id,name=row.name,url=url_for('packaging.edit',package_id=row.id))


@packaging.post('/<package_id>/duplicate')
def duplicate(package_id):
    body(); row=db.session.get(Package,package_id)
    if not row: abort(404)
    value=catalogue.view(row); value['name']+=' (copy)'
    copy=catalogue.save(value)
    return jsonify(id=copy.id,url=url_for('packaging.edit',package_id=copy.id))


@packaging.post('/<package_id>/archive')
def archive(package_id):
    value=body(); row=db.session.get(Package,package_id)
    if not row: abort(404)
    if value.get('confirm') is not True: raise ValueError('Confirm archive after inspecting assignments. Shopify packages are unchanged.')
    if value.get('version')!=row.version: raise ValueError('Package changed; review again.')
    count=db.session.query(Package).filter_by(id=row.id,version=row.version).update({Package.archived:True,Package.version:row.version+1})
    if count!=1: raise ValueError('Concurrent package change; reload.')
    db.session.commit()
    return jsonify(archived=True)


@packaging.get('/<package_id>/usage')
def usage(package_id):
    row=db.session.get(Package,package_id)
    if not row: abort(404)
    return render_template('packaging/usage.html',package=catalogue.view(row),assignments=workspace.dependencies(row.id))


@packaging.post('/capabilities')
def capabilities():
    if not FlaskForm().validate_on_submit(): abort(400)
    try:
        shopify_packaging.refresh()
        flash('Read-only capability evidence refreshed. No shipping settings changed.','success')
    except (shopify_packaging.ShopifyError,ValueError):
        flash('Capability check unavailable. Check the existing Shopify connection and permissions. No shipping settings changed.','warning')
    return redirect(url_for('packaging.index'))


@packaging.post('/recommend')
def recommend():
    value=body(); document=value.get('document')
    if not isinstance(document,dict): raise ValueError('Enter the current local draft.')
    from app.packaging_contract import effective
    from app.packing import recommend as solo
    item=effective(document,value.get('attributes'))
    from app.utils.json_utils import apply_variation_modifiers
    weight=apply_variation_modifiers(document,value['attributes'],log=lambda *a,**k:None)['weight'] if value.get('attributes') is not None else document.get('weight')
    item.setdefault('weight_g',weight)
    return jsonify(solo(item,workspace.packages()))


@packaging.get('/reconcile')
def reconcile():
    return render_template('packaging/reconcile.html',capability=shopify_packaging.current(),form=FlaskForm())


@packaging.post('/reconcile/compare')
def compare():
    return jsonify(reconciliation.compare(body().get('records')))


@packaging.post('/reconcile/preview')
def reconciliation_preview():
    data=body();plan,token=reconciliation.preview(data.get('records'),data.get('choices'))
    return jsonify(plan=plan,token=token)


@packaging.post('/reconcile/confirm')
def reconciliation_confirm():
    data=body()
    return jsonify(plan=reconciliation.confirm(data.get('token',''),data.get('approve')))


@packaging.post('/sync')
def sync_all():
    if not FlaskForm().validate_on_submit():abort(400)
    ids=request.form.getlist('package_id') or None
    if ids and (len(ids)>reconciliation.LIMIT or any(db.session.get(Package,i) is None for i in ids)):abort(400)
    return render_template('packaging/sync.html',plan=reconciliation.sync_preview(ids),form=FlaskForm())


@packaging.post('/dimension-copy-preview')
def dimension_copy_preview():
    """Unsaved local draft only; never save, scan or request Shopify."""
    value=body(); document=value.get('document')
    if not isinstance(document,dict): raise ValueError('Enter the current local draft.')
    from app.packaging_contract import dimension_configurations
    configurations=dimension_configurations(document)
    if not configurations: raise ValueError('Enter finished product dimensions before copying.')
    return jsonify(configurations=configurations,remote_writes=0,local_writes=0)
