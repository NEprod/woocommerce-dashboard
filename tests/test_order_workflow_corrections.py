"""Targeted operational fixes only; synthetic orders and existing ledger fixtures."""
import html
import re
from uuid import uuid4
import pytest
from app import db, order_work as work, orders_workspace as workspace
from app.models import Settings, ShopifyOrder, ShopifyOrderLine
from app.order_work_models import OrderWorkLine, WorkPrint, WorkEvent, WorkPick
from app.inventory import service as stock
from app.inventory.models import Transaction
from test_metadata_editor_core import core, save as metadata_save
from test_order_fulfilment import fulfilment, act
from test_inventory import item, manufactured, transact
from test_shopify_orders import order, line, save as import_order


def add_order(world,number=1002):
    core,oid,lid,*_=world
    raw=order(number);p=line(number);p['sku']=db.session.get(ShopifyOrderLine,lid).sku;p['customAttributes']=[]
    raw['lineItems']['nodes']=[p];import_order(raw)
    return ShopifyOrder.query.filter_by(remote_id=raw['id']).one()


def token(response):return html.unescape(re.search(rb'name="selection" value="([^"]+)"',response.data).group(1).decode())


def test_legacy_numeric_lookup_lazy_prepare_and_repeat_scans_preserve_work(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        Settings.query.first().include_test_orders=False;db.session.commit()
        old=add_order(fulfilment);old_id,old_line=old.id,old.lines[0].id
        assert db.session.get(OrderWorkLine,old_line) is None
        Settings.query.first().include_test_orders=True;db.session.commit()
        transactions=Transaction.query.count()
    for reference in ['1002','#1002','#TEST-1002',f'TLC-ORDER-{old_id}']:
        page=client.get('/production/orders/',query_string={'reference':reference},follow_redirects=True)
        assert page.status_code==200 and page.request.path==f'/production/orders/{old_id}'
    with app.app_context():
        row=db.session.get(OrderWorkLine,old_line);assert row and row.version==1
        assert WorkEvent.query.filter_by(line_id=old_line).count()==1
        assert Transaction.query.count()==transactions
        assert ShopifyOrder.query.count()==2


def test_ambiguous_numeric_number_never_selects_arbitrary_order(fulfilment):
    core,oid,lid,*_=fulfilment
    with core[0].app_context():
        extra=add_order(fulfilment,1002);extra.name='WEB-1001-UK';db.session.commit()
        assert len(work.scan('1001'))==2


def test_bulk_print_confirmation_reprint_and_no_state_or_stock_mutations(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        second=add_order(fulfilment);sid=second.id
        act(lid,'start_picking');before=(Transaction.query.count(),WorkEvent.query.count(),db.session.get(OrderWorkLine,lid).version)
    listing=client.get('/orders/?per_page=10&stage=Picking')
    assert listing.status_code==200 and b'data-order-select-all' in listing.data and b'form="bulk-orders"' in listing.data
    preview=client.post('/orders/print',data={'order_id':[str(oid),str(sid)]})
    assert preview.status_code==200 and preview.data.count(b'<article class="order-pick-sheet">')==2
    assert b'Fictional Buyer' in preview.data and b'1 Fictional Test Road' in preview.data
    assert preview.data.index(b'barcode.svg')>preview.data.index(b'<footer class="order-sheet-barcode">')
    selection=token(preview)
    assert client.post('/orders/printed',data={'selection':selection}).status_code==400
    with app.app_context():assert WorkPrint.query.count()==0
    for _ in range(2):assert client.post('/orders/printed',data={'selection':selection,'confirmed':'yes'}).status_code==303
    with app.app_context():
        assert WorkPrint.query.count()==2
        assert before==(Transaction.query.count(),WorkEvent.query.count(),db.session.get(OrderWorkLine,lid).version)
        assert db.session.get(OrderWorkLine,lid).state=='picking'
    second_preview=client.post('/orders/print',data={'order_id':[str(oid),str(sid)]})
    assert client.post('/orders/printed',data={'selection':token(second_preview),'confirmed':'yes'}).status_code==303
    with app.app_context():assert WorkPrint.query.count()==4
    assert client.post('/orders/print',data={}).status_code==400


def test_global_toggle_backend_inventory_and_import_cursor_safety(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    with app.app_context():
        act(lid,'start_picking');r=work.requirements(lid)[0]
        act(lid,'pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id=str(tub))
        before=(stock.balance(iid),Transaction.query.count(),WorkPick.query.count(),r.reserved)
        from app.order_models import ShopifyOrderSync
        from test_shopify_orders import STORE
        db.session.add(ShopifyOrderSync(store_id=STORE,domain='fictional.myshopify.com',checkpoint=work.now(),cursor='retained-cursor',pending_json='[]'))
        db.session.commit()
        checkpoint=db.session.get(ShopifyOrderSync,STORE).checkpoint
    assert client.post('/orders/settings',data={}).status_code==400
    assert client.post('/orders/settings',data={'acknowledge':'yes'}).status_code==303
    assert client.get(f'/production/orders/{oid}').status_code==403
    assert client.get(f'/orders/{oid}').status_code==403
    with app.app_context():
        assert not work.scan('1001') and not work.scan(f'TLC-ORDER-{oid}')
        assert workspace.listing({'test':'yes','include_test':'1'})['pagination'].total==0
        assert workspace.overview(include_test=True)['recent']==[]
        assert db.session.get(ShopifyOrderSync,STORE).checkpoint==checkpoint
        assert db.session.get(ShopifyOrderSync,STORE).cursor=='retained-cursor'
        with pytest.raises(ValueError,match='Test orders are disabled'):act(lid,'finish_picking')
        db.session.rollback()
        old=add_order(fulfilment);new_line=old.lines[0].id
        assert db.session.get(OrderWorkLine,new_line) is None
        assert before==(stock.balance(iid),Transaction.query.count(),WorkPick.query.count(),work.requirements(lid)[0].reserved)
    assert client.post('/orders/settings',data={'include_test':'yes'}).status_code==303
    with app.app_context():
        assert db.session.get(OrderWorkLine,new_line)
        assert before==(stock.balance(iid),Transaction.query.count(),WorkPick.query.count(),work.requirements(lid)[0].reserved)


def test_finished_stock_shortage_plans_one_linked_batch_without_stock(fulfilment):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    raw=item(client,'material','SHORTAGE-PLA','PLA',unit='g')
    finished=manufactured(client,[(raw,'40','g')])
    assert metadata_save(core,kind='shared',data={'production':{'workflow':{'type':'laser','stock_item_id':finished}}}).status_code==200
    with app.app_context():
        act(lid,'refresh');act(lid,'start_picking');r=work.requirements(lid)[0]
        assert db.session.get(OrderWorkLine,lid).state=='waiting'
        key=uuid4().hex
        plan=work.plan_replenishment(finished,key,actor='fixture',requirement_id=r.id);db.session.commit()
        assert work.plan_replenishment(finished,key,actor='fixture',requirement_id=r.id).id==plan.id
        assert plan.requirement_id==r.id and stock.balance(finished)==0
        with pytest.raises(ValueError,match='No uncovered'):work.plan_replenishment(finished,uuid4().hex,actor='fixture',requirement_id=r.id)
        db.session.rollback()


def test_environment_default_and_disabled_projection_share_global_policy(fulfilment,monkeypatch):
    from types import SimpleNamespace
    from app.order_integration_models import OrderIntegrationSettings
    from app import order_work_outbound
    core,oid,lid,*_=fulfilment;app=core[0]
    with app.app_context():
        Settings.query.first().include_test_orders=None;db.session.commit()
        from app.deployment_guard import DEV_DOMAIN,DEV_SHOP
        app.config['TLC_DEPLOYMENT_PROOF']=(SimpleNamespace(environment='development',domain=DEV_DOMAIN,shop=DEV_SHOP),None,None)
        assert workspace.include_test_orders()
        app.config['TLC_DEPLOYMENT_PROOF']=(SimpleNamespace(environment='production',domain='other.myshopify.com',shop='other'),None,None)
        assert not workspace.include_test_orders()
        app.config['TLC_DEPLOYMENT_PROOF']=None
        order=db.session.get(ShopifyOrder,oid)
        db.session.add(OrderIntegrationSettings(store_id=order.store_id,projection_enabled=True,fingerprint='a'*64,actor='fixture',authorised_at=work.now()));db.session.commit()
        monkeypatch.setattr(order_work_outbound,'configuration',lambda:pytest.fail('Disabled test orders cannot project remotely'))
        assert order_work_outbound.process(oid,adapter=object()) is None
@pytest.mark.parametrize('kind',['print','laser'])
def test_finished_stock_replaces_components_without_fresh_manufacture(fulfilment,kind):
    core,oid,lid,iid,tub=fulfilment;app,client=core[:2]
    raw=item(client,'material','FINISHED-PLA','PLA',unit='g')
    finished=manufactured(client,[(raw,'40','g')])
    transact(client,'opening',finished,qty='3',destination=str(tub),cost='3',reason='Existing finished stock')
    result=metadata_save(core,kind='shared',data={'production':{'workflow':{'type':kind,'stock_item_id':finished},'lines':{'components':{'op':'add','item_id':iid,'quantity':'2','unit':'piece'}}}})
    assert result.status_code==200,result.json
    with app.app_context():
        act(lid,'refresh');r=work.requirements(lid)[0]
        assert r.item_id==finished and r.quantity==stock.SCALE and len(work.requirements(lid))==1
        act(lid,'start_picking');act(lid,'pick',requirement_id=str(r.id),code='BODY',quantity='1',location_id=str(tub))
        act(lid,'finish_picking')
        assert db.session.get(OrderWorkLine,lid).state=='quality' and db.session.get(OrderWorkLine,lid).committed_at is None
        assert stock.balance(iid)==20*stock.SCALE and stock.balance(finished)==2*stock.SCALE


def test_mixed_partial_progress_and_whole_order_packing_dispatch_gate(fulfilment):
    core,oid,lid,iid,tub=fulfilment
    with core[0].app_context():
        original=db.session.get(ShopifyOrderLine,lid);raw=order();one=line();two=line(2)
        for row in [one,two]:row['sku']=original.sku;row['customAttributes']=[]
        raw['updatedAt']='2026-10-16T11:00:00Z';raw['lineItems']['nodes']=[one,two];import_order(raw)
        second=db.session.get(ShopifyOrder,oid).lines[1].id
        act(second,'proof_override',required='yes');act(lid,'start_picking');r=work.requirements(lid)[0]
        act(lid,'pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id=str(tub))
        assert work.summary(db.session.get(ShopifyOrder,oid))=='Partially Picked'
        act(lid,'pick',requirement_id=str(r.id),code='SANTA-BODY',quantity='1',location_id=str(tub))
        act(lid,'finish_picking');act(lid,'start_production');act(lid,'quality');act(lid,'ready_pack')
        assert work.summary(db.session.get(ShopifyOrder,oid))=='Partially Produced'
        from app.order_work_outbound import projected_status
        assert projected_status(db.session.get(ShopifyOrder,oid))=='Partially Produced'
        with pytest.raises(ValueError,match='All applicable'):act(lid,'packing')
        db.session.rollback()
        db.session.get(OrderWorkLine,second).state='ready_pack';db.session.commit()
        act(lid,'packing');act(lid,'ready_dispatch')
        with pytest.raises(ValueError,match='All applicable'):act(lid,'dispatch',carrier='Synthetic handoff',tracking='TEST')
        db.session.rollback()
        assert db.session.get(ShopifyOrder,oid).fulfillment_status=='UNFULFILLED'


def test_privacy_print_and_interactive_instructions_exclude_private_urls(fulfilment):
    core,oid,lid,*_=fulfilment
    with core[0].app_context():
        import json
        line=db.session.get(ShopifyOrderLine,lid)
        line.personalisation_json=json.dumps({'fields':[{'label':'Name','display':'Santa for Ellie','type':'text'},
            {'label':'Photo','display':'https://private.example/token-secret','type':'file_upload'}]})
        db.session.commit()
    for url in [f'/production/orders/{oid}',f'/production/orders/{oid}/sheet']:
        page=core[1].get(url);assert page.status_code==200 and b'Santa for Ellie' in page.data and b'token-secret' not in page.data
