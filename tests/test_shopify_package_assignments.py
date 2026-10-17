"""Synthetic reviewed InventoryItem assignments: no real Shopify or postage calls."""
import copy
import json
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from app import db, shopify_drafts as drafts, package_links
from app.packaging_models import Package
from app.packaging_catalogue import save as save_package
from app.shopify_connection import ShopifyAdapter, ShopifyError, PACKAGE_ASSIGN, WRITE_QUERIES
from app.models import CatalogueOperation
from test_packaging import package
from test_shopify_drafts import world
from test_shopify_simple_updates import UpdateAdapter,change
from test_shopify_variables import VariableAdapter,variable_world
from test_phase3_woo_publish_preview import preview_app


class AssignmentMixin:
    permission=True
    assignment_failure=None
    def test_connection(self,config):
        result=super().test_connection(config)
        result['scopes']=[s for s in result['scopes'] if s!='write_inventory']
        if self.permission:result['scopes'].append('write_inventory')
        return result

    def assign_package(self,config,inventory_id,package_id):
        assert config.api_version=='2026-10'
        with db.engine.connect() as connection:
            raw=connection.execute(text('select media_evidence_json from shopify_product_identity')).scalar_one()
            data=drafts.evidence(SimpleNamespace(media_evidence_json=raw))
            assert connection.execute(text('select state from shopify_product_identity')).scalar_one()=='updating'
            proof=next(p for p in data['package_assignments'].values() if p['state']=='pending')
            assert proof['inventory_id']==inventory_id and proof['shipping_package_id']==package_id
            assert proof['source_revision']==data['plan']['digest'] and proof['independently_verified'] is False
            assert connection.execute(text('select status from catalogue_operation where id=:id'),{'id':proof['operation_id']}).scalar_one()=='running'
        self.calls.append(('package_assign',inventory_id,package_id))
        if self.assignment_failure=='timeout':raise ShopifyError('Synthetic lost acknowledgement',category='network')
        if self.assignment_failure=='auth':raise ShopifyError('Synthetic denied authentication',category='authentication')
        if self.assignment_failure=='graphql':raise ShopifyError('Synthetic GraphQL error')
        if self.assignment_failure=='userErrors':return {'inventoryItemUpdate':{'inventoryItem':None,'userErrors':[{'field':['input','measurement','shippingPackageId'],'message':'synthetic rejection'}]}}
        if self.assignment_failure=='incomplete':return {'inventoryItemUpdate':{'userErrors':[]}}
        return {'inventoryItemUpdate':{'inventoryItem':{'id':inventory_id},'userErrors':[]}}


class Adapter(AssignmentMixin,UpdateAdapter):pass
class VariantAdapter(AssignmentMixin,VariableAdapter):pass


def linked(number=123):
    row=save_package(dict(package(),name='Fictional mailer '+str(number)))
    package_links.set_id(row,f'gid://shopify/ShippingPackage/{number}',[])
    return row


def existing(world):
    product=world[0];adapter=Adapter()
    _,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    adapter.calls.clear();adapter.inputs.clear()
    return product,row,adapter


def test_package_only_signed_update_acknowledged_and_not_replayed(world):
    product,row,adapter=existing(world);pkg=linked()
    change(world,packaging={'selected_package_id':pkg.id})
    before=copy.deepcopy(adapter.remote)
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update' and token
    assert plan['optional_packages'][0]['inventory_id']==drafts.evidence(row)['inventory_item_id']
    assert plan['optional_packages'][0]['action']=='assign'
    with pytest.raises(ShopifyError):drafts.confirm(token,False,adapter)
    result=drafts.confirm(token,True,adapter)
    proof=drafts.evidence(result)['package_assignments']['product']
    assert result.state=='verified' and proof['state']=='accepted' and not proof['independently_verified']
    assert proof['shipping_package_id']=='gid://shopify/ShippingPackage/123'
    assert adapter.remote==before and adapter.inputs==[]
    assert len([c for c in adapter.calls if isinstance(c,tuple)])==1
    assert db.session.get(CatalogueOperation,row.operation_id).status=='partial'
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='no_change' and token is None and plan['package_assignments_unverified']
    assert plan['optional_packages'][0]['state']=='Shopify accepted — not independently verified'
    assert not any(f['changed'] for f in plan['fields'])


def test_changed_package_and_central_id_are_source_bound(world):
    product,row,adapter=existing(world);pkg=linked();other=linked(124)
    change(world,packaging={'selected_package_id':pkg.id})
    _,token=drafts.preview(product,adapter)
    package_links.set_id(pkg,'gid://shopify/ShippingPackage/125',['gid://shopify/ShippingPackage/123'])
    with pytest.raises(ShopifyError,match='changed'):drafts.confirm(token,True,adapter)
    assert not any(isinstance(c,tuple) for c in adapter.calls)
    _,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    first_operation=row.operation_id
    change(world,packaging={'selected_package_id':other.id})
    plan,token=drafts.preview(product,adapter)
    assert plan['classification']=='update'
    drafts.confirm(token,True,adapter)
    assert drafts.evidence(row)['package_assignments']['product']['shipping_package_id']=='gid://shopify/ShippingPackage/124'
    historical=json.loads(db.session.get(CatalogueOperation,first_operation).scope)['package_assignments']['product']
    assert historical['state']=='accepted' and historical['shipping_package_id']=='gid://shopify/ShippingPackage/125'


@pytest.mark.parametrize('failure,state',[('timeout','uncertain'),('graphql','uncertain'),('incomplete','uncertain'),('userErrors','rejected'),('auth','rejected')])
def test_assignment_error_classification_and_no_false_readback(world,failure,state):
    product,row,adapter=existing(world);pkg=linked()
    change(world,packaging={'selected_package_id':pkg.id})
    adapter.assignment_failure=failure
    _,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    data=drafts.evidence(row);assert data['package_assignments']['product']['state']==state
    assert not data['package_assignments']['product']['independently_verified']
    if state=='uncertain':
        assert row.state=='uncertain'
        with pytest.raises(ShopifyError):drafts.preview(product,adapter)
        with pytest.raises(ShopifyError):drafts.reconcile(row,adapter=adapter)
        assert row.state=='uncertain'
    else:assert row.state=='verified'
    assert len([c for c in adapter.calls if isinstance(c,tuple)])==1


def test_no_id_or_no_permission_does_not_block_other_product_updates(world):
    product,row,adapter=existing(world);pkg=save_package(dict(package(),name='Local only'))
    change(world,packaging={'selected_package_id':pkg.id},description='Synthetic updated description')
    plan,token=drafts.preview(product,adapter)
    assert plan['optional_packages'][0]['state']=='Shopify Package ID required' and token
    drafts.confirm(token,True,adapter)
    assert adapter.remote['descriptionHtml']=='Synthetic updated description'
    assert not any(isinstance(c,tuple) for c in adapter.calls)
    package_links.set_id(pkg,'gid://shopify/ShippingPackage/123',[]);adapter.permission=False
    change(world,packaging={'selected_package_id':pkg.id},description='Other metadata still supported')
    plan,token=drafts.preview(product,adapter)
    assert 'Missing write_inventory' in plan['optional_packages'][0]['state'] and token
    drafts.confirm(token,True,adapter)
    assert not any(isinstance(c,tuple) for c in adapter.calls)


def test_explicit_no_package_never_sends_undocumented_null_clear(world):
    product,row,adapter=existing(world);pkg=linked()
    change(world,packaging={'selected_package_id':pkg.id})
    _,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    count=len([c for c in adapter.calls if isinstance(c,tuple)])
    change(world,packaging={'selected_package_id':None})
    plan,token=drafts.preview(product,adapter)
    assert token is None and 'manual Shopify clearing' in plan['optional_packages'][0]['state']
    assert len([c for c in adapter.calls if isinstance(c,tuple)])==count
    assert drafts.evidence(row)['package_assignments']['product']['state']=='accepted'


def test_variable_inheritance_and_variant_exception_use_correct_inventory_ids(variable_world):
    product,path,*_=variable_world;pkg=linked();other=linked(124);adapter=VariantAdapter()
    raw=json.loads(path.read_text());raw['packaging']={'selected_package_id':pkg.id}
    wanted=product.variations[0]
    from app.utils.json_utils import json_key
    key=json_key({a.name:a.value for a in wanted.attributes})
    raw.setdefault('variation_modifiers',{}).setdefault(key,{})['packaging']={'selected_package_id':other.id}
    path.write_text(json.dumps(raw))
    plan,token=drafts.preview(product,adapter);row=drafts.confirm(token,True,adapter)
    assert row.state=='verified'
    data=drafts.evidence(row)
    assert len(data['package_assignments'])==len(product.variations)
    for entry in data['plan']['optional_packages']:
        proof=data['package_assignments'][entry['identity']]
        assert proof['inventory_id']==data['variant_maps'][entry['identity']]['inventory_id']
        assert proof['shipping_package_id']==('gid://shopify/ShippingPackage/124' if entry['sku']==wanted.sku else 'gid://shopify/ShippingPackage/123')
    assert drafts.preview(product,adapter)[0]['classification']=='no_change'


def test_package_transport_only_sends_measurement_package_id(monkeypatch):
    from app.shopify_connection import ShopifyConfiguration
    adapter=ShopifyAdapter();calls=[]
    monkeypatch.setattr(adapter,'_transport',lambda *a,**k:calls.append((a,k)))
    adapter.assign_package(ShopifyConfiguration('fictional.myshopify.com','fictional-token-000000','2026-10'),
        'gid://shopify/InventoryItem/250','gid://shopify/ShippingPackage/123')
    assert PACKAGE_ASSIGN in WRITE_QUERIES
    assert calls[0][0][1]==PACKAGE_ASSIGN and calls[0][0][2]=={'id':'gid://shopify/InventoryItem/250','input':{'measurement':{'shippingPackageId':'gid://shopify/ShippingPackage/123'}}}
    assert calls[0][1]=={}  # no2027-01 override and no weight or packed values
