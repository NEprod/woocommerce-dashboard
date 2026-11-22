"""HTTPS form submissions retain strict CSRF and same-origin verification."""
import re
from uuid import uuid4
from app import db
from app.order_work_models import OrderWorkLine, WorkEvent
from app.inventory.models import Transaction
from test_metadata_editor_core import core
from test_order_fulfilment import fulfilment


def test_https_refresh_with_strict_csrf(fulfilment):
    core,oid,lid,*_=fulfilment
    app,client=core[:2]
    app.config.update(WTF_CSRF_ENABLED=True,WTF_CSRF_SSL_STRICT=True)
    origin='https://localhost'
    path=f'/production/orders/{oid}'
    page=client.get(path,base_url=origin)
    assert page.status_code==200
    assert page.headers['Referrer-Policy']=='same-origin'
    csrf=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
    with app.app_context():
        version=db.session.get(OrderWorkLine,lid).version
        before=(Transaction.query.count(),WorkEvent.query.count())
    payload=dict(csrf_token=csrf,request_id=uuid4().hex,version=str(version),
                 confirmed='yes',reason='Refresh saved order requirements')
    action=f'{path}/line/{lid}/refresh'
    missing_ref=client.post(action,base_url=origin,data=payload)
    assert missing_ref.status_code==400 and b'referrer header is missing' in missing_ref.data
    foreign_ref=client.post(action,base_url=origin,data=payload,headers={'Referer':'https://other.invalid/'})
    assert foreign_ref.status_code==400 and b'referrer does not match' in foreign_ref.data
    bad_token=client.post(action,base_url=origin,data={**payload,'csrf_token':'invalid'},headers={'Referer':origin+path})
    assert bad_token.status_code==400
    with app.app_context():
        assert before==(Transaction.query.count(),WorkEvent.query.count())
    accepted=client.post(action,base_url=origin,data=payload,headers={'Referer':origin+path})
    assert accepted.status_code==303
    with app.app_context():
        assert db.session.get(OrderWorkLine,lid).version==version+1
        assert Transaction.query.count()==before[0]
