"""Isolated operations UI, read-only snapshots, complete galleries; no live API."""
import copy
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from PIL import Image
from app import db, shopify_drafts as drafts, scheduled_tasks as tasks
from app.models import Product, ProductImage, ShopifyProductIdentity, ShopifyPublishJob, CatalogueOperation, ShopifyPublishingEvent, ScheduledTask, ShopifyDueEvent
from app.shopify_connection import ShopifyAdapter, ShopifyError, QUERY, DIMENSION_DEFINITIONS, UPDATE_QUERY
from app.shopify_read_scope import inspection
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_variables import variable_world
from test_shopify_publication_sales import AcceptanceAdapter, VariableAcceptanceAdapter
from test_automatic_publishing import queued, authorise, execute
from test_shopify_simple_updates import change
from test_shopify_taxonomy_products import registry
from test_metadata_editor_core import core


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('app.utils.discord.publishing_message', lambda data: (False, 'disabled'))


def test_reference_reuse_is_read_scope_only_and_cannot_mask_write_readback(world):
    adapter = ShopifyAdapter(); calls = []
    def read(config, query, variables=None, **kwargs):
        calls.append(query)
        adapter._note_request(query, config.api_version, 200, {'requestedQueryCost': 4, 'actualQueryCost': 2})
        return {'value': [len(calls)]}
    config = drafts.configuration()
    with patch.object(adapter, '_request', side_effect=read):
        with inspection() as profile:
            first = adapter._read(config, DIMENSION_DEFINITIONS, {'owner': 'PRODUCT', 'namespace': 'tlc_dashboard'})
            first['value'].append('caller modification')
            assert adapter._read(config, DIMENSION_DEFINITIONS, {'owner': 'PRODUCT', 'namespace': 'tlc_dashboard'}) == {'value': [1]}
            with pytest.raises(ShopifyError, match='Read-only'): adapter._write(config, UPDATE_QUERY, {})
        assert profile['http_requests'] == 1 and profile['cache_hits'] == 1
        assert profile['requested_cost'] == 4 and profile['actual_cost'] == 2
        adapter._read(config, DIMENSION_DEFINITIONS, {'owner': 'PRODUCT', 'namespace': 'tlc_dashboard'})
        with inspection(): adapter._read(config, DIMENSION_DEFINITIONS, {'owner': 'PRODUCT', 'namespace': 'tlc_dashboard'})
        assert len(calls) == 3  # no cross-request/process/publishing cache


def test_errors_are_not_cached_and_cost_is_unknown_when_absent(world):
    adapter = ShopifyAdapter(); count = []
    def read(config, query, variables=None, **kwargs):
        count.append(1)
        adapter._note_request(query, config.api_version, 503)
        raise ShopifyError('required read failed')
    with inspection() as profile, patch.object(adapter, '_request', side_effect=read):
        for _ in range(2):
            with pytest.raises(ShopifyError): adapter._read(drafts.configuration(), QUERY)
    assert len(count) == 2 and profile['cost_observations'] == 0
    assert profile['repeated_identical_requests'] == 1


def test_operations_pages_are_compact_separate_and_read_only(world):
    from flask import current_app
    from app.catalogue_views import capture
    product, *_ = world
    queued(product); capture(product); db.session.commit()
    client = _client(current_app)
    with patch('requests.Session.request', side_effect=AssertionError('network')), patch('app.taxonomy_assignments.product_document', side_effect=AssertionError('JSON walk')):
        overview = client.get('/settings/tasks').get_data(as_text=True)
        assert 'Scheduled Product Releases' in overview and 'Scheduled Sale Transitions' in overview
        assert 'Daily' in overview and 'Schedule settings' not in overview
        queue = client.get('/publishing/queue').get_data(as_text=True)
        assert product.sku in queue and 'Intended / verified Shopify' in queue
        settings = client.get('/settings/publishing').get_data(as_text=True)
        assert 'Save publishing authority' in settings and '<table' not in settings
        assert client.get('/operations/events').status_code == 200
    assert not db.session.get(ScheduledTask, tasks.PUBLISHER_KEY).enabled


def test_event_history_has_real_details_delivery_filters_and_legacy_unknown(world):
    from flask import current_app
    product,*_=world; change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter)
    assert execute(product,adapter)=='verified'
    event=ShopifyPublishingEvent.query.filter_by(event_type='verified').first()
    assert event
    receipt=json.loads(event.receipt_json)
    receipt['changes']=[{'field':'price','label':'Price','before':'£4.50','after':'£5.00'}]
    event.receipt_json=json.dumps(receipt);event.trigger='hourly'
    op=db.session.get(CatalogueOperation,event.operation_id)
    op.finished_at=datetime(2026,6,1,12)
    old=CatalogueOperation(id='legacy-event',operation_type='shopify_draft_publish',scope='{}',status='succeeded',
        recovery_state='none',started_at=drafts.now(),finished_at=drafts.now())
    db.session.add(old);db.session.commit()
    client=_client(current_app)
    page=client.get('/operations/events?trigger=hourly').get_data(as_text=True)
    assert '1 events' in page and 'BST' in page and '£4.50' in page and '£5.00' in page
    detail=client.get('/operations/events/'+op.id).get_data(as_text=True)
    assert 'Original operation' in detail and 'Discord:' in detail
    legacy=client.get('/operations/events/legacy-event').get_data(as_text=True)
    assert 'Detailed previous field values are unavailable' in legacy
    assert CatalogueOperation.query.count() >= 2


def test_queue_filters_pagination_and_unknown_scan_evidence_do_not_invent_authority(world):
    from flask import current_app
    product,*_=world
    for i in range(27):
        p=Product(sku=f'QUEUE-{i}',title=f'Queued {i}',catalogue_status='active',collection_id=product.collection_id,source_relpath=f'Example/{i}')
        db.session.add(p);db.session.flush()
        db.session.add(ShopifyPublishJob(product_id=p.id,source_identity=p.source_relpath,requested_revision='unscanned',state='attention',trigger='bootstrap',next_attempt=drafts.now(),updated_at=drafts.now()))
    db.session.commit();client=_client(current_app)
    page=client.get('/publishing/queue?state=attention').get_data(as_text=True)
    assert '27 products' in page and 'Next' in page and '/publishing/scan/' in page
    last=client.get('/publishing/queue?state=attention&page=2').get_data(as_text=True)
    assert '27 products' in last and 'Previous' in last
    assert '1 products' in client.get('/publishing/queue?q=QUEUE-26').get_data(as_text=True)


def gallery(product, folder, count):
    product.images.clear()
    # Disposable fixture starts with two original files; source discovery rightly
    # includes those even after removing projected image rows.
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in {'.png', '.jpg'}: path.unlink()
    for i in range(count):
        name=f'gallery-{i:03}.png';Image.new('RGB',(5,5),'red').save(folder/name)
        product.images.append(ProductImage(url=name,position=i,alt_text=f'Artwork {i}'))
    db.session.commit()


@pytest.mark.parametrize('count',[1,20,21,53,250])
def test_complete_gallery_creation_and_readback_no_truncation(world,count):
    product,folder,_=world;gallery(product,folder,count)
    class BatchAdapter(AcceptanceAdapter):
        def stage_images(self,config,images):
            assert len(images)<=20
            return super().stage_images(config,images)
    adapter=BatchAdapter()
    plan,token=drafts.preview(product,adapter)
    row=drafts.confirm(token,True,adapter)
    while drafts.upload_pending(row):
        assert adapter.remote is None  # no Create until all uploads are acknowledged
        _,token=drafts.preview(product,adapter)
        row=drafts.confirm(token,True,adapter)
    assert row.state=='verified',drafts.evidence(row).get('error')
    assert len(drafts.evidence(row)['created_media'])==len(adapter.remote['media']['nodes'])==count
    assert adapter.calls.count('binary_upload')==count
    assert adapter.calls.count('product_create')==1


def test_53_images_processing_resume_reuses_media_ids_and_does_not_upload_again(world):
    product,folder,_=world;gallery(product,folder,53);change(world,live=True)
    adapter=AcceptanceAdapter();adapter.processing=True;authorise(adapter)
    assert execute(product,adapter)=='retry'
    for _ in range(4):
        if adapter.remote:break
        assert execute(product,adapter)=='retry'
    assert adapter.remote
    identity=ShopifyProductIdentity.query.one();ids=[r['id'] for r in adapter.remote['media']['nodes']]
    for i,node in enumerate(adapter.remote['media']['nodes']):node.update(status='READY',image={'url':f'https://cdn.shopify.com/ready-{i}.png'})
    assert execute(product,adapter)=='verified'
    assert identity.shopify_product_id==adapter.remote['id']
    assert ids==[r['id'] for r in adapter.remote['media']['nodes']]
    assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1


def test_gallery_above_platform_input_bound_is_blocked_without_truncation(world):
    product,folder,_=world;gallery(product,folder,251)
    with pytest.raises(ShopifyError,match='251 images; limit 250'):drafts.local_plan(product)
    assert len(product.images)==251


def test_known_partial_upload_restart_continues_without_reuploading_acknowledged_files(world):
    from app import automatic_publishing as publisher
    product,folder,_=world;gallery(product,folder,53);change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter)
    assert execute(product,adapter)=='retry'
    row=ShopifyProductIdentity.query.one();job=db.session.get(ShopifyPublishJob,product.id)
    assert drafts.upload_pending(row) and len(drafts.evidence(row)['upload_inputs'])==20
    assert adapter.remote is None
    job.owner='expired-owner';job.running_revision=job.requested_revision;db.session.commit()
    publisher.recover_jobs('new-owner')
    assert job.state=='pending' and drafts.upload_pending(row)
    assert execute(product,adapter)=='retry'
    assert len(drafts.evidence(row)['upload_inputs'])==40
    assert execute(product,adapter)=='verified'
    assert adapter.calls.count('binary_upload')==53 and adapter.calls.count('product_create')==1
    assert not any('parameters' in u for u in drafts.evidence(row)['upload_inputs'])


def test_uncertain_upload_is_not_treated_as_an_acknowledged_resumable_file(world):
    product,folder,_=world;gallery(product,folder,53);change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter)
    assert execute(product,adapter)=='retry'
    adapter.failure='upload'
    assert execute(product,adapter)=='recovery_required'
    count=adapter.calls.count('binary_upload')
    assert not drafts.upload_pending(ShopifyProductIdentity.query.one())
    assert execute(product,adapter)=='recovery_required'
    assert adapter.calls.count('binary_upload')==count and adapter.remote is None


def test_large_unchanged_gallery_is_not_uploaded_and_reorder_retains_media_ids(world):
    product,folder,_=world;gallery(product,folder,53)
    adapter=AcceptanceAdapter()
    for _ in range(4):
        _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
        if row.state=='verified':break
    ids={n['id'] for n in adapter.remote['media']['nodes']};uploads=adapter.calls.count('binary_upload')
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'
    for image in product.images:image.position=52-image.position
    db.session.commit()
    plan,token=drafts.preview(product,adapter)
    assert token and not plan['blockers']
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert ids=={n['id'] for n in adapter.remote['media']['nodes']}
    assert adapter.calls.count('binary_upload')==uploads


@pytest.mark.parametrize('change_kind',['added','removed'])
def test_existing_destructive_gallery_changes_remain_explicit_blockers(world,change_kind):
    product,folder,_=world;gallery(product,folder,3);adapter=AcceptanceAdapter()
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    before=copy.deepcopy(adapter.remote['media'])
    if change_kind=='added':
        Image.new('RGB',(5,5),'blue').save(folder/'extra.png')
        product.images.append(ProductImage(url='extra.png',position=3))
    else:
        (folder/'gallery-002.png').unlink()
        db.session.delete(next(i for i in product.images if i.url=='gallery-002.png'))
    db.session.commit()
    with pytest.raises(ShopifyError,match='Unsupported media addition/removal'):
        drafts.preview(product,adapter)
    assert adapter.remote['media']==before


def test_variable_53_image_gallery_batched_creation_preserves_variant_identities(variable_world):
    from app.catalogue_images import _source_context
    product=variable_world[0];_,folder,_=_source_context(product)
    initial=len(drafts.local_plan(product)['images'])
    for i in range(53-initial):
        name=f'large-{i:03}.png';Image.new('RGB',(5,5),'yellow').save(folder/name)
        product.images.append(ProductImage(url=name,position=100+i))
    db.session.commit()
    adapter=VariableAcceptanceAdapter()
    assert len(drafts.local_plan(product)['images'])==53
    for _ in range(4):
        _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
        if row.state=='verified':break
        assert drafts.upload_pending(row)
    assert row.state=='verified' and len(adapter.remote['media']['nodes'])==53
    ids={v['id'] for v in adapter.remote['variants']['nodes']}
    assert len(ids)==4 and adapter.calls.count('product_create')==1
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'
    assert ids=={v['id'] for v in adapter.remote['variants']['nodes']}


def test_release_and_sale_tasks_are_independently_scheduled_and_mutually_exclusive(world):
    release=db.session.get(ScheduledTask,tasks.RELEASE_KEY)
    assert release.interval_minutes==5
    sale_run=tasks.enqueue(tasks.SALE_KEY,manual=True)
    tasks.enqueue(tasks.RELEASE_KEY,manual=True)
    assert tasks.claim('owner-one')==(tasks.SALE_KEY,sale_run)
    assert tasks.claim('owner-two') is None


def test_existing_selected_reconciliation_interval_is_not_overwritten(world):
    task=db.session.get(ScheduledTask,tasks.KEY)
    task.interval_minutes=60;db.session.commit()
    tasks.snapshot(task)
    assert task.interval_minutes==60
    assert ScheduledTask(key='default-contract',next_due=drafts.now()).interval_minutes is None  # SQL default applies at insertion
    new=ScheduledTask(key='default-contract',next_due=drafts.now());db.session.add(new);db.session.flush()
    assert new.interval_minutes==1440


def test_registry_slice_snapshot_is_discarded_and_later_changes_are_visible(world,registry):
    from app.taxonomy_registry import registry_inspection,load_configured_registry
    with registry_inspection():
        before=load_configured_registry()
        data=json.loads(registry.read_text());data['tags'].append({'key':'new-tag','name':'New tag','slug':'new-tag','state':'active'})
        registry.write_text(json.dumps(data))
        assert load_configured_registry().snapshot.digest==before.snapshot.digest
    assert load_configured_registry().snapshot.digest!=before.snapshot.digest


@pytest.mark.parametrize('source',['Simple Collection/Blue Token','Variable Collection/Badge One','Single Variable'])
def test_scan_guidance_uses_actual_collection_scope_without_rewriting_json(core,source,monkeypatch):
    from app.utils.operation_control import finish_catalogue_operation
    app,client,root,_,ids,*_=core
    before={p:p.read_bytes() for p in root.rglob('product_info.json')}
    calls=[];monkeypatch.setattr('app.utils.scan_runner.start_scan',lambda *a,**k:calls.append(k))
    result=client.post(f'/publishing/scan/{ids[source]}',data={'confirmed':'yes'})
    assert result.status_code==302
    assert len(calls)==1 and calls[0]['scan_mode']=='shared_collection'
    assert calls[0]['collection_relpath']==source.split('/')[0]
    assert calls[0]['collection_relpath']!='.'
    with app.app_context():finish_catalogue_operation(calls[0]['operation_id'],status='succeeded')
    assert all(p.read_bytes()==content for p,content in before.items())


def test_invalid_scan_context_never_falls_back_to_full_catalogue(world,monkeypatch):
    from flask import current_app
    product,*_=world
    product.source_relpath='../../outside';db.session.commit()
    calls=[];monkeypatch.setattr('app.utils.scan_runner.start_scan',lambda *a,**k:calls.append(k))
    response=_client(current_app).post(f'/publishing/scan/{product.id}',data={'confirmed':'yes'})
    assert response.status_code==409 and not calls


def test_scoped_scan_action_does_not_duplicate_an_active_catalogue_operation(world,monkeypatch):
    from flask import current_app
    from app.utils.operation_control import acquire_catalogue_operation,finish_catalogue_operation
    product,*_=world
    operation=acquire_catalogue_operation('shared_collection_update',{'fixture_only':True})
    calls=[];monkeypatch.setattr('app.utils.scan_runner.start_scan',lambda *a,**k:calls.append(k))
    try:
        result=_client(current_app).post(f'/publishing/scan/{product.id}',data={'confirmed':'yes'})
        assert result.status_code==409 and not calls
    finally:finish_catalogue_operation(operation.id,status='succeeded')


def test_in_progress_upload_events_never_claim_reviewed_fields_were_published(world):
    product,folder,_=world;gallery(product,folder,53);change(world,live=True)
    adapter=AcceptanceAdapter();authorise(adapter)
    assert execute(product,adapter)=='retry'
    event=ShopifyPublishingEvent.query.one();data=json.loads(event.receipt_json)
    assert event.event_type=='processing' and data['changes']==[]
    assert 'no ProductCreate' in data['verification_scope']
    assert adapter.remote is None


def test_large_gallery_upload_attempts_do_not_exhaust_post_create_readback_retries(world):
    product,folder,_=world;gallery(product,folder,250);change(world,live=True)
    adapter=AcceptanceAdapter();adapter.processing=True;authorise(adapter)
    for _ in range(14):
        assert execute(product,adapter)=='retry'
        if adapter.remote:break
    assert adapter.remote and db.session.get(ShopifyPublishJob,product.id).attempts>=13
    assert execute(product,adapter)=='retry'  # first real readback poll, not the fourteenth
    row=ShopifyProductIdentity.query.one()
    assert drafts.evidence(row)['automatic_readback_attempts']==1
    assert adapter.calls.count('product_create')==1 and adapter.calls.count('binary_upload')==250


def test_reconciliation_slice_count_covers_bounded_catalogue_and_fast_continuation_preserves_lease(world,monkeypatch):
    from app.models import ScheduledTaskRun
    fixed_now=tasks.now()
    monkeypatch.setattr(tasks,'now',lambda:fixed_now)
    assert tasks.MAX_SLICES*tasks.FULL_PER_SLICE>=tasks.MAX_PRODUCTS
    run_id=tasks.enqueue(tasks.KEY,manual=True)
    assert tasks.claim('progress-owner')==(tasks.KEY,run_id)
    run=db.session.get(ScheduledTaskRun,run_id);data=json.loads(run.data_json)
    tasks.save_progress(tasks.KEY,run_id,'progress-owner',data,pause=True,resume_seconds=1)
    task=db.session.get(ScheduledTask,tasks.KEY)
    assert task.owner is None and task.resume_at-tasks.now()<=timedelta(seconds=1)
    assert tasks.claim('early-owner') is None


@pytest.mark.parametrize('count',[2,20,21,53,250])
def test_real_read_adapter_paginates_media_without_requesting_250_edges_for_small_products(world,count):
    from app.shopify_connection import SIMPLE_QUERY,GALLERY_QUERY
    product_id='gid://shopify/Product/100'
    nodes=[{'id':f'gid://shopify/MediaImage/{1000+i}','alt':f'Art {i}','status':'READY','mediaContentType':'IMAGE','image':{'url':f'https://cdn.shopify.com/{i}.png'}} for i in range(count)]
    calls=[];adapter=ShopifyAdapter()
    def response(config,query,variables=None,**kwargs):
        calls.append(query)
        start=int((variables or {}).get('after','0'))
        end=min(start+20,count)
        return {'product':{'id':product_id,'status':'ACTIVE','updatedAt':'2026-10-27T10:00:00Z','metafield':{'value':'fixture'},
            'media':{'nodes':nodes[start:end],'pageInfo':{'hasNextPage':end<count,'endCursor':str(end)}}}}
    with patch.object(adapter,'_request',side_effect=response):
        remote=adapter.read_simple(drafts.configuration(),product_id,'tlc_dashboard')
    assert remote['media']['nodes']==nodes and not remote['media']['pageInfo']['hasNextPage']
    assert len(calls)==(count+19)//20
    assert 'media(first: 20)' in SIMPLE_QUERY and 'media(first: 250)' not in SIMPLE_QUERY
    assert all(q==GALLERY_QUERY for q in calls[1:])


@pytest.mark.parametrize('defect',['revision','duplicate','cursor','missing_revision','failed_page'])
def test_incomplete_or_unstable_gallery_pagination_never_becomes_complete_evidence(world,defect):
    from app.shopify_connection import GALLERY_QUERY
    adapter=ShopifyAdapter();header={'id':'gid://shopify/Product/100','status':'DRAFT','updatedAt':'2026-10-27T10:00:00Z','metafield':{'value':'fixture'}}
    nodes=[{'id':f'gid://shopify/MediaImage/{i+1}'} for i in range(20)]
    first={**header,'media':{'nodes':nodes,'pageInfo':{'hasNextPage':True,'endCursor':'20'}}}
    if defect=='missing_revision':first.pop('updatedAt')
    def response(config,query,variables=None,**kwargs):
        if query!=GALLERY_QUERY:return {'product':first}
        if defect=='failed_page':raise ShopifyError('Required media page failed')
        page={**header,'media':{'nodes':[{'id':'gid://shopify/MediaImage/21'}],'pageInfo':{'hasNextPage':False}}}
        if defect=='revision':page['updatedAt']='2026-10-27T10:00:01Z'
        if defect=='duplicate':page['media']['nodes']=nodes[:1]
        if defect=='cursor':page['media']['pageInfo']={'hasNextPage':True,'endCursor':'20'}
        return {'product':page}
    with patch.object(adapter,'_request',side_effect=response),pytest.raises(ShopifyError):
        adapter.read_simple(drafts.configuration(),header['id'],'tlc_dashboard')
