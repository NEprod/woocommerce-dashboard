"""Only working-sheet controls, private proof attachments and ledger confirmation."""
from io import BytesIO
from uuid import uuid4
import pytest
from PIL import Image
from app import db, order_work as work
from app.models import ShopifyOrder
from app.order_work_models import OrderWorkLine, WorkProof, WorkEvent, WorkPick
from app.inventory import service as stock
from app.inventory.models import Transaction
from test_metadata_editor_core import core
from test_order_fulfilment import fulfilment


def submit(client,oid,lid,action,version,**data):
    return client.post(f'/production/orders/{oid}/line/{lid}/{action}',data=dict(
        request_id=uuid4().hex,version=str(version),confirmed='yes',**data))


def picture():
    output=BytesIO();Image.new('RGB',(40,60),'white').save(output,'PNG');output.seek(0);return output


def test_routine_status_and_stock_need_no_reason_and_remain_idempotent(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        version=db.session.get(OrderWorkLine,lid).version;before=Transaction.query.count()
    response=submit(client,oid,lid,'status',version,status='production');assert response.status_code==303
    with app.app_context():
        row=db.session.get(OrderWorkLine,lid);assert row.state=='production'
        assert Transaction.query.count()==before
        r=work.requirements(lid)[0];key=uuid4().hex
        payload=dict(request_id=key,version=str(row.version),confirmed='yes',requirement_id=str(r.id),
            code='SANTA-BODY',quantity='2',location_id=str(tub))
    for _ in range(2):assert client.post(f'/production/orders/{oid}/line/{lid}/pick',data=payload).status_code==303
    with app.app_context():
        assert stock.balance(iid)==18*stock.SCALE and WorkPick.query.count()==1
        assert Transaction.query.count()==before+1
        assert WorkEvent.query.filter_by(action='pick').one().reason
        for target in ['produced','quality_checked','packed','ready_dispatch','dispatched']:
            row=db.session.get(OrderWorkLine,lid)
            work.act(lid,'status',dict(confirmed='yes',status=target),uuid4().hex,actor='operator',version=row.version)
            db.session.commit()
        assert db.session.get(ShopifyOrder,oid).fulfillment_status=='UNFULFILLED'


def test_private_proof_attach_send_approve_and_failed_replacement(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        row=db.session.get(OrderWorkLine,lid);row.proof_required=True;row.state='awaiting_proof'
        version=row.version;before=Transaction.query.count();db.session.commit()
    response=submit(client,oid,lid,'attach_proof',version,proof_file=(picture(),'proof.png'))
    assert response.status_code==303
    with app.app_context():
        proof=WorkProof.query.one();pid=proof.id;blob=proof.file_data;version=db.session.get(OrderWorkLine,lid).version
        assert proof.state=='preparing' and proof.sent_at is None and blob[:4]==b'RIFF'
    file=client.get(f'/production/orders/{oid}/proof/{pid}')
    assert file.status_code==200 and file.mimetype=='image/webp' and file.data==blob
    assert 'no-store' in file.headers['Cache-Control']
    assert client.get(f'/production/orders/{oid+100}/proof/{pid}').status_code==404
    invalid=submit(client,oid,lid,'attach_proof',version,proof_file=(BytesIO(b'<script>bad</script>'),'fake.png'))
    assert invalid.status_code==303
    with app.app_context():
        assert WorkProof.query.count()==1 and db.session.get(WorkProof,pid).file_data==blob
        assert db.session.get(OrderWorkLine,lid).version==version
    assert submit(client,oid,lid,'status',version,status='proof_sent').status_code==303
    with app.app_context():
        assert WorkProof.query.count()==1 and db.session.get(WorkProof,pid).state=='sent'
        version=db.session.get(OrderWorkLine,lid).version
    # A selected status is not evidence of approval without explicit confirmation.
    assert submit(client,oid,lid,'status',version,status='proof_approved').status_code==303
    with app.app_context():assert db.session.get(OrderWorkLine,lid).state=='proof_sent'
    assert submit(client,oid,lid,'status',version,status='proof_approved',approval_received='yes').status_code==303
    with app.app_context():
        assert db.session.get(WorkProof,pid).state=='approved'
        assert Transaction.query.count()==before
    guest=app.test_client();assert guest.get(f'/production/orders/{oid}/proof/{pid}').status_code==401


def test_working_sheet_tables_and_routine_controls(fulfilment):
    core,oid,lid,*_=fulfilment;app,client=core[:2]
    response=client.get(f'/production/orders/{oid}');assert response.status_code==200
    from html.parser import HTMLParser
    class Forms(HTMLParser):
        def __init__(self):super().__init__();self.action=None;self.fields={}
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='form':self.action=attrs.get('data-action');self.fields.setdefault(self.action,[])
            if tag=='input' and self.action:self.fields[self.action].append(attrs)
        def handle_endtag(self,tag):
            if tag=='form':self.action=None
    forms=Forms();forms.feed(response.get_data(as_text=True))
    assert not any(i.get('name')=='reason' for i in forms.fields['status']+forms.fields['pick']+forms.fields['refresh'])
    assert any(i.get('name')=='reason' and 'required' in i for i in forms.fields['cancel'])
    assert b'fulfilment-product-table' in response.data and b'data-quick-scan' in response.data
    assert b'mailto:' in response.data and b'Personalisation' in response.data
