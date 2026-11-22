"""Focused operator-stage, proof precedence and existing-ledger regressions."""
import json
from uuid import uuid4
import pytest
from app import db, order_work as work
from app.models import ShopifyOrder, ShopifyOrderLine
from app.order_work_models import OrderWorkLine, WorkPick, WorkPrint
from app.inventory import service as stock
from app.inventory.models import Transaction
from app.order_personalisation import parse
from test_metadata_editor_core import core, save as metadata_save
from test_order_fulfilment import fulfilment, act
from test_inventory import item, transact


def configure(world,workflow=None,item_id=None):
    core,oid,lid,iid,tub=world
    production={'lines':{'sheet':{'op':'add','item_id':item_id or iid,'quantity':'1','unit':'piece'}}}
    if workflow is not None:production['workflow']=workflow
    assert metadata_save(core,kind='shared',removals=[['production']]).status_code==200
    response=metadata_save(core,kind='shared',data={'production':production})
    assert response.status_code==200,response.json
    with core[0].app_context():act(lid,'refresh')


@pytest.mark.parametrize('route',['card','print','assembly','laser','stock','mixed'])
def test_existing_route_identifiers_and_requirements_remain_compatible(fulfilment,route):
    configure(fulfilment,{'type':route})
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        row=db.session.get(OrderWorkLine,lid)
        assert row.state=='ready' and json.loads(row.policy_json)['type']==route
        assert work.requirements(lid)[0].quantity==stock.SCALE


@pytest.mark.parametrize('choice,expected',[('yes',True),('no',False),('required',True),(None,True)])
def test_purchase_proof_precedence_and_legacy(fulfilment,choice,expected):
    core,oid,lid,*_=fulfilment
    configure(fulfilment,{'type':'card','proof_required':True})
    with core[0].app_context():
        line=db.session.get(ShopifyOrderLine,lid)
        if choice:
            display={'yes':'Yes, send me a proof for approval.','no':'No, proceed without a proof.','required':'Proof Required'}[choice]
            snapshot=dict(schema_version=1,revision='a'*64,fields=[],proof=dict(policy='required' if choice=='required' else 'optional',choice=choice,display=display,property_key='Digital proof'))
            props=[dict(key='_tlc_configuration',value='a'*64),dict(key='_tlc_personalisation',value=json.dumps(snapshot)),dict(key='Digital proof',value=display)]
            personal=parse(props)
            assert personal['proof']['choice']==choice
            line.personalisation_json=json.dumps(personal);line.properties_json=json.dumps(props);db.session.commit()
        act(lid,'refresh')
        row=db.session.get(OrderWorkLine,lid)
        assert row.proof_required is expected
        assert row.state==('awaiting_proof' if expected else 'ready')
        assert 'proof_source' in json.loads(row.policy_json)


def test_no_route_one_sheet_manual_stages_and_retry(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    sheet=item(client,'material','GLOSS-SHEET','6x6 Gloss Card',unit='piece',pack_qty='10',pack_unit='piece',pack_price='5')
    transact(client,'receive',sheet,qty='10',destination=str(tub),cost='5')
    configure(fulfilment,item_id=sheet)
    with app.app_context():
        assert 'type' not in json.loads(db.session.get(OrderWorkLine,lid).policy_json)
        before=Transaction.query.count()
        act(lid,'status',status='production')
        assert stock.balance(sheet)==10*stock.SCALE and Transaction.query.count()==before
        with pytest.raises(ValueError,match='outstanding'):act(lid,'status',status='produced')
        db.session.rollback()
        row=db.session.get(OrderWorkLine,lid);r=work.requirements(lid)[0];key=uuid4().hex;version=row.version
        data=dict(confirmed='yes',reason='One sheet physically used',requirement_id=str(r.id),code='GLOSS-SHEET',quantity='1',location_id=str(tub))
        for _ in range(2):work.act(lid,'pick',data,key,actor='operator',version=version);db.session.commit()
        assert stock.balance(sheet)==9*stock.SCALE and WorkPick.query.count()==1
        for target in ['produced','quality_checked','packed','ready_dispatch']:
            act(lid,'status',status=target)
        act(lid,'status',status='printed')
        assert db.session.get(OrderWorkLine,lid).state=='ready_dispatch' and WorkPrint.query.count()==1
        act(lid,'status',status='dispatched',carrier='Actual handoff',tracking='REF-1001')
        assert stock.balance(sheet)==9*stock.SCALE and Transaction.query.count()==before+1
        assert db.session.get(ShopifyOrder,oid).fulfillment_status=='UNFULFILLED'


def test_manual_proof_evidence_required_and_no_stock_on_status(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    configure(fulfilment,{'proof_required':True})
    with core[0].app_context():
        before=stock.balance(iid)
        with pytest.raises(ValueError,match='Proof Approval'):act(lid,'status',status='production')
        db.session.rollback()
        with pytest.raises(ValueError,match='current proof sent'):act(lid,'status',status='proof_approved',approval_received='yes',method='Phone')
        db.session.rollback()
        act(lid,'status',status='proof_sent',reference='PROOF-1001-V1',method='Existing approved channel')
        with pytest.raises(ValueError,match='actual customer approval'):act(lid,'status',status='proof_approved',method='Phone')
        db.session.rollback()
        act(lid,'status',status='proof_approved',approval_received='yes',method='Phone')
        act(lid,'status',status='production')
        assert stock.balance(iid)==before


def test_legacy_refresh_render_email_and_stage_controls(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    configure(fulfilment)
    with app.app_context():
        row=db.session.get(OrderWorkLine,lid);row.state='needs_configuration';row.hold_reason='Configure the product/collection manufacturing workflow first.'
        order=db.session.get(ShopifyOrder,oid);order.email='purchased@example.test';db.session.commit()
        act(lid,'refresh');assert row.state=='ready'
        before=(Transaction.query.count(),row.version)
    for _ in range(2):
        response=client.get(f'/production/orders/{oid}')
        assert response.status_code==200
        for text in [b'Update Status',b'Copy Email',b'purchased@example.test',b'Advanced / Troubleshooting',b'Confirm Picked']:
            assert text in response.data
    with app.app_context():assert before==(Transaction.query.count(),db.session.get(OrderWorkLine,lid).version)


def test_mixed_order_manual_completion_is_not_whole_order_completion(fulfilment):
    from test_order_workflow_corrections import add_order
    core,oid,lid,*_=fulfilment
    with core[0].app_context():
        other=add_order(fulfilment)
        # Reuse a second real imported line, attached to the same synthetic order.
        second=other.lines[0];second.order_id=oid
        workrow=db.session.get(OrderWorkLine,second.id);workrow.order_id=oid;workrow.state='proof_sent'
        first=db.session.get(OrderWorkLine,lid);first.state='produced'
        for requirement in work.requirements(lid):requirement.active=False
        db.session.commit()
        assert work.summary(db.session.get(ShopifyOrder,oid))=='Partially Produced'
        with pytest.raises(ValueError,match='All applicable'):act(lid,'status',status='packed')
