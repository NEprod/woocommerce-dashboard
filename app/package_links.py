"""Manual store-bound saved-package identities. No discovery or Shopify writes."""
import json
import re
import uuid
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from app import db
from app.packaging_models import PackageLink, PackagingEvidence


def validate(value):
    if not isinstance(value,str):raise ValueError('Paste the complete Shopify package GID, not a package name.')
    value=value.strip()
    # The documented scalar is ID; its package object name is not publicly readable.
    # Preserve the actual package GID, never manufacture one from a number or name.
    if len(value)>96 or not re.fullmatch(r'gid://shopify/(?:[A-Za-z][A-Za-z0-9]*)?Package/[1-9][0-9]{0,29}',value):
        raise ValueError('Use the complete gid://shopify/…Package/… identifier copied from your own Shopify Admin. Do not invent or convert an ID.')
    return value


def stores(config,store_id=None):
    from app.models import ShopifyProductIdentity
    values={'domain:'+config.domain}
    if store_id:values.add(store_id)
    values.update(r[0] for r in db.session.query(ShopifyProductIdentity.store_id).filter_by(store_domain=config.domain).distinct())
    return values


def find(package_id,config,store_id=None):
    rows=PackageLink.query.filter(PackageLink.package_id==package_id,PackageLink.store_id.in_(stores(config,store_id))).all()
    if len({r.remote_id for r in rows})>1:raise ValueError('Conflicting retained package identities require explicit review; no assignment is allowed.')
    return rows[0] if rows else None


def current(package_id):
    from app.shopify_drafts import configuration
    try:
        row=find(package_id,configuration())
        return {'id':row.remote_id if row else None,'status':'Entered manually — not remotely verified' if row else 'Shopify Package ID required',
            'revision':row.observed if row else None,'expected':[row.remote_id] if row else []}
    except ValueError as exc:return {'id':None,'status':str(exc),'revision':None,'expected':[]}


def set_id(package,value,expected,*,unlink=False,confirmed=False):
    from app.shopify_drafts import configuration
    config=configuration();scope=stores(config)
    rows=PackageLink.query.filter(PackageLink.package_id==package.id,PackageLink.store_id.in_(scope)).all()
    previous=sorted({r.remote_id for r in rows})
    if not isinstance(expected,list) or any(not isinstance(v,str) for v in expected) or previous!=sorted(expected):raise ValueError('Package link changed; reload before editing it.')
    if unlink:
        if not confirmed:raise ValueError('Confirm unlinking. This does not clear any existing Shopify variant assignment.')
        target=None
    else:target=validate(value)
    if target and PackageLink.query.filter(PackageLink.store_id.in_(scope),PackageLink.remote_id==target,PackageLink.package_id!=package.id).first():
        raise ValueError('This Shopify package ID is already linked to another local package.')
    if previous==([target] if target else []):return current(package.id)
    try:
        count=db.session.query(type(package)).filter_by(id=package.id,version=package.version).update({type(package).version:package.version+1})
        if count!=1:raise ValueError('Package changed; reload before editing the link.')
        if rows:
            db.session.add(PackagingEvidence(key='package-link-history:'+uuid.uuid4().hex,
                value=json.dumps({'package_id':package.id,'links':[{'store':r.store_id,'id':r.remote_id,'evidence':r.observed} for r in rows]}),checked_at=datetime.utcnow()))
            for row in rows:db.session.delete(row)
            db.session.flush()
        if target:db.session.add(PackageLink(package_id=package.id,store_id='domain:'+config.domain,remote_id=target,
            observed=json.dumps({'source':'manual_id','api_verified':False}),checked_at=datetime.utcnow()))
        db.session.commit()
    except IntegrityError:
        db.session.rollback();raise ValueError('Concurrent or duplicate package link; reload. No link was replaced.') from None
    except Exception:
        db.session.rollback();raise
    return current(package.id)
