"""M3A.8.5 fictional API evidence only; no production files/store calls."""
import copy
import json
import sqlite3
from datetime import timedelta
import pytest
from app import db, shopify_drafts as drafts, shopify_lifecycle as lifecycle, shopify_attributes as info
from app.models import ShopifyProductIdentity, ShopifyCollectionIdentity, ShopifyAttributeIdentity, CatalogueOperation, ShopifyProductComparison
from app.shopify_connection import ShopifyError
from app.shopify_products import states_for
from app.database import _alembic_config, ensure_database
from app.utils.operation_control import prune_operation_history
from test_shopify_taxonomy_products import registry, TaxonomyAdapter
from test_shopify_drafts import world
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world, VariableAdapter
from test_shopify_variables import rescan
from test_phase3_woo_publish_preview import preview_app, _client


class LifecycleAdapter(TaxonomyAdapter):
    def __init__(self):
        super().__init__(); self.deleted=False; self.delete_failure=None; self.resources={}; self.attr_values={}; self.resource_failure=None
    def test_connection(self,config):
        shop=super().test_connection(config)
        shop["scopes"]+=list(info.READ_SCOPES|info.WRITE_SCOPES|{"read_publications"})
        return shop
    def lifecycle_product(self,config,pid):
        self.calls.append("existence_read")
        if self.deleted: return None
        if self.delete_failure=="read": raise ShopifyError("Read permission unavailable")
        result=copy.deepcopy(self.remote)
        result["variants"]=result["simpleVariants"]
        return result
    def delete_product(self,config,pid):
        row=ShopifyProductIdentity.query.one()
        assert row.state=="deleting" and drafts.evidence(row)["lifecycle"]["state"]=="delete_pending"
        self.calls.append("delete_write")
        if self.delete_failure!="survive": self.deleted=True
        if self.delete_failure: raise ShopifyError("Deletion response lost")
        return {"productDelete":{"deletedProductId":pid,"userErrors":[]}}
    def create_collection(self,config,data):
        row=ShopifyCollectionIdentity.query.filter_by(state="creating").one(); assert row.operation_id
        self.calls.append("collection_create")
        gid=f"gid://shopify/Collection/{900+len(self.collections)}"
        self.collections[gid]={"title":data["title"],"handle":data["handle"],"marker":data["metafields"][0]["value"],"members":set()}
        if self.collection_failure: raise ShopifyError("Collection outcome lost")
        return {"collectionCreate":{"collection":{"id":gid},"userErrors":[]}}
    def attribute_schema(self,config):
        return {"definition":{"inputFields":[{"name":k} for k in ("type","fieldDefinitions")]},"assignment":{"inputFields":[{"name":"compareDigest"}]}}
    def key(self,kind,spec):
        return (kind,spec["type"] if kind=="definition" else spec["handle"] if kind=="term" else spec["key"])
    def attribute_resource(self,config,kind,spec):
        self.calls.append("attribute_read")
        result=copy.deepcopy(self.resources.get(self.key(kind,spec)))
        if result and kind=="term":
            result["definition"]=copy.deepcopy(self.resources.get(("definition",spec["type"])))
            result["capabilities"]={"publishable":None}
        return result
    def create_attribute_resource(self,config,kind,spec):
        pending=ShopifyAttributeIdentity.query.filter_by(state="creating").one(); assert pending.operation_id
        self.calls.append("attribute_"+kind+"_create")
        if self.resource_failure==kind+"_before": raise ShopifyError("Response lost before a known outcome")
        gid=f"gid://shopify/{info.PAYLOADS[kind][2]}/{7000+len(self.resources)}"
        value=copy.deepcopy(spec); value["id"]=gid
        if kind=="definition": value["fieldDefinitions"]=[{"key":f["key"],"type":{"name":f["type"]}} for f in spec["fieldDefinitions"]]
        if kind=="field": value["type"]={"name":spec["type"]}
        self.resources[self.key(kind,spec)]=value
        if self.resource_failure==kind: raise ShopifyError("Resource response lost")
        name,receipt,_=info.PAYLOADS[kind]
        return {name:{receipt:{"id":gid},"userErrors":[]}}
    def attribute_values(self,config,pid,namespace):
        return list(copy.deepcopy(self.attr_values).values())
    def set_attributes(self,config,values):
        self.calls.append("attribute_assignment_write")
        assert drafts.evidence(ShopifyProductIdentity.query.one())["stage"]=="attribute_assignment"
        for value in values:
            current=self.attr_values.get(value["key"])
            assert value["compareDigest"]==(current["compareDigest"] if current else None)
            self.attr_values[value["key"]]={"id":"gid://shopify/Metafield/9000","key":value["key"],"type":value["type"],"value":value["value"],"compareDigest":"digest-"+drafts.digest(value["value"])}
        return {"metafieldsSet":{"metafields":list(self.attr_values.values()),"userErrors":[]}}
    def navigation_eligibility(self,config,ids,pub):
        return {"publication":{"id":pub,"name":"Online Store","app":{"title":"Online Store"}},
            "nodes":[{"id":i,"status":self.remote["status"],"metafield":self.remote["metafield"],"publishedOnPublication":self.remote["status"]=="ACTIVE"} for i in ids]}


def create(world,adapter=None):
    adapter=adapter or LifecycleAdapter()
    _,token=drafts.preview(world[0],adapter)
    assert token
    row=drafts.confirm(token,True,adapter)
    assert row.state=="verified",drafts.evidence(row).get("error")
    return row,adapter


def test_inventory_correction_is_reviewed_and_idempotent(variable_world):
    adapter=VariableAdapter()
    _,token=drafts.preview(variable_world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=="verified"
    variants=adapter.remote["variants"]["nodes"]
    identities={v["id"]:v["inventoryItem"]["id"] for v in variants}
    for v in variants[1:]: v["inventoryItem"]["tracked"]=True; v["inventoryPolicy"]="DENY"
    plan,token=drafts.preview(variable_world[0],adapter)
    assert plan["classification"]=="update" and token
    assert sum(f["changed"] for f in plan["fields"])==6
    before=adapter.calls.count("variants_create")
    assert drafts.confirm(token,True,adapter).state=="verified"
    assert all(v["inventoryItem"]["tracked"] is False and v["inventoryPolicy"]=="CONTINUE" for v in variants)
    assert identities=={v["id"]:v["inventoryItem"]["id"] for v in variants}
    assert before==adapter.calls.count("variants_create")
    assert drafts.preview(variable_world[0],adapter)[0]["classification"]=="no_change"
    assert all("inventoryQuantities" not in v for batch in adapter.inputs for v in batch if isinstance(v,dict))


def test_explicit_tracking_is_preserved_without_quantity_writes(world):
    change(world,manage_stock=True,backorders="no",stock_quantity=7)
    row,adapter=create(world)
    actual=adapter.remote["simpleVariants"]["nodes"][0]
    assert actual["inventoryItem"]["tracked"] is True and actual["inventoryPolicy"]=="DENY"
    assert "inventoryQuantities" not in drafts.evidence(row)["plan"]["managed"]["variant"]


def informational(registry):
    data=json.loads(registry.read_text())
    data["attributes"]=[{"key":"occasion","name":"Occasion","slug":"occasion","state":"active","navigation":True,"visible_default":False,
        "browse_role":"filter","browse_group":"Occasions","terms":[
        {"key":"seasonal","name":"Seasonal","slug":"seasonal","state":"active"},
        {"key":"christmas","name":"Christmas","slug":"christmas","state":"active","browse_role":"both","browse_parent":"seasonal"},
        {"key":"birthday","name":"Birthday","slug":"birthday","state":"active"}]}]
    registry.write_text(json.dumps(data))


def test_on_demand_categories_ancestors_and_storefront_reuse(world,registry):
    change(world,categories=["Cards > Birthday Cards"],storefront_collections=["Personalised"])
    adapter=LifecycleAdapter(); plan,token=drafts.preview(world[0],adapter)
    assert len(plan["collections"])==3 and all(c["action"]=="Create & Join" for c in plan["collections"])
    assert "collection_create" not in adapter.calls
    row=drafts.confirm(token,True,adapter)
    assert row.state=="verified",drafts.evidence(row).get("error")
    assert adapter.calls.count("collection_create")==3
    assert all(row.shopify_product_id in adapter.collections[c["remote_id"]]["members"] for c in drafts.evidence(row)["plan"]["collections"])
    _,token=drafts.preview(world[0],adapter); assert token is None


def test_informational_first_use_multiple_terms_and_explicit_clear(world,registry):
    informational(registry)
    change(world,attributes={"Occasion":["Christmas","Birthday"]},variation_attributes=[])
    row,adapter=create(world)
    assert adapter.calls.count("attribute_term_create")==2 and ShopifyAttributeIdentity.query.count()==4
    assert adapter.calls.count("collection_create")==1  # only explicit Christmas destination, not every term/folder
    assert len(json.loads(next(iter(adapter.attr_values.values()))["value"]))==2
    _,token=drafts.preview(world[0],adapter); assert token is None
    (world[1]/"product_info.json").write_text(json.dumps({"attributes":{"Occasion":[]},"variation_attributes":[]}))
    plan,token=drafts.preview(world[0],adapter)
    assert plan["classification"]=="update" and plan["attributes"][0]["action"]=="Clear owned references"
    assert drafts.confirm(token,True,adapter).state=="verified"
    assert json.loads(next(iter(adapter.attr_values.values()))["value"])==[]
    assert adapter.calls.count("attribute_term_create")==2  # entries and remote-only memberships are retained


@pytest.mark.parametrize("failure",["definition","term","field"])
def test_attribute_uncertainty_blocks_duplicate_registration(world,registry,failure):
    informational(registry); change(world,attributes={"Occasion":["Birthday"]},variation_attributes=[])
    adapter=LifecycleAdapter(); adapter.resource_failure=failure
    _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=="uncertain" and row.shopify_product_id
    kept=ShopifyAttributeIdentity.query.filter_by(state="uncertain").one()
    count=adapter.calls.count("attribute_"+failure+"_create")
    info.reconcile(kept,adapter,drafts.configuration())
    assert kept.state=="verified" and count==adapter.calls.count("attribute_"+failure+"_create")
    drafts.reconcile(row,adapter=adapter)
    assert row.state=="attribute_review_required" and row.verified_at is None
    adapter.resource_failure=None
    plan,token=drafts.preview(world[0],adapter)
    assert plan["classification"]=="update" and token
    assert drafts.confirm(token,True,adapter).state=="verified"
    assert adapter.calls.count("product_create")==1


def test_missing_attribute_scopes_and_remote_candidates_block_writes(world,registry):
    informational(registry); change(world,attributes={"Occasion":["Birthday"]},variation_attributes=[])
    adapter=LifecycleAdapter(); adapter.test_connection=lambda c:{"shop_id":"gid://shopify/Shop/42","scopes":["read_products","write_products"]}
    plan,token=drafts.preview(world[0],adapter)
    assert not token and any("read_metaobjects" in b for b in plan["blockers"])
    assert "product_create" not in adapter.calls


def test_reviewed_delete_archives_history_preserves_local_and_recreates(world):
    row,adapter=create(world)
    before={str(p):p.read_bytes() for p in world[1].rglob('*') if p.is_file()}
    old_id=row.shopify_product_id; old_media=drafts.evidence(row)["media"]
    plan,token=lifecycle.review(world[0],adapter)
    with pytest.raises(ShopifyError,match="SKU"): lifecycle.confirm(token,True,"WRONG",expected_product_id=world[0].id,adapter=adapter)
    lifecycle.confirm(token,True,row.sku,expected_product_id=world[0].id,adapter=adapter)
    assert row.state=="remote_deleted" and row.shopify_product_id is None
    archive=db.session.get(CatalogueOperation,row.operation_id)
    assert json.loads(archive.scope)["previous_evidence"]["media"]==old_media
    assert before=={str(p):p.read_bytes() for p in world[1].rglob('*') if p.is_file()}
    with __import__('flask',fromlist=['current_app']).current_app.test_request_context(): assert states_for([world[0]])[world[0].id]["label"]=="Needs Creating"
    assert _client(__import__('flask',fromlist=['current_app']).current_app._get_current_object()).get('/shopify/drafts/identity/'+str(row.id)).status_code==200
    adapter.remote=None; adapter.deleted=False
    original_create=adapter.create_draft
    def new_create(config,payload,media):
        result=original_create(config,payload,media)
        adapter.remote["id"]="gid://shopify/Product/101"
        result["productCreate"]["product"]["id"]=adapter.remote["id"]
        for document in (adapter.remote,result["productCreate"]["product"]):
            document["variants"]["nodes"][0]["id"]="gid://shopify/ProductVariant/600"
            document["simpleVariants"]["nodes"][0]["id"]="gid://shopify/ProductVariant/600"
            document["simpleVariants"]["nodes"][0]["inventoryItem"]["id"]="gid://shopify/InventoryItem/1250"
            for index,item in enumerate(document["media"]["nodes"]): item["id"]=f"gid://shopify/MediaImage/{1300+index}"
        return result
    adapter.create_draft=new_create
    _,token=drafts.preview(world[0],adapter); assert token
    drafts.confirm(token,True,adapter)
    assert row.state=="verified" and row.shopify_product_id!=old_id and row.sku==world[0].sku
    assert drafts.evidence(row)["variant_id"]=="gid://shopify/ProductVariant/600"
    assert not set(m["id"] for m in old_media)&set(m["id"] for m in drafts.evidence(row)["media"])


@pytest.mark.parametrize("failure,absent",[("timeout",True),("survive",False)])
def test_delete_uncertainty_reconciles_exact_outcome_without_retry(world,failure,absent):
    row,adapter=create(world); adapter.delete_failure=failure
    _,token=lifecycle.review(world[0],adapter)
    lifecycle.confirm(token,True,row.sku,expected_product_id=world[0].id,adapter=adapter)
    assert row.state=="delete_uncertain"
    lifecycle.reconcile(row,adapter)
    assert row.state==("remote_deleted" if absent else "verified") and adapter.calls.count("delete_write")==1


def test_remote_absence_errors_and_archived_are_not_conflated(world):
    row,adapter=create(world)
    adapter.remote["status"]="ARCHIVED"
    assert lifecycle.check_absence(row,adapter) is False and row.shopify_product_id
    adapter.delete_failure="read"
    with pytest.raises(ShopifyError): lifecycle.check_absence(row,adapter)
    assert row.state=="verified" and row.shopify_product_id
    adapter.delete_failure=None; adapter.deleted=True
    assert lifecycle.check_absence(row,adapter) and row.state=="remote_deleted"
    assert "delete_write" not in adapter.calls


def test_navigation_uses_actual_publication_and_preserves_empty_local(world,registry,monkeypatch):
    from app import shopify_navigation
    informational(registry); change(world,attributes={"Occasion":["Christmas"]},variation_attributes=[],categories=["Cards > Birthday Cards"],primary_category="birthday")
    row,adapter=create(world)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID","gid://shopify/Publication/9")
    assert not any(d["visible"] for d in shopify_navigation.preview()["destinations"])
    shopify_navigation.check(adapter=adapter)  # Draft never visible
    assert not any(d["visible"] for d in shopify_navigation.preview()["destinations"])
    adapter.remote["status"]="ACTIVE"; shopify_navigation.check(adapter=adapter)
    targets=shopify_navigation.preview()["destinations"]
    assert any(d["label"]=="Occasions > Seasonal > Christmas" and d["visible"] for d in targets)
    assert any(d["key"]=="categories:cards" and d["visible"] for d in targets)
    assert registry.exists() and "delete_write" not in adapter.calls


def test_additive_attribute_migration_upgrade_and_uniqueness(tmp_path):
    from alembic import command
    path=tmp_path/'upgrade.db'; url='sqlite:///'+str(path)
    command.upgrade(_alembic_config(url),'0011_shopify_taxonomy')
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO shopify_collection_identity(store_id,source_key,state,evidence_json) VALUES('store','key','verified','{}')")
    assert ensure_database(url).revision=='0012_shopify_attributes'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM shopify_attribute_identity').fetchone()[0]==0
        assert conn.execute('SELECT state FROM shopify_collection_identity').fetchone()[0]=='verified'
        conn.execute("INSERT INTO shopify_attribute_identity(store_id,source_key,state,evidence_json) VALUES('store','term','pending','{}')")
        with pytest.raises(sqlite3.IntegrityError): conn.execute("INSERT INTO shopify_attribute_identity(store_id,source_key,state,evidence_json) VALUES('store','term','pending','{}')")


@pytest.mark.parametrize("interrupted",[False,True])
def test_variable_informational_attributes_do_not_generate_extra_variants(variable_world,registry,interrupted):
    informational(registry)
    p,shared,_,_,_=variable_world
    source=json.loads(shared.read_text()); source["variation_attributes"]=list(source["attributes"])
    source["attributes"]["Occasion"]=["Birthday"]
    shared.write_text(json.dumps(source)); rescan(variable_world)
    class Adapter(VariableAdapter,LifecycleAdapter):
        def __init__(self):
            VariableAdapter.__init__(self)
            self.resources={}; self.attr_values={}; self.resource_failure=None
            self.collections={}; self.collection_failure=None
        def test_connection(self,config):
            result=VariableAdapter.test_connection(self,config); result["scopes"]+=list(info.READ_SCOPES|info.WRITE_SCOPES)
            return result
    adapter=Adapter()
    if interrupted: adapter.resource_failure="term"
    plan,token=drafts.preview(p,adapter)
    assert len(plan["variants"])==4 and len(plan["attributes"])==1
    assert all(o["name"]!="Occasion" for o in plan["options"])
    row=drafts.confirm(token,True,adapter)
    if interrupted:
        assert row.state=="uncertain"
        kept=ShopifyAttributeIdentity.query.filter_by(state="uncertain").one()
        info.reconcile(kept,adapter,drafts.configuration())
        drafts.reconcile(row,adapter=adapter)
        assert row.state=="attribute_review_required"
        adapter.resource_failure=None
        _,token=drafts.preview(p,adapter)
        row=drafts.confirm(token,True,adapter)
    assert row.state=="verified",drafts.evidence(row).get("error")
    assert adapter.calls.count("attribute_term_create")==1 and len(adapter.remote["variants"]["nodes"])==4


def test_automatic_status_recognises_confirmed_remote_deletion_without_writes(world):
    from app import shopify_status
    row,adapter=create(world)
    adapter.deleted=True; adapter.remote=None
    previous=list(adapter.calls)
    result=shopify_status.refresh([world[0]],force=True,adapter=adapter)
    assert not result["errors"] and row.state=="remote_deleted"
    assert adapter.calls[len(previous):].count("existence_read")==2
    assert "delete_write" not in adapter.calls


def test_wrong_identity_and_delete_permission_fail_closed(world):
    row,adapter=create(world)
    _,token=lifecycle.review(world[0],adapter)
    adapter.remote["metafield"]["value"]="unrelated"
    with pytest.raises(ShopifyError): lifecycle.confirm(token,True,row.sku,expected_product_id=world[0].id,adapter=adapter)
    assert "delete_write" not in adapter.calls and row.state=="verified"


def test_browse_parent_cycle_and_primary_category_are_validated(world,registry):
    from app.taxonomy_workspace import validate, RegistryEditError
    informational(registry); data=json.loads(registry.read_text())
    data["attributes"][0]["terms"][0]["browse_parent"]="christmas"
    with pytest.raises(RegistryEditError): validate(data)
    change(world,primary_category="not-assigned")
    plan,token=drafts.preview(world[0],LifecycleAdapter())
    assert not token and any("Primary category" in b for b in plan["blockers"])


def test_navigation_and_delete_gets_are_local_only(world,registry,monkeypatch):
    from flask import current_app
    informational(registry)
    def forbidden(*a,**k): raise AssertionError("GET attempted Shopify access")
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter.test_connection',forbidden)
    client=_client(current_app._get_current_object())
    assert client.get('/taxonomy?tab=navigation').status_code==200
    response=client.get('/products/'+str(world[0].id)+'/shopify/delete')
    assert response.status_code==200 and b'Permanent remote deletion' in response.data
    assert b'Online Store' in client.get('/taxonomy?tab=navigation').data


def test_exact_resource_noncreation_needs_new_review_before_retry(world,registry):
    informational(registry); change(world,attributes={"Occasion":["Birthday"]},variation_attributes=[])
    adapter=LifecycleAdapter(); adapter.resource_failure="definition_before"
    _,token=drafts.preview(world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=="uncertain"
    kept=ShopifyAttributeIdentity.query.filter_by(state="uncertain").one()
    info.reconcile(kept,adapter,drafts.configuration()); assert kept.state=="absent_verified"
    assert adapter.calls.count("attribute_definition_create")==1
    drafts.reconcile(row,adapter=adapter); assert row.state=="attribute_review_required"
    adapter.resource_failure=None
    _,new_token=drafts.preview(world[0],adapter)
    assert new_token and drafts.confirm(new_token,True,adapter).state=="verified"
    assert adapter.calls.count("attribute_definition_create")==2 and adapter.calls.count("product_create")==1
