"""Live-acceptance regressions with fictional files, shops and durable receipts."""
import copy
import json
from types import SimpleNamespace
import pytest
from app import db, shopify_drafts as drafts, shopify_attributes as info
from app.models import CatalogueOperation, ShopifyProductIdentity, ShopifyAttributeIdentity, ShopifyProductComparison, User
from app.shopify_connection import ShopifyError
from app.shopify_products import states_for
from test_shopify_drafts import world
from test_shopify_simple_updates import UpdateAdapter, created, change
from test_shopify_taxonomy_products import registry
from test_shopify_taxonomy_lifecycle import informational, LifecycleAdapter
from test_shopify_variables import variable_world, VariableAdapter
from test_phase3_woo_publish_preview import preview_app, _client


def test_hero_sized_review_is_lossless_and_update_retains_media(world):
    product,row,adapter = created(world)
    description = '<p>Personalised Hero Print — custom character, face and message.</p>' * 150
    change(world,description=description)
    # Represent a product already published with substantial metadata, not a new
    # shortened production fixture. Six real fixture images are not necessary to
    # trigger the duplicated current/before/previous description evidence.
    adapter.remote['descriptionHtml'] = description
    old=drafts.evidence(row)
    old['plan']['description']=description
    old['plan']['managed']['product']['descriptionHtml']=description
    drafts.save(row,old)
    change(world,description=description+'<p>Reviewed addition</p>')
    media=copy.deepcopy(adapter.remote['media'])
    plan,token=drafts.preview(product,adapter)
    assert token and len(token)<4096
    assert len(json.dumps({**old,'plan':plan,'previous_verified':{'plan':old['plan']}}).encode())>65536
    result=drafts.confirm(token,True,adapter)
    assert result.state=='verified',drafts.evidence(result).get('error')
    assert adapter.remote['descriptionHtml']==description+'<p>Reviewed addition</p>'
    assert drafts.evidence(result)['plan']['description']==adapter.remote['descriptionHtml']
    assert len(result.media_evidence_json.encode())<=65536
    assert adapter.remote['media']==media and 'stage_write' not in adapter.calls
    assert len(ShopifyProductComparison.query.one().summary_json.encode())<=32768


def test_large_review_source_change_invalidates_confirmation(world):
    product,row,adapter=created(world)
    change(world,description='<p>Hero — detailed authored description</p>'*1000)
    _,token=drafts.preview(product,adapter)
    assert token
    change(world,description='Changed after Review')
    with pytest.raises(ShopifyError,match='changed'):
        drafts.confirm(token,True,adapter)
    assert 'product_update' not in adapter.calls


def test_lossless_evidence_hash_size_and_legacy_compatibility():
    value={'version':1,'plan':{'description':'é'*40000},'images':[{'source':'file.png','sha256':'a'*64}]*20}
    encoded=drafts.encode(value)
    assert json.loads(encoded)['encoding']=='zlib-json-v1'
    row=SimpleNamespace(media_evidence_json=encoded)
    assert drafts.evidence(row)==value
    packed=json.loads(encoded); packed['sha256']='0'*64; row.media_evidence_json=json.dumps(packed)
    with pytest.raises(ShopifyError,match='invalid'): drafts.evidence(row)
    packed['bytes']=drafts.MAX_EVIDENCE_BYTES+1; row.media_evidence_json=json.dumps(packed)
    with pytest.raises(ShopifyError,match='invalid'): drafts.evidence(row)
    assert drafts.evidence(SimpleNamespace(media_evidence_json='{"version":1,"stage":"verified"}'))['stage']=='verified'


def test_large_media_and_variant_manifests_roundtrip():
    plan={'description':'Readable substantive authored HTML. '*5000,
        'images':[{'source':f'Collection/Hero/{i}.png','filename':f'{i}.png','sha256':'a'*64,'alt':'Authored alt '+str(i)} for i in range(20)],
        'variants':[{'sku':f'HERO-{i}','options':[{'name':'Finish','value':str(i)}],'managed':{'price':'4.00','inventoryItem':{'tracked':False}}} for i in range(32)]}
    data={'version':1,'plan':plan,'previous_verified':{'plan':copy.deepcopy(plan)}}
    assert drafts.evidence(SimpleNamespace(media_evidence_json=drafts.encode(data)))==data


def test_retained_simple_gallery_more_than_six_is_not_removed_or_uploaded(world):
    from PIL import Image
    from app.models import ProductImage
    for i in range(6):
        name=f'gallery-{i}.webp'
        Image.new('RGB',(5,5),'red').save(world[1]/name,format='WEBP')
        world[0].images.append(ProductImage(url=name,position=i+2,alt_text=f'Gallery {i}'))
    db.session.commit()
    product,row,adapter=created(world)
    ids=[m['id'] for m in adapter.remote['media']['nodes']]
    assert len(ids)==8
    change(world,description='<p>Large retained description — complete.</p>'*1500)
    _,token=drafts.preview(product,adapter)
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert ids==[m['id'] for m in adapter.remote['media']['nodes']]
    assert 'stage_write' not in adapter.calls


def test_no_informational_assignments_require_no_attribute_scopes(world):
    product,row,adapter=created(world)
    actual=adapter.remote['simpleVariants']['nodes'][0]; actual['inventoryPolicy']='DENY'
    plan,token=drafts.preview(product,adapter)
    assert token and plan['attributes']==[] and not plan['blockers']
    assert drafts.confirm(token,True,adapter).state=='verified'


def test_inventory_only_matching_comparison_cannot_be_full_up_to_date(world,registry):
    product,row,adapter=created(world)
    informational(registry)
    change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    plan,token=drafts.preview(product,adapter,scope='inventory')
    assert plan['classification']=='no_change' and token is None
    with __import__('flask').current_app.test_request_context():
        state=states_for([product])[product.id]
    assert state['state']=='attention'
    data=json.loads(ShopifyProductComparison.query.one().summary_json)
    assert data['scope']=='inventory' and data['blockers']


def test_explicit_inventory_only_defers_required_attributes(world,registry):
    product,row,adapter=created(world)
    informational(registry)
    data=json.loads(registry.read_text()); data['attributes'][0]['terms'][1]['browse_role']='filter'; registry.write_text(json.dumps(data))
    change(world,attributes={'Occasion':['Christmas','Birthday']},variation_attributes=[])
    actual=adapter.remote['simpleVariants']['nodes'][0]; actual['inventoryPolicy']='DENY'
    full,token=drafts.preview(product,adapter)
    assert token is None and any('read scopes' in b for b in full['blockers'])
    assert full['attribute_diagnostics'][0]['name']=='Occasion'
    original=(row.shopify_product_id,actual['id'],actual['inventoryItem']['id'],copy.deepcopy(adapter.remote['media']),row.verified_at)
    plan,token=drafts.preview(product,adapter,scope='inventory')
    assert token and plan['scope']=='inventory' and not plan['blockers']
    result=drafts.confirm(token,True,adapter)
    assert result.state=='scoped_verified' and result.verification_state=='inventory_verified'
    assert actual['inventoryPolicy']=='CONTINUE'
    assert original==(row.shopify_product_id,actual['id'],actual['inventoryItem']['id'],adapter.remote['media'],row.verified_at)
    assert set(adapter.inputs[-1])=={'id','inventoryPolicy'}
    with __import__('flask').current_app.test_request_context():
        assert states_for([product])[product.id]['state']=='attention'
    assert 'stage_write' not in adapter.calls


def test_inventory_uncertainty_is_read_only_reconcilable(world):
    product,row,adapter=created(world)
    adapter.remote['simpleVariants']['nodes'][0]['inventoryPolicy']='DENY'
    _,token=drafts.preview(product,adapter,scope='inventory')
    original=adapter.update_simple_variant
    def lost(*args): original(*args); raise ShopifyError('Response lost')
    adapter.update_simple_variant=lost
    result=drafts.confirm(token,True,adapter)
    assert result.state=='uncertain'
    with pytest.raises(ShopifyError,match='reconcile|Reconcile'):
        drafts.preview(product,adapter,scope='inventory')
    before=len(adapter.inputs)
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='scoped_verified' and len(adapter.inputs)==before


def test_pre_85_variable_inventory_update_keeps_all_identities(variable_world):
    product=variable_world[0]; adapter=VariableAdapter()
    _,token=drafts.preview(product,adapter); row=drafts.confirm(token,True,adapter)
    before=copy.deepcopy(drafts.evidence(row))
    for variant in adapter.remote['variants']['nodes']:
        variant['inventoryPolicy']='DENY'
    # Explicit scoped correction works without any new attribute scopes and
    # verifies the existing retained variant/InventoryItem mappings.
    _,token=drafts.preview(product,adapter,scope='inventory')
    assert token
    count=adapter.calls.count('variants_create')
    result=drafts.confirm(token,True,adapter)
    assert result.state=='scoped_verified',drafts.evidence(result).get('error')
    assert before['variant_maps']==drafts.evidence(result)['variant_maps']
    assert all(v['inventoryPolicy']=='CONTINUE' for v in adapter.remote['variants']['nodes'])
    assert adapter.calls.count('variants_create')==count
    full,token=drafts.preview(product,adapter)
    assert full['classification']=='no_change' and token is None


class AccessAdapter(LifecycleAdapter):
    def update_attribute_access(self,config,kind,remote_id,spec):
        return super().update_attribute_access(config,kind,remote_id,spec)


def test_reviewed_access_repair_preserves_terms_and_multivalue_assignments(world,registry):
    informational(registry)
    change(world,attributes={'Occasion':['Christmas','Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=='verified',drafts.evidence(row).get('error')
    gids={r.source_key:r.remote_id for r in ShopifyAttributeIdentity.query.all()}
    for (kind,_),resource in adapter.resources.items():
        if kind in {'definition','field'}: resource['access']['storefront']='NONE'
    plan,token=drafts.preview(world[0],adapter)
    assert token and sum(r['action']=='Update storefront access' for a in plan['attributes'] for r in a['resources'])==2
    terms=adapter.calls.count('attribute_term_create')
    assert drafts.confirm(token,True,adapter).state=='verified'
    assert gids=={r.source_key:r.remote_id for r in ShopifyAttributeIdentity.query.all()}
    assert adapter.calls.count('attribute_term_create')==terms
    assert len(json.loads(next(iter(adapter.attr_values.values()))['value']))==2
    assert all(r['access']['storefront']=='PUBLIC_READ' for (kind,_),r in adapter.resources.items() if kind in {'definition','field'})


def test_assignment_only_needs_product_write_not_resource_create_scopes(world,registry):
    informational(registry); change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    value=next(iter(adapter.attr_values.values())); value['value']='[]'; value['compareDigest']='different'
    original=adapter.test_connection
    adapter.test_connection=lambda c:{**original(c),'scopes':sorted(info.READ_SCOPES|{'read_products','write_products'})}
    plan,token=drafts.preview(world[0],adapter)
    assert token and not plan['blockers']
    assert drafts.confirm(token,True,adapter).state=='verified'


def test_new_terms_report_specific_missing_write_scope_without_writes(world,registry):
    informational(registry); change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); original=adapter.test_connection
    adapter.test_connection=lambda c:{**original(c),'scopes':sorted(info.READ_SCOPES|{'read_products','write_products','write_metaobject_definitions'})}
    plan,token=drafts.preview(world[0],adapter)
    assert token is None
    assert plan['attributes'][0]['missing_scopes']==['write_metaobjects']
    assert any('Occasion' in b and 'write_metaobjects' in b for b in plan['blockers'])
    assert ShopifyAttributeIdentity.query.count()==0


def test_access_timeout_retains_gids_and_reconciles_without_write_replay(world,registry):
    informational(registry); change(world,attributes={'Occasion':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    definition=next(r for (kind,_),r in adapter.resources.items() if kind=='definition')
    definition['access']['storefront']='NONE'
    original=adapter.update_attribute_access
    def lost(*args): original(*args); raise ShopifyError('Access response lost')
    adapter.update_attribute_access=lost
    _,token=drafts.preview(world[0],adapter); result=drafts.confirm(token,True,adapter)
    assert result.state=='uncertain'
    mapping=ShopifyAttributeIdentity.query.filter_by(state='uncertain').one()
    before=(mapping.remote_id,adapter.calls.count('attribute_access_update'))
    info.reconcile(mapping,adapter,drafts.configuration())
    assert mapping.state=='verified' and before==(mapping.remote_id,adapter.calls.count('attribute_access_update'))
    drafts.reconcile(row,adapter=adapter)
    assert row.state=='verified'


def test_confirm_without_javascript_returns_to_durable_operation(world,monkeypatch):
    product,row,adapter=created(world); change(world,price='4.50')
    _,token=drafts.preview(product,adapter)
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    client=_client(__import__('flask').current_app._get_current_object())
    response=client.post(f'/products/{product.id}/shopify/review',data={'action':'confirm','review':token,'acknowledge':'yes','return_to':'https://other.test/products'})
    assert response.status_code==302 and response.location.startswith('/operations/')
    assert not adapter.inputs


def test_delete_visible_from_product_detail_only_to_admin(world,monkeypatch):
    product,row,adapter=created(world)
    client=_client(__import__('flask').current_app._get_current_object())
    response=client.get(f'/products/{product.id}')
    assert response.status_code==200 and b'Delete from Shopify' in response.data
    assert f'/products/{product.id}/shopify/delete'.encode() in response.data
    assert client.get(f'/products/{product.id}/shopify/delete').status_code==200
    user=db.session.get(User,1); user.is_admin=False; db.session.commit()
    response=client.get(f'/products/{product.id}')
    assert b'Delete from Shopify' not in response.data
    assert client.get(f'/products/{product.id}/shopify/delete').status_code==403


def test_progress_completion_redirect_and_duplicate_post(world,monkeypatch):
    product,row,adapter=created(world)
    change(world,price='4.50')
    _,token=drafts.preview(product,adapter)
    import app.commerce_routes as routes
    monkeypatch.setattr(drafts,'ShopifyAdapter',lambda:adapter)
    client=_client(__import__('flask').current_app._get_current_object())
    path=f'/products/{product.id}/shopify/review'
    body={'action':'confirm','review':token,'acknowledge':'yes','return_to':'/products?q=Hero'}
    response=client.post(path,data=body,headers={'Accept':'application/json'})
    assert response.status_code==202,response.data
    assert response.json['state']=='processing' and response.json['return_url'].startswith('/operations/')
    from test_manual_publishing_worker import step
    step(adapter)
    count=len(adapter.inputs)
    progress=client.post(f'/products/{product.id}/shopify/progress',data={'review':token})
    assert progress.json['state']=='completed'
    assert client.post(path,data=body,headers={'Accept':'application/json'}).json['state']=='completed'
    assert len(adapter.inputs)==count
    assert client.post(f'/products/{product.id+1}/shopify/progress',data={'review':token}).status_code==400


def test_progress_waiting_does_not_write_or_assume_completion(world):
    product=world[0]; adapter=UpdateAdapter(); _,token=drafts.preview(product,adapter)
    client=_client(__import__('flask').current_app._get_current_object())
    response=client.post(f'/products/{product.id}/shopify/progress',data={'review':token})
    assert response.json['state']=='waiting' and ShopifyProductIdentity.query.count()==0
    assert CatalogueOperation.query.filter_by(operation_type='shopify_draft_publish').count()==0


def test_progress_template_mobile_and_no_script_resubmit(world):
    product=world[0]
    client=_client(__import__('flask').current_app._get_current_object())
    response=client.get(f'/products/{product.id}/shopify/review')
    assert response.status_code==200
    assert b'data-shopify-progress' in response.data and b'shopify-publish.js' in response.data
    assert b'inventory_only' in response.data


def test_live_assignment_diagnostic_reads_actual_values_without_persistence(world,registry):
    from app.shopify_attribute_diagnostic import inspect
    informational(registry); change(world,attributes={'Occasion':['Birthday','Christmas']},variation_attributes=[])
    adapter=AccessAdapter(); _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    def entries(config,ids):
        definition=next(copy.deepcopy(r) for (kind,_),r in adapter.resources.items() if kind=='definition')
        return [dict(copy.deepcopy(r),definition=definition,capabilities={'publishable':None}) for (kind,_),r in adapter.resources.items() if kind=='term' and r['id'] in ids]
    adapter.attribute_entries=entries
    before=(row.media_evidence_json,CatalogueOperation.query.count(),ShopifyAttributeIdentity.query.count())
    result=inspect(row,adapter)
    assert result['read_only'] and len(result['assignments'])==1
    assignment=result['assignments'][0]
    assert len(assignment['actual_referenced_entries'])==2
    assert all(t['retained_identity_matches'] and t['storefront_readable'] for t in assignment['actual_referenced_entries'])
    # Definition presence alone must not fabricate product assignments.
    adapter.attr_values={}
    assert inspect(row,adapter)['assignments']==[]
    assert before==(row.media_evidence_json,CatalogueOperation.query.count(),ShopifyAttributeIdentity.query.count())


@pytest.mark.parametrize('public',[False,True])
def test_production_method_follows_explicit_role_not_name(world,registry,public):
    informational(registry); data=json.loads(registry.read_text()); attr=data['attributes'][0]
    attr['name']='Production Method'; attr['browse_role']='filter' if public else 'internal'
    for term in attr['terms']: term.pop('browse_role',None)
    registry.write_text(json.dumps(data))
    change(world,attributes={'Production Method':['Birthday']},variation_attributes=[])
    adapter=AccessAdapter(); plan,token=drafts.preview(world[0],adapter)
    assert plan['attributes'][0]['public'] is public
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified',drafts.evidence(row).get('error')
    assert all(r['access']['storefront']==('PUBLIC_READ' if public else 'NONE') for (kind,_),r in adapter.resources.items() if kind in {'definition','field'})


@pytest.mark.parametrize('outcome',['processing','uncertain','scoped_verified'])
def test_progress_never_claims_completion_for_incomplete_receipts(world,outcome):
    from app.shopify_progress import receipt
    product,row,adapter=created(world)
    _,token=drafts.preview(product,adapter)  # no change has no signed write token
    token=drafts.serializer().dumps({'product_id':product.id,'digest':'a'*64})
    operation=db.session.get(CatalogueOperation,row.operation_id)
    operation.scope=json.dumps({'review_ticket':drafts.digest(token)})
    operation.status='partial'; row.state=outcome; db.session.commit()
    with __import__('flask').current_app.test_request_context():
        value=receipt(product.id,token)
    assert value['state']=={'processing':'processing','uncertain':'recovery_required','scoped_verified':'attention'}[outcome]


def test_progress_csrf_and_scope_validation(world):
    app=__import__('flask').current_app._get_current_object(); client=_client(app)
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post(f'/products/{world[0].id}/shopify/progress',data={'review':'invalid'}).status_code==400
    app.config['WTF_CSRF_ENABLED']=False
    assert client.post(f'/products/{world[0].id}/shopify/progress',data={'review':'invalid'}).status_code==400


def test_diagnostic_cli_enforces_sqlite_read_only(world,tmp_path,monkeypatch,capsys):
    import sqlite3
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from app import shopify_attribute_diagnostic as diagnostic
    product,row,adapter=created(world)
    source=sqlite3.connect(db.engine.url.database)
    destination=sqlite3.connect(tmp_path/'site.db')
    source.backup(destination); source.close(); destination.close()
    def read_only(identity):
        with pytest.raises(OperationalError,match='readonly|read-only'):
            db.session.execute(text('CREATE TABLE diagnostic_must_not_write (id INTEGER)'))
        db.session.rollback()
        return {'read_only':True,'sku':identity.sku}
    monkeypatch.setattr(diagnostic,'inspect',read_only)
    monkeypatch.setattr('sys.argv',['diagnostic','--sku',product.sku,'--instance',str(tmp_path)])
    diagnostic.main()
    assert json.loads(capsys.readouterr().out)['read_only']
