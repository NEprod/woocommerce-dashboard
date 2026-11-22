"""Disposable catalogue + inventory + imported orders; network is forbidden."""
import json
from uuid import uuid4
import pytest
import requests
from app import db, order_work as work
from app.models import Product, ShopifyProductIdentity, ShopifyOrder
from app.order_work_models import OrderWorkLine, WorkPick, WorkEvent
from app.inventory import service as stock
from app.inventory.models import Transaction
from test_metadata_editor_core import core, save as metadata_save
from test_inventory import item, location, transact, manufactured
from test_shopify_orders import order, line, save as import_order, STORE, PRODUCT, VARIANT


@pytest.fixture
def fulfilment(core, monkeypatch):
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('Unexpected external request'))
    app,client,*_=core
    with app.app_context():
        from app.models import Settings
        Settings.query.first().include_test_orders=True;db.session.commit()
    tub=location(client,'ORDER-TUB','Order tub')
    component=item(client,'purchased','SANTA-BODY','Santa Body',pack_qty='10',pack_price='5',minimum='2',target='10')
    transact(client,'receive',component,qty='20',destination=str(tub),cost='10')
    result=metadata_save(core,kind='shared',data={'production':{'workflow':{'type':'assembly'},'lines':{'body':{'op':'add','item_id':component,'quantity':'2','unit':'piece'}}}})
    assert result.status_code==200,result.json
    with app.app_context():
        p=db.session.get(Product,core[4]['Simple Collection/Blue Token'])
        db.session.add(ShopifyProductIdentity(product_id=p.id,source_identity=p.source_relpath,sku=p.sku,
            store_key='fixture',store_id=STORE,store_domain='fictional.myshopify.com',shopify_product_id=PRODUCT,
            state='verified',verification_state='verified',reviewed_digest='a'*64,
            media_evidence_json=json.dumps({'version':1,'variant_id':VARIANT})))
        db.session.commit()
        raw=order();purchased=line();purchased['sku']=p.sku;purchased['customAttributes']=[]
        raw['lineItems']['nodes']=[purchased];import_order(raw)
        oid=ShopifyOrder.query.one().id;lid=ShopifyOrder.query.one().lines[0].id
    return core,oid,lid,component,tub


def act(lid,action,**data):
    row=db.session.get(OrderWorkLine,lid)
    result=work.act(lid,action,dict(confirmed='yes',reason='Synthetic operator evidence',**data),uuid4().hex,actor='test-admin',version=row.version)
    db.session.commit();return result


def test_manual_and_barcode_lookup_print_are_same_read_only_job(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        before=(Transaction.query.count(),WorkEvent.query.count(),db.session.get(OrderWorkLine,lid).version)
        assert work.scan('#TEST-1001')[0].id==work.scan(f'TLC-ORDER-{oid}')[0].id==oid
    for ref in ['#TEST-1001',f'TLC-ORDER-{oid}']:
        response=client.get('/production/orders/',query_string={'reference':ref})
        assert response.headers['Location']==f'/production/orders/{oid}'
    for path in [f'/production/orders/{oid}',f'/production/orders/{oid}/sheet',f'/production/orders/{oid}/barcode.svg','/production/orders/replenishment']:
        response=client.get(path);assert response.status_code==200,response.data[:400]
    with app.app_context():assert before==(Transaction.query.count(),WorkEvent.query.count(),db.session.get(OrderWorkLine,lid).version)


def test_partial_picking_idempotency_and_production_progress(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        assert db.session.get(OrderWorkLine,lid).state=='ready'
        act(lid,'start_picking');r=work.requirements(lid)[0]
        assert r.quantity==2*stock.SCALE and r.reserved==2*stock.SCALE
        key=uuid4().hex;version=db.session.get(OrderWorkLine,lid).version
        data=dict(confirmed='yes',reason='Partial pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id=str(tub))
        for _ in range(2):work.act(lid,'pick',data,key,actor='test-admin',version=version);db.session.commit()
        assert stock.balance(iid)==19*stock.SCALE and WorkPick.query.count()==1
        with pytest.raises(ValueError,match='Every required'):act(lid,'finish_picking')
        db.session.rollback()
        with pytest.raises(ValueError,match='Wrong component'):act(lid,'pick',requirement_id=str(r.id),code='NOT-THIS',quantity='1',location_id=str(tub))
        db.session.rollback()
        act(lid,'pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id=str(tub))
        act(lid,'finish_picking');act(lid,'start_production')
        assert db.session.get(OrderWorkLine,lid).committed_at
        for action in ['assembly','quality','ready_pack','packing','ready_dispatch']:act(lid,action)
        assert work.summary(db.session.get(ShopifyOrder,oid))=='Ready for Dispatch'
        act(lid,'dispatch',carrier='Test handoff',tracking='TEST-ONLY')
        assert stock.balance(iid)==18*stock.SCALE
        assert db.session.get(ShopifyOrder,oid).fulfillment_status=='UNFULFILLED'


@pytest.mark.parametrize('kind',['card','laser','print'])
def test_proof_gate_revision_and_manual_approval(fulfilment,kind):
    core,oid,lid,iid,tub=fulfilment
    response=metadata_save(core,kind='shared',data={'production':{'workflow':{'type':kind,'proof_required':True,'personalised':True},'lines':{}}})
    assert response.status_code==200,response.json
    with core[0].app_context():
        act(lid,'refresh')
        with pytest.raises(ValueError,match='Awaiting Proof Approval'):act(lid,'start_production')
        db.session.rollback()
        act(lid,'prepare_proof',reference='private-proof-reference-v1')
        act(lid,'send_proof',reviewed='yes',method='Manual approved channel')
        with pytest.raises(ValueError,match='Awaiting Proof Approval'):act(lid,'start_production')
        db.session.rollback()
        act(lid,'changes');act(lid,'prepare_proof',reference='private-proof-reference-v2')
        act(lid,'send_proof',reviewed='yes',method='Manual approved channel');act(lid,'approve_proof',method='Recorded customer instruction')
        act(lid,'start_production')
        assert work.latest_proof(lid).version==2


def test_external_order_edit_holds_frozen_bom_and_releases_claims(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        act(lid,'start_picking');snapshot=db.session.get(OrderWorkLine,lid).bom_json
        row=db.session.get(ShopifyOrder,oid);row.lines[0].current_quantity=2
        work.reconcile(row);db.session.commit()
        assert db.session.get(OrderWorkLine,lid).state=='hold'
        assert db.session.get(OrderWorkLine,lid).bom_json==snapshot
        assert work.reserved(iid)==0 and stock.balance(iid)==20*stock.SCALE


def test_purchasing_plan_links_existing_receipt_without_new_stock(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    other=item(client,'material','LOW-PLA','Low PLA',unit='g',pack_qty='1000',pack_unit='g',pack_price='13.99',minimum='500',target='1000')
    with app.app_context():
        plan=work.plan_replenishment(other,uuid4().hex,actor='test-admin');db.session.commit();pid=plan.id
        assert work.purchase_incoming(plan)>0
    transact(client,'receive',other,qty='1000',destination=str(tub),cost='13.99')
    with app.app_context():
        receipt=Transaction.query.filter_by(item_id=other,kind='receive').one()
        before=Transaction.query.count();key=uuid4().hex
        for _ in range(2):work.reconcile_purchase(pid,receipt.id,key,actor='test-admin',reason='Matched delivery');db.session.commit()
        assert Transaction.query.count()==before and work.purchase_incoming(db.session.get(work.WorkPlan,pid))==0


def test_batch_shortage_plan_receive_resume_pick_and_compensating_return(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    raw=item(client,'material','ORDER-PLA','Order PLA',unit='g',pack_qty='1000',pack_price='13.99')
    transact(client,'receive',raw,qty='1000',destination=str(tub),cost='13.99')
    body=manufactured(client,[(raw,'40','g')])
    result=metadata_save(core,kind='shared',data={'production':{'workflow':{'type':'assembly'},'lines':{'body':{'op':'add','item_id':body,'quantity':'2','unit':'piece'}}}})
    assert result.status_code==200,result.json
    with app.app_context():
        act(lid,'refresh');act(lid,'start_picking')
        assert db.session.get(OrderWorkLine,lid).state=='waiting'
        r=work.requirements(lid)[0]
        plan=work.plan_replenishment(body,uuid4().hex,actor='test-admin',requirement_id=r.id);db.session.commit()
        assert stock.balance(body)==0
        from app.inventory.models import ProductionBatch
        batch=ProductionBatch.query.filter_by(component_id=body).one()
        stock.complete_batch(batch.id,3,1,{raw:('40',tub)},tub,uuid4().hex,actor='test-admin');db.session.commit()
        assert stock.balance(raw)==960*stock.SCALE
        act(lid,'start_picking')
        with pytest.raises(ValueError,match='specific production batch'):
            act(lid,'pick',requirement_id=str(r.id),code='BODY',quantity='2',location_id=str(tub))
        db.session.rollback()
        act(lid,'pick',requirement_id=str(r.id),code=batch.code,quantity='2',location_id=str(tub))
        assert stock.batch_available(batch.id)==stock.balance(body)==stock.SCALE
        pick=WorkPick.query.one()
        with pytest.raises(ValueError,match='Order Fulfilment'):
            stock.plan('reversal',dict(original=pick.transaction_id,reason='Test incorrect generic return'))
        db.session.rollback()
        act(lid,'return_pick',pick_id=pick.id)
        assert stock.balance(body)==stock.batch_available(batch.id)==3*stock.SCALE
        assert work.picked(r)==0 and db.session.get(OrderWorkLine,lid).state=='hold'


def test_reservations_protect_other_order_stock_and_cancel_does_not_refund(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        act(lid,'start_picking')
        with pytest.raises(ValueError,match='reserved'):
            stock.plan('use',dict(item_id=str(iid),qty='19',source=str(tub),reason='Would consume another order claim'))
        db.session.rollback()
        act(lid,'cancel')
        assert work.reserved(iid)==0 and stock.balance(iid)==20*stock.SCALE
        assert db.session.get(ShopifyOrder,oid).cancelled_at is None


def test_mixed_line_status_and_proof_gate_are_independent(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        first=db.session.get(ShopifyOrder,oid).lines[0]
        sku=first.sku
        raw=order();one=line();one['sku']=sku;one['customAttributes']=[]
        two=line(2);two['sku']=sku;two['customAttributes']=[]
        raw['updatedAt']='2026-10-16T11:00:00Z';raw['lineItems']['nodes']=[one,two];import_order(raw)
        second=db.session.get(ShopifyOrder,oid).lines[1]
        act(second.id,'proof_override',required='yes')
        act(lid,'start_picking')
        assert db.session.get(OrderWorkLine,lid).state=='picking'
        assert db.session.get(OrderWorkLine,second.id).state=='awaiting_proof'
        assert work.summary(db.session.get(ShopifyOrder,oid))!='Ready for Dispatch'
        with pytest.raises(ValueError,match='Awaiting Proof Approval'):act(second.id,'start_picking')
        db.session.rollback()


def test_committed_cancellation_requires_audited_exception_even_for_standard_goods(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        act(lid,'start_picking');r=work.requirements(lid)[0]
        act(lid,'pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='2',location_id=str(tub))
        act(lid,'finish_picking');act(lid,'start_production')
        assert not json.loads(db.session.get(OrderWorkLine,lid).policy_json)['personalised']
        with pytest.raises(ValueError,match='authorised exception'):act(lid,'cancel')
        db.session.rollback()
        act(lid,'cancel',exception='yes',reconciliation='Physical picked components retained pending authorised return; external refund review recorded.')
        assert db.session.get(ShopifyOrder,oid).cancelled_at is None
        assert stock.balance(iid)==18*stock.SCALE


def test_failed_pick_rolls_back_claim_and_ledger_together(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        act(lid,'start_picking');r=work.requirements(lid)[0];count=Transaction.query.count()
        with pytest.raises(ValueError):act(lid,'pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id='99999')
        db.session.rollback()
        assert work.reserved(iid)==2*stock.SCALE and Transaction.query.count()==count and WorkPick.query.count()==0


def test_same_pick_concurrent_requests_are_one_ledger_issue(fulfilment):
    from concurrent.futures import ThreadPoolExecutor
    core,oid,lid,iid,tub=fulfilment;app=core[0]
    with app.app_context():
        act(lid,'start_picking');r=work.requirements(lid)[0];version=db.session.get(OrderWorkLine,lid).version
        data=dict(confirmed='yes',reason='Concurrent retry evidence',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id=str(tub))
    key=uuid4().hex
    def submit():
        with app.app_context():
            result=work.act(lid,'pick',data,key,actor='test-admin',version=version);db.session.commit();return result.state
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:submit(),range(2)))==['picking','picking']
    with app.app_context():assert WorkPick.query.count()==1 and stock.balance(iid)==19*stock.SCALE


def test_unmapped_variant_is_configuration_block_not_arbitrary_sku_match(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        line=db.session.get(work.ShopifyOrderLine,lid);line.variant_id='gid://shopify/ProductVariant/9999'
        work.reconcile(line.order);db.session.commit()
        with pytest.raises(ValueError,match='variant/SKU'):act(lid,'refresh')
        db.session.rollback()
        assert stock.balance(iid)==20*stock.SCALE and WorkPick.query.count()==0


def test_variant_workflow_overrides_collection_policy_without_copying_bom(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        product=db.session.get(Product,core[4]['Variable Collection/Badge One']);variant=product.variations[0]
        sku,source,pid=variant.sku,variant.source_identity,product.id
    response=metadata_save(core,'Variable Collection/Badge One',kind='shared',data={'production':{'workflow':{'type':'card','proof_required':True},'lines':{},
        'variants':{sku:{'workflow':{'type':'laser','proof_required':False}}}}})
    assert response.status_code==200,response.json
    with core[0].app_context():
        product=db.session.get(Product,pid)
        db.session.add(ShopifyProductIdentity(product_id=pid,source_identity=product.source_relpath,sku=product.sku,
            store_key='fixture-variable',store_id=STORE,store_domain='fictional.myshopify.com',shopify_product_id='gid://shopify/Product/101',
            state='verified',verification_state='verified',reviewed_digest='a'*64,
            media_evidence_json=json.dumps({'version':1,'variant_maps':{source:{'id':'gid://shopify/ProductVariant/201','sku':sku}}})))
        db.session.commit()
        raw=order(1002);purchased=line(3);purchased.update(sku=sku,product={'id':'gid://shopify/Product/101'},variant={'id':'gid://shopify/ProductVariant/201'},customAttributes=[])
        raw['lineItems']['nodes']=[purchased];import_order(raw)
        row=db.session.get(OrderWorkLine,ShopifyOrder.query.filter_by(remote_id=raw['id']).one().lines[0].id)
        assert row.state=='ready' and not row.proof_required and json.loads(row.policy_json)['type']=='laser'
