"""Narrow outbound client for private customer photos. No customer-facing routes."""
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

import requests
from flask import current_app

MAX = 10_000_000
REF = re.compile(r'tlcp1_[a-f0-9]{64}')
ID = re.compile(r'[a-z][a-z0-9_]{0,63}')
REVISION = re.compile(r'[a-f0-9]{64}')
MIMES = {'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}
ENV_KEYS = ('TLC_PHOTO_WORKER_URL', 'TLC_PHOTO_WORKER_TOKEN', 'TLC_PHOTO_PROXY_PATH')


class Unavailable(RuntimeError):
    pass


def parse_settings(cfg):
    origin, secret, path = (cfg.get(key, '') for key in ENV_KEYS)
    if not all(isinstance(v,str) for v in (origin,secret,path)): return None,None
    try: target = urlsplit(origin)
    except ValueError: return None,None
    if (origin == 'https://' + target.netloc and re.fullmatch(r'[a-z0-9][a-z0-9.-]*',target.netloc)
            and not any((target.path,target.query,target.fragment))
            and re.fullmatch(r'/(apps|a|community|tools)/[a-z0-9-]+',path)
            and re.fullmatch(r'[A-Za-z0-9_-]{32,128}',secret)):
        return {'origin':origin,'path':path},secret
    return None,None


def settings():
    return parse_settings(current_app.config)


def _request(endpoint, data=None, *, download=False):
    public, secret = settings()
    if not public: raise Unavailable()
    from time import monotonic
    deadline=monotonic()+(12 if download else 8)
    try:
        with requests.Session() as session:
            session.trust_env=False
            with session.request('GET' if data is None else 'POST',public['origin']+'/private/'+endpoint,
                    json=data,headers={'Authorization':'Bearer '+secret},timeout=(2,6 if endpoint=='health' else 4),
                    allow_redirects=False,stream=True) as response:
                if response.status_code == 404 and download: return None
                if response.status_code != 200: raise Unavailable()
                mime=response.headers.get('Content-Type','').split(';')[0]
                limit=MAX if download else 4096
                if mime not in (MIMES if download else {'application/json'}): raise Unavailable()
                size=response.headers.get('Content-Length')
                if (download and not size) or (size and (not size.isdigit() or int(size)>limit)): raise Unavailable()
                parts=bytearray()
                for piece in response.iter_content(65536):
                    parts.extend(piece)
                    if len(parts)>limit or monotonic()>deadline: raise Unavailable()
                if not parts or size and int(size)!=len(parts): raise Unavailable()
                return (bytes(parts),mime) if download else json.loads(parts)
    except (requests.RequestException,ValueError,TypeError):
        raise Unavailable() from None


def ready(shop, store):
    proof = current_app.config.get('TLC_DEPLOYMENT_PROOF')
    if proof and (shop, store) != (proof[0].domain, proof[0].shop):
        raise Unavailable()
    public,_ = settings()
    if not public: raise Unavailable()
    evidence = _request('health')
    if (not isinstance(evidence, dict) or evidence.get('ready') is not True
            or evidence != {'ready':True,'shop':shop,'store':store,'path':public['path']}):
        raise Unavailable()


def _managed_order_service(store):
    proof = current_app.config.get('TLC_DEPLOYMENT_PROOF')
    if proof:
        # Establish the Worker/shop identity before sending an order, line,
        # cart hash or reference. The legacy client retains its old behaviour.
        ready(proof[0].domain, store)


def cart_digest(value):
    """Shopify's private '?key=' portion never leaves the browser or is saved here."""
    token = value.split('?key=',1)[0] if isinstance(value,str) else ''
    if not re.fullmatch(r'[!-~]{16,512}',token) or '?' in token:
        return None
    return sha256(token.encode()).hexdigest()


def references(personalisation):
    return {f['id']:f['value'] for f in personalisation.get('fields',[]) if f.get('type')=='file_upload'}


def comparable(encoded):
    value=json.loads(encoded)
    value.pop('photo_verified',None)
    return value


def bind_order(store_id, order, lines, previous=()):
    """One bounded call per order. Shopify has already authenticated cart/line data."""
    from collections import Counter
    from app.shopify_orders import dumps
    prepared, owners, items = [], {}, []
    parsed=[comparable(line['personalisation_json']) for line in lines]
    occurrences=Counter(ref for personal in parsed for ref in references(personal).values())
    retained={line.remote_id:line for line in previous}
    for line,personal in zip(lines,parsed):
        photo_fields=references(personal)
        if not photo_fields:
            prepared.append((line,personal,False))
            continue
        prepared.append((line,personal,True))
        old=retained.get(line['remote_id'])
        if (old and old.properties_json==line['properties_json'] and old.product_id==line['product_id']
                and old.variant_id==line['variant_id']):
            verified={ref for ref in json.loads(old.personalisation_json).get('photo_verified',[])
                      if ref in photo_fields.values() and occurrences[ref]==1}
            if verified: personal['photo_verified']=sorted(verified)
        rev=personal.get('revision')
        if not (order.get('_photo_cart') and line['product_id'] and line['variant_id']
                and isinstance(rev,str) and REVISION.fullmatch(rev) and not personal['warnings']):
            continue
        for field,ref in photo_fields.items():
            if occurrences[ref]!=1 or ref in personal.get('photo_verified',[]): continue
            owners[ref]=personal
            items.append({'ref':ref,'field':field,'revision':rev,'line':line['remote_id'],
                          'product':line['product_id'].rsplit('/',1)[-1],'variant':line['variant_id'].rsplit('/',1)[-1]})
    if 0<len(items)<=10 and settings()[0]:
        try:
            _managed_order_service(store_id)
            response=_request('bind',{'shop':store_id,'order':order['remote_id'],'cart':order['_photo_cart'],'items':items})
            accepted=response.get('accepted') if isinstance(response,dict) else None
            if (isinstance(accepted,list) and all(isinstance(r,str) and r in owners for r in accepted)
                    and len(set(accepted))==len(accepted)):
                for ref in accepted:
                    verified=owners[ref].setdefault('photo_verified',[])
                    if ref not in verified: verified.append(ref);verified.sort()
        except Unavailable:
            pass  # Never block the order. A later existing Sync Now can recheck.
    return [{**line,'personalisation_json':dumps(personal)} if changed else line for line,personal,changed in prepared]


def download(order,line,ref):
    _managed_order_service(order.store_id)
    return _request('photo',{'shop':order.store_id,'order':order.remote_id,'line':line.remote_id,'ref':ref},download=True)
