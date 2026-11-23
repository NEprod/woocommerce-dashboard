import json
import pytest
from app import db
from app.inventory import service as stock
from app.inventory.models import Item, Identity, RecipeMaterial, BomScenario, Movement
from app.models import Settings, CatalogueOperation
from test_inventory import workshop, client_for, item, manufactured, transact, location
from test_setup_flow import setup_app


@pytest.fixture(autouse=True)
def empty_catalogue(workshop, tmp_path):
    root=tmp_path/'empty-catalogue';root.mkdir()
    with workshop.app_context():
        settings=Settings.query.first()
        if settings: settings.product_folder=str(root)
        else: db.session.add(Settings(product_folder=str(root)))
        db.session.commit()


def remove(client, iid, code, **extra):
    return client.post(f'/production/item/{iid}/delete', data=dict(
        version='1', confirmed='yes', confirm_code=code, **extra), follow_redirects=True)


@pytest.mark.parametrize('kind',['material','purchased','manufactured'])
def test_delete_unused_requires_confirmation_preserves_ids_and_other_records(workshop, kind):
    c=client_for(workshop)
    raw=item(c,'material','RAW','Raw',unit='g')
    iid=manufactured(c,[(raw,'40','g')]) if kind=='manufactured' else item(c,kind,'MISTAKE','Mistake',unit='g' if kind=='material' else 'piece')
    code='BODY' if kind=='manufactured' else 'MISTAKE'
    url=f'/production/item/{iid}/delete'
    assert c.get(url).status_code==405
    c.post(url,data=dict(version='1',confirm_code=code))
    with workshop.app_context(): assert db.session.get(Item,iid) is not None
    page=remove(c,iid,code)
    assert page.status_code==200 and b'Unused item deleted' in page.data, page.get_data(as_text=True).split('alert')[1:3]
    with workshop.app_context():
        assert db.session.get(Item,iid) is None
        assert db.session.get(Item,raw) is not None
        assert db.session.get(Identity,iid).code==code
        assert RecipeMaterial.query.filter_by(component_id=iid).count()==0
        assert Movement.query.count()==0
        event=CatalogueOperation.query.filter_by(operation_type='inventory_item_maintenance').one()
        assert json.loads(event.scope)['action']=='delete_unused'
    assert c.get('/production/lookup',query_string={'code':code}).status_code==400
    newer=item(c,'purchased','NEW','New')
    assert newer!=iid


def test_delete_blocks_history_batches_recipe_and_scenario_references(workshop):
    c=client_for(workshop)
    raw=item(c,'material','RAW','Raw',unit='g',pack_qty='1000',pack_price='20')
    part=manufactured(c,[(raw,'40','g')])
    assert b'Archive' in remove(c,raw,'RAW').data
    with workshop.app_context():
        stock.reserve_batches(part,1,'a'*32);db.session.commit()
    assert b'Archive' in remove(c,part,'BODY').data
    used=item(c,'purchased','USED','Used',pack_qty='1',pack_price='1')
    tub=location(c,'TUB','Tub')
    transact(c,'opening',used,qty='1',cost='1',destination=tub,reason='Fixture opening stock')
    assert b'Archive' in remove(c,used,'USED').data
    saved=item(c,'purchased','SAVED','Saved')
    with workshop.app_context():
        db.session.add(BomScenario(name='Saved',document=json.dumps({'lines':[{'item_id':saved}]}),version=1));db.session.commit()
    assert b'saved scenario' in remove(c,saved,'SAVED').data
    with workshop.app_context(): assert Item.query.count()==4 and Movement.query.count()==1


def test_delete_checks_unscanned_catalogue_and_fails_closed(workshop,tmp_path):
    c=client_for(workshop);iid=item(c,'purchased','JSON-PART','Part')
    root=tmp_path/'catalogue';root.mkdir()
    source=root/'product_info.json';source.write_text(json.dumps({'production':{'lines':{'part':{'item_id':iid}}}}))
    with workshop.app_context():
        settings=Settings.query.first()
        if settings: settings.product_folder=str(root)
        else: db.session.add(Settings(product_folder=str(root)))
        db.session.commit()
    assert b'catalogue JSON' in remove(c,iid,'JSON-PART').data
    source.write_text('{bad json')
    assert b'cannot be checked' in remove(c,iid,'JSON-PART').data
    source.unlink();root.rmdir()
    assert b'storage is unavailable' in remove(c,iid,'JSON-PART').data
    with workshop.app_context(): assert db.session.get(Item,iid) is not None


def test_barcode_correction_keeps_stock_recipe_batches_and_audit(workshop):
    c=client_for(workshop)
    raw=item(c,'material','RAW','Raw',unit='g',pack_qty='1000',pack_price='20')
    part=manufactured(c,[(raw,'40','g')])
    tub=location(c,'TUB','Tub')
    transact(c,'opening',part,qty='1',cost='1',destination=tub,reason='Fixture opening stock')
    with workshop.app_context():
        batch=stock.reserve_batches(part,1,'b'*32)[0]; old_batch=batch.code;db.session.commit()
    data=dict(version='1',name='Printed body',active='yes',yield_qty='4',
        material_id=str(raw),material_qty='40',material_unit='g',code='CORRECTED')
    assert c.post(f'/production/item/{part}/edit',data=data).status_code==400
    data['confirm_barcode']='yes'
    assert c.post(f'/production/item/{part}/edit',data=data).status_code==303
    with workshop.app_context():
        row=db.session.get(Item,part)
        assert row.identity.code=='CORRECTED' and row.materials[0].qty==40*stock.SCALE
        assert stock.balance(part)==stock.SCALE and Movement.query.count()==1
        assert row.version==2
        from app.inventory.models import ProductionBatch
        assert ProductionBatch.query.one().code==old_batch
        event=CatalogueOperation.query.filter_by(operation_type='inventory_item_maintenance').one()
        assert json.loads(event.scope)['before']=='BODY'
    assert c.get('/production/item/barcode-check',query_string=dict(code='CORRECTED',item_id=part)).json=={'available':True}
    data.update(version='2',code='RAW')
    assert c.post(f'/production/item/{part}/edit',data=data).status_code==400
    data.update(code=old_batch)
    assert c.post(f'/production/item/{part}/edit',data=data).status_code==400
    data.update(code='bad barcode')
    assert c.post(f'/production/item/{part}/edit',data=data).status_code==400
    data.update(version='1',code='ANOTHER')
    assert c.post(f'/production/item/{part}/edit',data=data).status_code==400


def test_maintenance_admin_only_and_csrf_protected(workshop):
    c=client_for(workshop);iid=item(c,'purchased','SAFE','Safe')
    assert client_for(workshop,3).post(f'/production/item/{iid}/delete').status_code==403
    workshop.config['WTF_CSRF_ENABLED']=True
    assert c.post(f'/production/item/{iid}/delete',data=dict(version='1',confirmed='yes',confirm_code='SAFE')).status_code==400


def test_migration_upgrade_preserves_existing_records_and_immutability(workshop):
    import sqlite3
    from alembic import command
    from app.database import _alembic_config
    c=client_for(workshop);iid=item(c,'purchased','KEPT','Kept')
    tub=location(c,'TUB','Tub')
    transact(c,'opening',iid,qty='1',cost='1',destination=tub,reason='Existing history')
    with workshop.app_context():
        url=str(db.engine.url);path=db.engine.url.database
        db.session.remove();db.engine.dispose()
    command.downgrade(_alembic_config(url),'0031_proof_attachment')
    def records():
        with sqlite3.connect(path) as conn:
            return [conn.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() for table in
                ['inventory_identity','inventory_item','inventory_transaction','inventory_movement']]
    before=records()
    command.upgrade(_alembic_config(url),'head')
    assert records()==before
    with sqlite3.connect(path) as conn:
        for sql in ["UPDATE inventory_identity SET code='UNAUTHORISED' WHERE id=?",
                    "UPDATE inventory_identity SET kind='location' WHERE id=?",
                    "DELETE FROM inventory_identity WHERE id=?"]:
            with pytest.raises(sqlite3.IntegrityError): conn.execute(sql,(iid,))
        with pytest.raises(sqlite3.IntegrityError): conn.execute("UPDATE inventory_transaction SET reason='changed'")
