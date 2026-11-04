"""Local editor/scan/projection and real CSV responses; no live services."""
import copy
import csv
import io
import json
import time
from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app import db, shopify_drafts as drafts
from app.models import Product, ShopifyPublishJob, CatalogueOperation, ShopifyProductIdentity
from app.royal_mail_export import generate, COLUMNS, country_names
from app.taxonomy_assignments import product_document
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
from test_metadata_editor_core import core, save
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app
from test_shopify_simple_updates import created, change
from test_shopify_variables import variable_world, create as create_variable


def read_csv(response):
    return list(csv.DictReader(io.StringIO(response.data.decode('utf-8'))))


@pytest.fixture
def export_world(core, monkeypatch):
    app, client, root, output, *_ = core
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('Remote request'))
    with app.app_context():
        for name in ('Simple Collection', 'Variable Collection', 'Single Variable'):
            path = root/name/'product_info.json'
            document = json.loads(path.read_text())
            document.update(live=True, customs_description='Greeting card', harmonized_system_code='001234', country_of_origin='GB')
            path.write_text(json.dumps(document))
            op = acquire_catalogue_operation('shared_collection_update', {'collection_relpath':name})
            rows = scan_collection(root/name, 'https://invalid.example/', output,
                force_update=True, operation_id=op.id, defer_markers=True, log=lambda *a, **k: None)
            result = ingest_rows_to_db(rows, operation_id=op.id, log=lambda *a, **k: None)
            assert not result['products_failed']
            finish_catalogue_operation(op.id, status='succeeded')
    return core


def test_csv_live_simple_and_each_variable_variant_no_parent(export_world):
    app, client, *_ = export_world
    response = client.get('/packaging/royal-mail.csv')
    assert response.status_code == 200, response.json
    assert 'no-store' in response.headers['Cache-Control']
    assert list(read_csv(response)[0]) == list(COLUMNS)
    with app.app_context():
        parents = Product.query.filter_by(product_type='variable').all()
        simple = Product.query.filter_by(product_type='simple').all()
        expected = {p.sku for p in simple} | {v.sku for p in parents for v in p.variations if v.visible is not False}
        rows = read_csv(response)
        assert {r['SKU'] for r in rows} == expected
        assert not {p.sku for p in parents} & {r['SKU'] for r in rows}
        assert len(rows) == len(expected) == int(response.headers['X-Export-Row-Count'])
        for row in rows:
            assert row['Non-standardised Manufacturer ID'] == row['SKU']
            assert row['HS Code'] == '001234'
            assert row['Country of Origin'] == 'United Kingdom'
            assert row['Customs Description'] == 'Greeting card'
        product = simple[0]
        row = next(r for r in rows if r['SKU'] == product.sku)
        assert row['Price (GBP)'] == format(product.regular_price, '.2f')
        assert Decimal(row['Weight (KG)']) == product.weight/1000
        for parent in parents:
            for variant in parent.variations:
                row = next(r for r in rows if r['SKU'] == variant.sku)
                assert row['Price (GBP)'] == format(variant.regular_price, '.2f')
                assert Decimal(row['Weight (KG)']) == variant.weight/1000


@pytest.mark.parametrize('broken_view',[False,True])
def test_reported_nbnoc_draft_missing_evidence_does_not_block_live_rows(export_world,broken_view):
    app,client,*_=export_world
    with app.app_context():
        draft=Product(sku='NBNOC-0001',title='Fictional local Draft',product_type='simple',
            source_relpath='Draft Collection/Naughty Ornament',published=False,status='draft')
        db.session.add(draft);db.session.flush()
        if broken_view:
            db.session.add(ShopifyPublishJob(product_id=draft.id,source_identity=draft.source_relpath,
                requested_revision='unscanned',state='attention',view_pending=True,view_json='not JSON',
                next_attempt=drafts.now(),updated_at=drafts.now()))
        db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200,response.json
    rows=read_csv(response)
    assert rows and not any(r['SKU']=='NBNOC-0001' for r in rows)
    assert 'NBNOC-0001' not in response.headers['X-Export-Warnings']


@pytest.mark.parametrize('intent',[None,False,'true'])
def test_unknown_or_saved_draft_intent_never_defaults_live(export_world,intent):
    app,client,*_=export_world
    with app.app_context():
        product=Product.query.filter_by(product_type='simple').first();sku=product.sku
        job=db.session.get(ShopifyPublishJob,product.id);saved=json.loads(job.view_json)
        saved['authored'].pop('live',None)
        if intent is not None:saved['authored']['live']=intent
        job.view_json=json.dumps(saved);db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200 and not any(r['SKU']==sku for r in read_csv(response))


def test_inactive_variants_are_excluded_before_price_validation(export_world):
    app,client,*_=export_world
    with app.app_context():
        product=Product.query.filter_by(product_type='variable').first()
        hidden,missing,*_=product.variations
        excluded={hidden.sku,missing.sku,product.sku}
        hidden.visible=False;hidden.regular_price=Decimal('-10')
        missing.catalogue_status='missing';missing.sale_price=Decimal('-10')
        db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200
    assert not excluded & {r['SKU'] for r in read_csv(response)}
    assert 'Invalid selling price' not in response.headers['X-Export-Warnings']


def test_all_optional_customs_price_and_weight_missing_still_exports(export_world):
    app,client,*_=export_world
    with app.app_context():
        product=Product.query.filter_by(product_type='simple').first();sku=product.sku
        product.regular_price=None;product.weight=None
        job=db.session.get(ShopifyPublishJob,product.id);saved=json.loads(job.view_json)
        for key in ('price','sale_price','customs_description','harmonized_system_code','country_of_origin'):
            saved['authored'].pop(key,None)
        job.view_json=json.dumps(saved);db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200
    row=next(r for r in read_csv(response) if r['SKU']==sku)
    assert all(row[k]=='' for k in ('Price (GBP)','Weight (KG)','Customs Description','HS Code','Country of Origin'))


def test_cross_parent_variant_duplicate_excludes_both_rows(export_world):
    app,client,*_=export_world
    with app.app_context():
        simple=Product.query.filter_by(product_type='simple').first();sku=simple.sku
        variant=Product.query.filter_by(product_type='variable').first().variations[0]
        variant.sku=sku;db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200
    assert not any(r['SKU'].casefold()==sku.casefold() for r in read_csv(response))
    assert json.loads(response.headers['X-Export-Warnings'])['Skipped products/rows']==2


def test_unrelated_draft_collection_refresh_does_not_block_live_export(export_world):
    app,client,*_=export_world
    with app.app_context():
        db.session.add(CatalogueOperation(id='b'*32,operation_type='shared_collection_update',status='running',
            scope=json.dumps({'collection_relpath':'Draft Collection'})))
        db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200 and read_csv(response)


def test_live_collection_child_draft_save_is_excluded_after_existing_scoped_scan(export_world,monkeypatch):
    from app.utils import scan_runner
    app,client,_,_,ids,*_=export_world
    product_id=ids['Simple Collection/Blue Token']
    monkeypatch.setattr('app.routes.start_scan',scan_runner.start_scan)
    monkeypatch.setattr('app.scheduled_tasks.queue_projected',lambda op:None)
    response=save(export_world,data={'live':False})
    assert response.status_code==200,response.json
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        with app.app_context():progress=scan_runner.get_progress(response.json['run_id'])
        if progress['status'] in {'done','error'}:break
        time.sleep(.03)
    assert progress['status']=='done',progress
    with app.app_context():
        product=db.session.get(Product,product_id);sku=product.sku
        assert product.published is False and product_document(product)['live'] is False
        # Model the reported missing/refreshing Draft view without changing its
        # now-authoritative scanned Draft flag. Export must not need that view.
        job=db.session.get(ShopifyPublishJob,product_id);job.view_json=None;job.scan_operation_id=None
        db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200 and not any(r['SKU']==sku for r in read_csv(response))


@pytest.mark.parametrize('name,kind', [('Simple Collection/Blue Token','shared'),
                                     ('Variable Collection/Badge One','shared'),
                                     ('Single Variable','shared'),
                                     ('Simple Collection/Blue Token','override')])
def test_actual_save_scoped_scan_projection_reload_clear(export_world, monkeypatch, name, kind):
    from app.utils import scan_runner
    app, client, root, _, ids, *_ = export_world
    monkeypatch.setattr('app.routes.start_scan', scan_runner.start_scan)
    monkeypatch.setattr('app.scheduled_tasks.queue_projected', lambda op: None)
    with app.app_context():
        product = db.session.get(Product, ids[name]); job = db.session.get(ShopifyPublishJob, product.id)
        baseline = (job.requested_revision, job.generation, job.state)
    def edit(value):
        result = save(export_world, name, kind=kind, data={'customs_description':value})
        assert result.status_code == 200, result.json
        limit = time.monotonic()+15
        while time.monotonic()<limit:
            with app.app_context(): progress = scan_runner.get_progress(result.json['run_id'])
            if progress['status'] in {'done','error'}: break
            time.sleep(.03)
        assert progress['status']=='done', progress
        with app.app_context():
            db.session.expire_all()
            product = db.session.get(Product, ids[name]); job = db.session.get(ShopifyPublishJob, product.id)
            operation = db.session.get(CatalogueOperation, result.json['operation_id'])
            assert operation.status=='succeeded' and json.loads(operation.scope)['collection_relpath']==name.split('/')[0]
            assert job.scan_operation_id == operation.id and not job.view_pending
            assert json.loads(job.view_json)['authored']['customs_description'] == value
            assert (job.requested_revision, job.generation, job.state) == baseline
            assert product_document(product)['customs_description']==value
            assert client.get(f'/edit_products/{product.id}/edit/{kind}').status_code==200
        target = root/name/'product_info.json' if kind=='override' else root/name.split('/')[0]/'product_info.json'
        assert json.loads(target.read_text())['customs_description']==value
    edit('Wooden ornament')
    assert client.get('/packaging/royal-mail.csv').status_code==200
    edit('')
    with app.app_context(): db.session.remove(); db.engine.dispose()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200 and any(r['Customs Description']=='' for r in read_csv(response))


def test_shopify_complete_plan_and_managed_fingerprint_are_unchanged(world):
    from app.automatic_publishing import local_revision
    product, _, _ = world
    product, identity, adapter = created(world)
    before = drafts.local_plan(product); revision = local_revision(product)
    receipt = identity.media_evidence_json
    change(world, customs_description='Wooden ornament')
    assert drafts.local_plan(product)==before and local_revision(product)==revision
    plan, token = drafts.preview(product, adapter)
    assert plan['classification']=='no_change' and token is None
    assert 'customs_description' not in json.dumps(plan)
    assert ShopifyProductIdentity.query.one().media_evidence_json==receipt


def test_variable_shopify_plan_and_fingerprint_unchanged(variable_world):
    from app.automatic_publishing import local_revision
    product, shared, *_ = variable_world
    identity, adapter = create_variable(variable_world)
    before=drafts.local_plan(product); revision=local_revision(product)
    doc=json.loads(shared.read_text());doc['customs_description']='Christmas decoration';shared.write_text(json.dumps(doc))
    assert drafts.local_plan(product)==before and local_revision(product)==revision
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None


def test_export_read_only_no_sources_scanner_api_or_sql_writes(export_world, monkeypatch):
    app,client,root,*_=export_world
    statements=[]
    with app.app_context():
        engine=db.engine
        def trace(conn,cursor,statement,parameters,context,executemany): statements.append(statement)
        event.listen(engine,'before_cursor_execute',trace)
        before={str(p):p.read_bytes() for p in root.rglob('product_info.json')}
        try:
            with (patch('app.taxonomy_assignments.product_document',side_effect=AssertionError('source read')),
                  patch('app.catalogue_images.product_image_diagnostics',side_effect=AssertionError('image walk')),
                  patch('app.routes.start_scan',side_effect=AssertionError('scan'))):
                response=client.get('/packaging/royal-mail.csv')
            assert response.status_code==200, response.json
            assert not any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','REPLACE','CREATE','ALTER')) for s in statements)
            assert before=={str(p):p.read_bytes() for p in root.rglob('product_info.json')}
            assert len(statements)<25
        finally:event.remove(engine,'before_cursor_execute',trace)


def test_csv_escaping_utf8_formula_protection_missing_values_and_no_listing_fallback(export_world):
    app,client,*_=export_world
    with app.app_context():
        product=Product.query.filter_by(product_type='simple').first();sku=product.sku;product_id=product.id
        product.title="Élodie's card, floral";product.weight=Decimal(50)
        job=db.session.get(ShopifyPublishJob,product_id);saved=json.loads(job.view_json)
        saved['display_title']=product.title;saved['authored'].update(customs_description='', description='NOT CUSTOMS')
        saved['authored'].pop('country_of_origin');saved['authored'].pop('harmonized_system_code')
        job.view_json=json.dumps(saved);db.session.commit()
    response=client.get('/packaging/royal-mail.csv');row=next(r for r in read_csv(response) if r['SKU']==sku)
    assert row['Product Name']=="Élodie's card, floral" and row['Weight (KG)']=='0.05'
    assert row['Country of Origin']==row['HS Code']==row['Customs Description']==''
    assert json.loads(response.headers['X-Export-Warnings'])['Missing HS Code']==1
    with app.app_context():
        job=db.session.get(ShopifyPublishJob,product_id);saved=json.loads(job.view_json)
        saved['display_title']='=HYPERLINK("https://invalid.example")';job.view_json=json.dumps(saved);db.session.commit()
    row=next(r for r in read_csv(client.get('/packaging/royal-mail.csv')) if r['SKU']==sku)
    assert row['Product Name'].startswith("'=HYPERLINK")


@pytest.mark.parametrize('problem',['revision','scan','missing_view','duplicate','missing_sku','unsafe_sku','invalid_weight'])
def test_invalid_live_rows_reported_without_blocking_independent_rows(export_world,problem):
    app,client,*_=export_world
    with app.app_context():
        products=Product.query.filter_by(product_type='simple').all();product=products[0]
        original_sku=product.sku
        job=db.session.get(ShopifyPublishJob,product.id)
        if problem=='revision':job.view_revision='stale'
        if problem=='scan':job.scan_operation_id=None
        if problem=='missing_view':job.view_json='{}'
        if problem=='duplicate':products[1].sku=product.sku.lower()
        if problem=='missing_sku':product.sku=None
        if problem=='unsafe_sku':product.sku='=1+1'
        if problem=='invalid_weight':product.weight=Decimal(-1)
        db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==200,response.json
    rows=read_csv(response);warnings=json.loads(response.headers['X-Export-Warnings'])
    assert rows and warnings
    if problem=='invalid_weight':
        assert next(r for r in rows if r['SKU']==original_sku)['Weight (KG)']==''
        assert warnings['Invalid product weight (left blank)']==1
    else:
        assert not any(r['SKU'].casefold()==original_sku.casefold() for r in rows)
        assert warnings['Skipped products/rows']>=1
        if problem=='duplicate':assert warnings['Skipped products/rows']==2
    assert any('Details' in key for key in warnings)


def test_shopify_status_invalidation_does_not_invalidate_scan_bound_local_export(export_world):
    from app.catalogue_views import invalidate
    app,client,*_=export_world
    before=client.get('/packaging/royal-mail.csv')
    with app.app_context():
        product_id=Product.query.filter_by(product_type='simple').first().id
        invalidate(product_id=product_id)
        job=db.session.get(ShopifyPublishJob,product_id)
        job.state='recovery_required';job.error='Uncertain package stage remains protected'
        db.session.commit()
        assert job.view_pending
    after=client.get('/packaging/royal-mail.csv')
    assert after.status_code==200 and after.data==before.data
    with app.app_context():
        job=db.session.get(ShopifyPublishJob,product_id)
        assert job.state=='recovery_required' and job.view_pending and job.error


def test_filename_date_no_live_export_authentication_and_package_page(export_world):
    app,client,*_=export_world
    with app.app_context():
        data,name,count,warnings=generate(at=datetime(2026,7,1,23,30,tzinfo=UTC))
        assert name=='tlc-royal-mail-products-2026-07-02.csv' and count>0
        assert next(csv.reader(io.StringIO(data.decode())))==list(COLUMNS)
        Product.query.update({'published':False});db.session.commit()
        with pytest.raises(ValueError,match='No locally Live products'):
            generate()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==409 and 'No locally Live products' in response.json['error']
    assert 'Content-Disposition' not in response.headers
    page=client.get('/packaging/')
    assert page.status_code==200 and b'Export Royal Mail CSV' in page.data
    assert b'data-export-feedback' in page.data and b'royal-mail-export.js' in page.data
    assert app.test_client().get('/packaging/royal-mail.csv').status_code in {302,401}
    assert country_names()['GB']=='United Kingdom'


@pytest.mark.parametrize('value',[None,True,123,'x'*51,'Line\nbreak','\tGreeting card'])
def test_invalid_customs_description_not_persisted(core,value):
    response=save(core,kind='shared',data={'customs_description':value})
    assert response.status_code==400 and not core[5]


def test_current_selling_price_no_vat_and_missing_weight_warning(export_world):
    app,client,*_=export_world
    with app.app_context():
        product=Product.query.filter_by(product_type='simple').first();sku=product.sku
        product.regular_price=Decimal('4.50');product.sale_price=Decimal('3.50');product.weight=None
        job=db.session.get(ShopifyPublishJob,product.id);saved=json.loads(job.view_json)
        saved['authored'].update(price='4.50',sale_price='3.50')
        saved['authored'].pop('sale_start_date',None);saved['authored'].pop('sale_end_date',None)
        job.view_json=json.dumps(saved);db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    row=next(r for r in read_csv(response) if r['SKU']==sku)
    assert row['Price (GBP)']=='3.50' and row['Weight (KG)']==''
    assert json.loads(response.headers['X-Export-Warnings'])['Missing product weight']==1


def test_active_source_refresh_blocks_export_not_publishing(export_world):
    app,client,*_=export_world
    with app.app_context():
        db.session.add(CatalogueOperation(id='a'*32,operation_type='append',status='running'))
        db.session.commit()
    response=client.get('/packaging/royal-mail.csv')
    assert response.status_code==409 and 'refresh is in progress' in response.json['error']


def test_customs_only_actual_save_keeps_linked_live_verified_and_not_requeued(export_world,monkeypatch):
    from app import catalogue_views as views
    from app.utils import scan_runner
    from app.models import ShopifyCollectionIdentity
    from test_edit_publish_lifecycle import LifecycleAdapter, cycle
    from test_automatic_publishing import authorise
    from test_shopify_foundation import TOKEN
    app,client,root,_,ids,*_=export_world
    product_id=ids['Simple Collection/Blue Token']
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN','fictional.myshopify.com')
    monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN',TOKEN)
    monkeypatch.setattr('app.routes.start_scan',scan_runner.start_scan)
    monkeypatch.setattr('app.scheduled_tasks.queue_projected',lambda op:None)
    adapter=LifecycleAdapter()
    def edit(data):
        response=save(export_world,kind='shared',data=data)
        assert response.status_code==200,response.json
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            with app.app_context(): progress=scan_runner.get_progress(response.json['run_id'])
            if progress['status'] in {'done','error'}:break
            time.sleep(.03)
        assert progress['status']=='done',progress
    edit({'live':True,'categories':[]})
    with app.app_context():
        Product.query.filter(Product.id!=product_id).update({'catalogue_status':'missing'})
        ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id!=product_id).delete()
        db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42',source_key='collection:Simple Collection',
            remote_id='gid://shopify/Collection/800',state='verified',evidence_json='{"version":1}'))
        db.session.commit();authorise(adapter);cycle(adapter)
        product=db.session.get(Product,product_id);job=db.session.get(ShopifyPublishJob,product_id)
        baseline=(job.requested_revision,job.generation,job.state,job.verified_revision)
        assert job.state=='verified'
        remote_id=ShopifyProductIdentity.query.one().shopify_product_id
    adapter.calls.clear()
    edit({'customs_description':'Printed wall art'})
    with app.app_context():
        product=db.session.get(Product,product_id);job=db.session.get(ShopifyPublishJob,product_id)
        assert (job.requested_revision,job.generation,job.state,job.verified_revision)==baseline
        with app.test_request_context(): assert views.saved_states([product])[product_id]['state']=='no_change'
        assert not adapter.calls
        assert ShopifyProductIdentity.query.one().shopify_product_id==remote_id
        # Explicitly simulate the existing hourly scheduler: still no API work.
        # The shared scan correctly restored the other local fixture child;
        # isolate this assertion to the linked product under test, as above.
        Product.query.filter(Product.id!=product_id).update({'catalogue_status':'missing'})
        ShopifyPublishJob.query.filter(ShopifyPublishJob.product_id!=product_id).delete()
        db.session.commit()
        cycle(adapter)
        assert not adapter.calls
