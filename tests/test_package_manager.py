"""Shipping package administration: synthetic observations, no Shopify mutations."""
import copy
import json
from datetime import datetime
import pytest
from app import db, package_reconciliation as service, packaging_catalogue as catalogue, shopify_packaging
from app.packaging_models import Package, PackageLink, PackagingEvidence
from app.shopify_connection import ShopifyError, READ_QUERIES, WRITE_QUERIES
from test_packaging import packaging_app, logged, package
from test_setup_flow import setup_app

CAP={'store_id':'gid://shopify/Shop/42','config':'fictional-config','api_version':'2026-10',
    'package_sync_enabled':False,'admin_url':'https://fictional.myshopify.com/admin/settings/shipping',
    'limitation':'No verified saved-package listing/readback adapter.'}


@pytest.fixture
def manager(packaging_app,monkeypatch):
    app,path=packaging_app
    monkeypatch.setattr(shopify_packaging,'current',lambda:copy.deepcopy(CAP))
    monkeypatch.setattr(shopify_packaging,'refresh',lambda:copy.deepcopy(CAP))
    with app.app_context():
        row=catalogue.save(dict(package(),name='Measured example'))
        yield app,path,row


def remote(row,**changes):
    local=catalogue.view(row)
    result={'id':'gid://shopify/ShippingPackage/123','name':row.name,'type':local['type'],
        'dimensions':dict(local['external_cm'],unit='cm'),'weight':{'value':local['tare_g'],'unit':'g'}}
    result.update(changes);return result


def choice(row,direction='import',retain=False):
    return {'package_id':row.id,'direction':direction,'retain_link':retain}


def test_existing_six_unchanged_on_page_and_preview(packaging_app,monkeypatch):
    app,path=packaging_app
    monkeypatch.setattr(shopify_packaging,'refresh',lambda:(_ for _ in ()).throw(ShopifyError('Unavailable')))
    client=logged(app)
    with app.app_context():before={p.id:(p.name,p.specification) for p in Package.query.all()}
    page=client.get('/packaging/')
    assert page.status_code==200 and b'Shipping Packages' in page.data
    assert b'Add Package' in page.data and b'Sync All' in page.data and b'Reconcile with Shopify' in page.data
    assert b'Not API verified' in page.data
    assert client.post('/packaging/sync').status_code==200
    with app.app_context():assert {p.id:(p.name,p.specification) for p in Package.query.all()}==before


def test_exact_and_normalised_name_candidates_never_auto_link(manager):
    _,_,row=manager
    result=service.compare([remote(row,name='  MEASURED   EXAMPLE  ')])
    assert len(result['records'][0]['candidates'])==1
    assert not result['records'][0]['candidates'][0]['identity_match']
    assert PackageLink.query.count()==0 and PackagingEvidence.query.count()==0


def test_dimension_weight_unit_conversion_and_equal_match(manager):
    _,_,row=manager
    result=service.compare([remote(row,dimensions={'length':180,'width':180,'height':20,'unit':'mm'},weight={'value':'.04','unit':'kg'})])
    assert result['records'][0]['candidates'][0]['compatible']
    value=service.normalise(remote(row,dimensions={'length':1,'width':2,'height':3,'unit':'in'},weight={'value':'1','unit':'oz'}))
    assert value['external_cm']['length']=='2.54' and value['tare_g']=='28.349523125'


def test_duplicate_name_is_ambiguous_and_requires_explicit_target(manager):
    _,_,row=manager
    catalogue.save(dict(package(),name=row.name))
    assert service.compare([remote(row)])['records'][0]['ambiguous']
    with pytest.raises(ValueError):service.preview([remote(row)],[{'direction':'import'}])
    assert PackageLink.query.count()==0


def test_import_review_preserves_tlc_fields_and_invalidates_measurement_proof(manager):
    _,_,row=manager
    before=catalogue.view(row);ident=row.id
    observed=remote(row,name='Remote name',dimensions={'length':'20','width':'19','height':'3','unit':'cm'},weight={'value':'50','unit':'g'})
    plan,token=service.preview([observed],[choice(row,retain=True)])
    assert row.name=='Measured example' and PackageLink.query.count()==0
    with pytest.raises(ValueError):service.confirm(token,False)
    result=service.confirm(token,True)
    assert result['remote_writes']==0 and row.id==ident
    after=catalogue.view(row)
    for field in ('internal_cm','protection','flat','fragile','max_contents_g','max_gross_g','clearance_cm','cost_pence'):
        assert after[field]==before[field]
    assert after['name']=='Remote name' and after['tare_g']=='50'
    assert not after['dimensions_measured'] and not after['tare_measured']
    link=PackageLink.query.one();proof=json.loads(link.observed)
    assert link.package_id==ident and proof['source']=='merchant' and not proof['api_verified']
    assert service.status(after,CAP)['label']=='Shopify API unavailable'
    assert service.status(after,CAP)['verified_at'] is None
    assert logged(manager[0]).get(f'/packaging/{row.id}/edit').status_code==200
    assert service.confirm(token,True)==result and row.version==2


def test_partial_observation_never_clears_unknown_fields_or_local_c5_rule(packaging_app,monkeypatch):
    app,_=packaging_app
    monkeypatch.setattr(shopify_packaging,'current',lambda:copy.deepcopy(CAP))
    with app.app_context():
        row=Package.query.filter_by(seed_key='c5-plus').one()
        observed=remote(row,dimensions={'length':'250','unit':'mm'},weight={})
        plan,token=service.preview([observed],[choice(row)])
        width=next(f for f in plan['rows'][0]['fields'] if f['field']=='external_cm.width')
        assert width['remote'] is None and width['proposed']=='16.5'
        service.confirm(token,True)
        value=catalogue.view(row)
        assert value['external_cm']=={'length':'25.0','width':'16.5','height':None}
        assert value['max_contents_thickness_mm']=='3' and value['tare_g'] is None
        assert all(v is None for v in value['internal_cm'].values())


def test_impossible_import_rejected_without_discarding_usable_interior(manager):
    _,_,row=manager
    with pytest.raises(ValueError,match='interior'):
        service.preview([remote(row,dimensions={'length':'2','width':'2','height':'1','unit':'cm'})],[choice(row)])
    assert row.version==1


def test_tube_not_disguised_as_shopify_box(manager):
    _,_,row=manager
    tube=Package.query.filter_by(seed_key='a4-tube').one()
    assert service.native(catalogue.view(tube))['type'] is None
    with pytest.raises(ValueError,match='Tube'):
        service.preview([remote(row)],[choice(tube)])


def test_local_units_and_incomplete_save_keep_identity(manager):
    app,_,row=manager;client=logged(app)
    data={'name':'Unknown local','type':'BOX','external_cm':{'length':'6','width':'4'},
        'dimension_unit':'in','tare_g':'.04','weight_unit':'kg'}
    response=client.post('/packaging/new',json=data)
    assert response.status_code==201
    value=catalogue.view(db.session.get(Package,response.json['id']))
    assert value['external_cm']=={'length':'15.24','width':'10.16','height':None} and value['tare_g']=='40.00'
    assert all(v is None for v in value['internal_cm'].values())


def test_link_uniqueness_and_stable_id_first_after_rename(manager):
    _,_,row=manager
    observed=remote(row)
    _,token=service.preview([observed],[choice(row,'link',True)]);service.confirm(token,True)
    catalogue.save(dict(catalogue.view(row),name='Local renamed'),row,version=row.version)
    candidate=service.compare([observed])['records'][0]['candidates']
    assert len(candidate)==1 and candidate[0]['id']==row.id and candidate[0]['identity_match']
    other=catalogue.save(dict(package(),name='Other'))
    with pytest.raises(ValueError,match='conflict'):service.preview([observed],[choice(other,'link',True)])
    changed=remote(row,id='gid://shopify/ShippingPackage/456')
    with pytest.raises(ValueError,match='conflict'):service.preview([changed],[choice(row,'link',True)])
    assert PackageLink.query.count()==1


def test_name_only_import_without_gid_never_creates_remote_link(manager):
    _,_,row=manager
    _,token=service.preview([remote(row,id=None)],[choice(row)])
    service.confirm(token,True)
    assert PackageLink.query.count()==0 and row.version==1


def test_link_needs_store_and_actual_gid(manager,monkeypatch):
    _,_,row=manager
    with pytest.raises(ValueError):service.preview([remote(row,id=None)],[choice(row,'link',True)])
    monkeypatch.setattr(shopify_packaging,'current',lambda:{})
    with pytest.raises(ValueError):service.preview([remote(row)],[choice(row,'link',True)])


def test_local_native_edit_pending_but_local_restriction_edit_not_pending(manager):
    _,_,row=manager
    _,token=service.preview([remote(row)],[choice(row,'link',True)]);service.confirm(token,True)
    before=catalogue.view(row)
    catalogue.save(dict(before,max_contents_g='999'),row,version=row.version)
    assert service.status(catalogue.view(row),CAP)['label']=='Shopify API unavailable'
    catalogue.save(dict(catalogue.view(row),name='New Shopify-compatible name'),row,version=row.version)
    assert service.status(catalogue.view(row),CAP)['label']=='Local changes pending'


def test_manual_remote_drift_and_conflict_remain_distinct(manager):
    _,_,row=manager
    _,token=service.preview([remote(row,name='Shopify name')],[choice(row,'link',True)]);service.confirm(token,True)
    assert service.status(catalogue.view(row),CAP)['label']=='Shopify changes detected'
    catalogue.save(dict(catalogue.view(row),name='Different local name'),row,version=row.version)
    assert service.status(catalogue.view(row),CAP)['label']=='Conflict — review needed'


def test_signed_review_rejects_modified_source_and_store_and_tampering(manager,monkeypatch):
    _,_,row=manager
    _,token=service.preview([remote(row,name='Remote changed')],[choice(row)])
    with pytest.raises(ValueError):service.confirm(token+'bad',True)
    catalogue.save(dict(catalogue.view(row),notes='Changed locally'),row,version=row.version)
    with pytest.raises(ValueError,match='changed after review'):service.confirm(token,True)
    _,token=service.preview([remote(row)],[choice(row)])
    monkeypatch.setattr(shopify_packaging,'current',lambda:dict(CAP,config='different'))
    with pytest.raises(ValueError,match='Store'):service.confirm(token,True)


def test_multi_import_atomic_and_repeated_confirmation_idempotent(manager):
    _,_,row=manager
    other=catalogue.save(dict(package(),name='Second'))
    records=[remote(row,name='New first'),remote(other,id='gid://shopify/ShippingPackage/124',name='New second')]
    _,token=service.preview(records,[choice(row),choice(other)])
    catalogue.save(dict(catalogue.view(other),notes='Concurrent edit'),other,version=other.version)
    with pytest.raises(ValueError):service.confirm(token,True)
    db.session.expire_all();assert row.name=='Measured example' and row.version==1
    assert json.loads(PackagingEvidence.query.first().value)['state']=='reviewed'


def test_export_individual_and_sync_all_no_mutations_or_fake_success(manager,monkeypatch):
    app,_,row=manager
    _,token=service.preview([remote(row)],[choice(row,'export',True)])
    result=service.confirm(token,True)
    assert result['rows'][0]['state']=='manual_action_required' and result['remote_writes']==0
    assert row.version==1
    def denied():raise ShopifyError('Read denied')
    monkeypatch.setattr(shopify_packaging,'refresh',denied)
    result=service.sync_preview([row.id])
    assert result['remote_writes']==0 and 'Skipped' in result['rows'][0]['result']
    assert 'API unavailable' in result['failure']
    result=service.sync_preview()
    assert len(result['rows'])==7 and sum('no verified' in r['result'] for r in result['rows'])==6
    client=logged(app)
    response=client.post('/packaging/sync',data={'package_id':row.id})
    assert response.status_code==200 and b'API unavailable during this capability check' in response.data
    assert not any('shippingPackage' in q for q in WRITE_QUERIES)


def test_read_only_capability_probe_and_verified_admin_url(packaging_app,monkeypatch):
    from app.shopify_drafts import configuration
    app,_=packaging_app
    monkeypatch.setenv('SHOPIFY_STORE_DOMAIN','fictional.myshopify.com');monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN','fictional_admin_token_for_tests')
    raw={'shop':{'id':CAP['store_id'],'myshopifyDomain':'fictional.myshopify.com'},'queryRoot':{'fields':[]},
        'mutations':{'fields':[{'name':'shippingPackageUpdate'}]},'measurement':None,'measurementInput':None,'package':None,
        'shopType':{'fields':[]},'packageInput':{'inputFields':[{'name':k} for k in ('name','type','dimensions','weight','default')]},
        'packageTypes':{'enumValues':[{'name':k} for k in ('BOX','ENVELOPE','SOFT_PACK','FLAT_RATE')]},'updatePayload':{'fields':[{'name':'userErrors'}]}}
    calls=[]
    class Adapter:
        def test_connection(self,c):calls.append('connection_read');return {'shop_id':CAP['store_id'],'scopes':['read_products']}
        def _read(self,c,q):assert q in READ_QUERIES;calls.append('capability_read');return raw
    with app.app_context():
        result=shopify_packaging.refresh(Adapter())
        assert result['package_update_schema'] and not result['package_sync_enabled'] and not result['package_create_schema']
        assert result['admin_url']==CAP['admin_url'] and calls==['connection_read','capability_read']
        assert shopify_packaging.current()['store_id']==CAP['store_id']
        monkeypatch.setenv('SHOPIFY_ADMIN_ACCESS_TOKEN','rotated_fictional_token_for_tests')
        assert 'admin_url' not in shopify_packaging.current()


@pytest.mark.parametrize('bad',[[],{},[{}],False])
def test_invalid_or_unbounded_observations_rejected(manager,bad):
    with pytest.raises(ValueError):service.compare(bad)


def test_duplicate_gids_and_targets_never_merge(manager):
    _,_,row=manager
    with pytest.raises(ValueError,match='twice'):service.compare([remote(row),remote(row)])
    with pytest.raises(ValueError,match='several'):service.preview([remote(row),remote(row,id=None)],[choice(row),choice(row)])


def test_private_routes_csrf_and_safe_customer_strings(manager):
    app,_,row=manager;client=logged(app)
    with app.app_context():assert app.test_client().get('/packaging/reconcile').status_code in (302,401)
    assert client.get('/packaging/reconcile').status_code==200
    assert client.post('/packaging/reconcile/compare',json={'records':[remote(row,name='<script>not-executed</script>')]}).status_code==200
    app.config['WTF_CSRF_ENABLED']=True
    for path in ('/packaging/reconcile/compare','/packaging/reconcile/preview','/packaging/reconcile/confirm','/packaging/sync'):
        assert client.post(path,json={}).status_code==400
