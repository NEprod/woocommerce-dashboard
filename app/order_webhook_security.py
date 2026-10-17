"""Shared event-only gateway/inbox cryptography; no commerce API client."""
import base64
import hashlib
import hmac
import json
import re
from cryptography.fernet import Fernet

TOPICS=frozenset({'orders/create','orders/updated','orders/cancelled','orders/paid','refunds/create'})
MAX_BODY=2*1024*1024


def verified(body,headers,*,secret,domain):
    headers={k.lower():v for k,v in headers.items()}
    signature=headers.get('x-shopify-hmac-sha256','')
    expected=base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha256).digest()).decode()
    if not secret or len(body)>MAX_BODY or not hmac.compare_digest(expected,signature):raise ValueError('Webhook authentication rejected.')
    topic=headers.get('x-shopify-topic')
    delivery=headers.get('x-shopify-webhook-id','')
    if (headers.get('x-shopify-shop-domain')!=domain or topic not in TOPICS
            or headers.get('x-shopify-api-version')!='2026-10'
            or not re.fullmatch(r'[A-Za-z0-9-]{8,128}',delivery)):
        raise ValueError('Webhook source, version or topic rejected.')
    raw=json.loads(body)
    if not isinstance(raw,dict):raise ValueError('Webhook object required.')
    return raw,topic,delivery


def seal(body,headers,key):
    retained={k.lower():v for k,v in headers.items() if k.lower() in {
        'x-shopify-hmac-sha256','x-shopify-topic','x-shopify-shop-domain','x-shopify-api-version',
        'x-shopify-webhook-id','x-shopify-event-id','x-shopify-triggered-at'}}
    return Fernet(key.encode()).encrypt(json.dumps({'body':base64.b64encode(body).decode(),'headers':retained}).encode()).decode()


def unseal(envelope,key):
    value=json.loads(Fernet(key.encode()).decrypt(envelope.encode()))
    return base64.b64decode(value['body'],validate=True),value['headers']
