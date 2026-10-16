"""Fictional scanner fixtures and mocked Shopify only; never live writes."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image
from flask import current_app
from app import db, shopify_drafts as drafts, shopify_variables as variable
from app.models import Product, Settings, ShopifyProductIdentity, CatalogueOperation
from app.shopify_connection import ShopifyError
from app.shopify_products import states_for
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from test_shopify_drafts import world
from test_shopify_simple_updates import UpdateAdapter
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_taxonomy_products import registry


class VariableAdapter(UpdateAdapter):
    def __init__(self):
        super().__init__()
        self.packed = {}
        self.failure_stage = None
        self.native_permission = True
        self.processing = False
        self.next_id = 201

    def test_connection(self,config):
        result=super().test_connection(config)
        if self.native_permission: result["scopes"].append("write_inventory")
        return result

    def find_skus(self,config,skus):
        self.calls.append("sku_batch_read")
        return {"nodes":[],"pageInfo":{"hasNextPage":False}}

    def product_revisions(self,config,ids):
        self.calls.append("revision_read")
        return {gid:{"id":gid,"status":self.remote["status"],"updatedAt":self.remote["updatedAt"]} for gid in ids}

    def packed_capability(self,config):
        return {"measurement":{"fields":[{"name":"packedDimensions"}]},
            "measurementInput":{"inputFields":[{"name":"packedDimensions"}]},
            "dimensionsInput":{"inputFields":[{"name":k} for k in ("length","width","height","unit")]},
            "units":{"enumValues":[{"name":k} for k in variable.native.FACTORS]}}

    def create_variable(self,config,product,media):
        row=ShopifyProductIdentity.query.one()
        assert row.state=="creating" and drafts.evidence(row)["create_attempted"]
        self.calls.append("product_create")
        assert product["status"]=="DRAFT" and "publications" not in product
        options=copy.deepcopy(product.pop("productOptions"))
        self.remote=dict(product,id="gid://shopify/Product/100",metafield={"value":product["metafields"][0]["value"]},
            options=[dict(o,id=f"gid://shopify/ProductOption/{40+i}",optionValues=[dict(value,id=f"gid://shopify/ProductOptionValue/{100+i*32+j}") for j,value in enumerate(o["values"])]) for i,o in enumerate(options)],
            variants={"nodes":[],"pageInfo":{"hasNextPage":False}},
            media={"nodes":[{"id":f"gid://shopify/MediaImage/{300+i}","alt":m["alt"],"status":"PROCESSING" if self.processing else "READY",
                "mediaContentType":"IMAGE","image":None if self.processing else {"url":f"https://cdn.shopify.com/original-{i}.png"}} for i,m in enumerate(media)],"pageInfo":{"hasNextPage":False}})
        self.remote.setdefault("vendor",""); self.remote.setdefault("tags",[]); self.remote.setdefault("productType","")
        self.remote.setdefault("category",None); self.remote.setdefault("seo",{"title":None,"description":None})
        self.remote["updatedAt"]="2026-10-13T10:00:00Z"
        for k in ("length","width","height"):
            self.remote[k]=next((dict(m,id="gid://shopify/Metafield/600") for m in product["metafields"] if m["key"]=="product_"+k),None)
        first=self._variant(200,[{"name":o["name"],"value":o["values"][0]["name"]} for o in options])
        self.remote["variants"]["nodes"].append(first)
        if self.failure_stage=="product_create": raise ShopifyError("Product Create response lost; reconcile.")
        return {"productCreate":{"product":copy.deepcopy(self.remote),"userErrors":[]}}

    def _variant(self,number,options):
        iid=f"gid://shopify/InventoryItem/{number+1000}"
        self.packed[iid]={"length":0,"width":0,"height":0,"unit":"CENTIMETERS"}
        return {"id":f"gid://shopify/ProductVariant/{number}","sku":"","price":"0.00","compareAtPrice":None,"inventoryPolicy":"DENY",
            "selectedOptions":options,"media":{"nodes":[],"pageInfo":{"hasNextPage":False}},
            "length":None,"width":None,"height":None,
            "inventoryItem":{"id":iid,"sku":"","countryCodeOfOrigin":None,"requiresShipping":True,"harmonizedSystemCode":None,
                "tracked":False,"measurement":{"weight":{"value":0,"unit":"GRAMS"}}}}

    def _apply(self,v,payload):
        for k,value in payload.items():
            if k=="inventoryItem":
                v[k].update(copy.deepcopy(value)); v["sku"]=v[k]["sku"]
            elif k=="metafields":
                for m in value: v[m["key"].removeprefix("product_")]=dict(m,id="gid://shopify/Metafield/700")
            elif k=="mediaId": v["media"]={"nodes":[{"id":value}],"pageInfo":{"hasNextPage":False}}
            elif k not in {"id","optionValues"}: v[k]=copy.deepcopy(value)

    def update_variants(self,config,pid,variants):
        self.calls.append("variants_update")
        self.inputs.append(copy.deepcopy(variants))
        assert drafts.evidence(ShopifyProductIdentity.query.one())["stage"] in {"variant_update","inventory_update"}
        for payload in variants:
            self._apply(next(v for v in self.remote["variants"]["nodes"] if v["id"]==payload["id"]),payload)
        if self.failure_stage=="variants_update": raise ShopifyError("Variant update response lost")
        return {"productVariantsBulkUpdate":{"productVariants":copy.deepcopy(self.remote["variants"]["nodes"]),"userErrors":[]}}

    def create_variants(self,config,pid,variants):
        self.calls.append("variants_create")
        assert drafts.evidence(ShopifyProductIdentity.query.one())["variant_creates_attempted"]
        for payload in variants:
            opts=[{"name":o["optionName"],"value":o["name"]} for o in payload["optionValues"]]
            for selected in opts:
                option=next(o for o in self.remote["options"] if o["name"]==selected["name"])
                if not any(v["name"]==selected["value"] for v in option["optionValues"]):
                    option["optionValues"].append({"id":f"gid://shopify/ProductOptionValue/{self.next_id+5000}","name":selected["value"]})
            assert not any(v["selectedOptions"]==opts for v in self.remote["variants"]["nodes"])
            v=self._variant(self.next_id,opts); self.next_id+=1
            self._apply(v,payload); self.remote["variants"]["nodes"].append(v)
        if self.failure_stage=="variants_create": raise ShopifyError("Variant create response lost")
        return {"productVariantsBulkCreate":{"productVariants":copy.deepcopy(self.remote["variants"]["nodes"]),"userErrors":[]}}

    def read_variable(self,config,pid,namespace,*,native=False):
        assert config.api_version=="2026-10"
        self.calls.append("native_read" if native else "variable_read")
        remote=copy.deepcopy(self.remote)
        if native:
            for v in remote["variants"]["nodes"]:
                v["inventoryItem"]["measurement"]["packedDimensions"]=copy.deepcopy(self.packed[v["inventoryItem"]["id"]])
        return remote

    def write_packed(self,config,iid,desired):
        assert any(p.get("state")=="pending" and p["inventory_id"]==iid for p in drafts.evidence(ShopifyProductIdentity.query.one())["variant_native"].values())
        self.calls.append("native_write")
        self.packed[iid]=copy.deepcopy(desired)
        if self.failure_stage=="native": raise ShopifyError("Native response lost")
        return {"inventoryItemUpdate":{"inventoryItem":{"id":iid},"userErrors":[]}}

    def read_packed_inventory(self,config,iid):
        self.calls.append("native_inventory_read")
        v=next(v for v in self.remote["variants"]["nodes"] if v["inventoryItem"]["id"]==iid)
        return {"id":iid,"sku":v["sku"],"measurement":{"packedDimensions":copy.deepcopy(self.packed[iid])}}


@pytest.fixture(params=["Variable Collection","Single Variable"])
def variable_world(world,request):
    root=Path(Settings.query.first().product_folder)
    collection=root/request.param
    shutil.copytree(Path(__file__).parent/"fixtures/catalogue"/request.param,collection)
    shared=collection/"product_info.json"
    data=json.loads(shared.read_text()); data.update(live=False,categories=[],tags=["variable"],country_of_origin="GB",requires_shipping=True)
    shared.write_text(json.dumps(data))
    if request.param == "Single Variable":
        (collection/"parent").mkdir()
        for theme in data["attributes"]["Theme"]:
            for size in data["attributes"]["Size"]:
                (collection/theme/size).mkdir(parents=True)
    for folder in [collection,*collection.rglob("*")]:
        if folder.is_dir() and (folder.name.lower()=="parent" or not any(p.is_dir() for p in folder.iterdir())):
            Image.new("RGB",(5,5),"red").save(folder/"original.png")
            Image.new("RGB",(5,5),"blue").save(folder/"second.jpg")
    output=root.parent/"variable-output"; output.mkdir()
    rows=scan_collection(str(collection),"https://invalid.example/",str(output),log=lambda *a,**k:None)
    summary=ingest_rows_to_db(rows,log=lambda *a,**k:None)
    assert summary["products_failed"]==0
    parent_sku=next(r["SKU"] for r in rows if r["Type"]=="variable")
    product=Product.query.filter_by(sku=parent_sku).one()
    assert product and len(product.variations)==4
    return product,shared,collection,output,rows


def create(w):
    adapter=VariableAdapter()
    plan,token=drafts.preview(w[0],adapter)
    assert token and not plan["blockers"], plan["blockers"]
    row=drafts.confirm(token,True,adapter)
    assert row.state=="verified", drafts.evidence(row).get("error")
    return row,adapter


def test_packaging_weight_and_native_size_are_managed_per_exact_variant(variable_world,monkeypatch):
    from app.shopify_products import source_fingerprint
    from app.utils.json_utils import json_key
    product,shared,*_=variable_world
    baseline=source_fingerprint(product,[])
    data=json.loads(shared.read_text())
    chosen=product.variations[0]
    selection={a.name:a.value for a in chosen.attributes}
    key=json_key(selection)
    modifier=data.setdefault('variation_modifiers',{}).setdefault(key,{})
    modifier['packaging']={'weight_g':'123.45','packed_dimensions_cm':{'length':'42','width':'29.7','height':'0.3'},'measured':True}
    shared.write_text(json.dumps(data))
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','false')
    assert source_fingerprint(product,[])!=baseline
    values=variable.projection(product)
    row=next(v for v in values if v['local_id']==chosen.id)
    assert str(row['managed']['inventoryItem']['measurement']['weight']['value'])=='123.45'
    assert row['managed']['inventoryItem']['measurement']['weight']['unit']=='GRAMS'
    monkeypatch.setenv('SHOPIFY_NATIVE_DIMENSIONS_ENABLED','true')
    monkeypatch.setenv('SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS','false')
    values=variable.projection(product)
    row=next(v for v in values if v['local_id']==chosen.id)
    assert row['native']['desired']['length']==42.0
    assert all(v['native']['desired'] is None for v in values if v['local_id']!=chosen.id)


def test_scanner_boundaries_prices_create_and_matching(variable_world):
    p,shared,collection,output,rows=variable_world
    files={str(f):f.read_bytes() for f in collection.rglob('*') if f.is_file()}
    adapter=VariableAdapter(); plan,token=drafts.preview(p,adapter)
    assert plan["kind"]=="variable" and len(plan["variants"])==4
    assert {v["sku"] for v in plan["variants"]}=={v.sku for v in p.variations}
    expected={v.sku:str(v.regular_price) for v in p.variations}
    assert {v["sku"]:v["managed"]["price"] for v in plan["variants"]}==expected
    assert "product_create" not in adapter.calls
    with pytest.raises(ShopifyError,match="acknowledgement"): drafts.confirm(token,False,adapter)
    row=drafts.confirm(token,True,adapter)
    assert row.state=="verified", drafts.evidence(row).get("error")
    ids=drafts.evidence(row)["variant_maps"]
    assert len(ids)==4 and len({m["inventory_id"] for m in ids.values()})==4
    assert files=={str(f):f.read_bytes() for f in collection.rglob('*') if f.is_file()}
    db.session.expire_all()
    plan,token=drafts.preview(p,adapter)
    assert plan["classification"]=="no_change" and token is None
    assert adapter.calls.count("product_create")==adapter.calls.count("variants_create")==1
    with current_app.test_request_context(): assert states_for([p])[p.id]["label"]=="Up to Date"


def test_uncertain_variant_create_is_read_only_recoverable(variable_world):
    adapter=VariableAdapter(); adapter.failure_stage="variants_create"
    _,token=drafts.preview(variable_world[0],adapter)
    row=drafts.confirm(token,True,adapter)
    assert row.state=="uncertain"
    with pytest.raises(ShopifyError,match="Recovery required"): drafts.preview(variable_world[0],adapter)
    writes=[c for c in adapter.calls if "create" in c or "update" in c]
    drafts.reconcile(row,adapter=adapter)
    assert row.state=="verified" and len(drafts.evidence(row)["variant_maps"])==4
    assert writes==[c for c in adapter.calls if "create" in c or "update" in c]


def test_native_dimensions_are_per_actual_inventory_item(variable_world,monkeypatch):
    monkeypatch.setenv("SHOPIFY_NATIVE_DIMENSIONS_ENABLED","true")
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS","true")
    row,adapter=create(variable_world)
    proof=drafts.evidence(row)
    for v in proof["plan"]["variants"]:
        mapping=proof["variant_maps"][v["identity"]]
        assert adapter.packed[mapping["inventory_id"]]==v["native"]["desired"]
    assert len(proof["variant_native"])==4 and adapter.calls.count("native_write")==4
    assert all(v["inventoryItem"]["tracked"] is False for v in adapter.remote["variants"]["nodes"])


def test_processing_finishes_without_duplicate_variant_or_upload(variable_world):
    adapter=VariableAdapter(); adapter.processing=True
    _,token=drafts.preview(variable_world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=="processing"
    with pytest.raises(ShopifyError,match="still processing"): drafts.preview(variable_world[0],adapter)
    for m in adapter.remote["media"]["nodes"]: m.update(status="READY",image={"url":"https://cdn.shopify.com/original.png"})
    plan,token=drafts.preview(variable_world[0],adapter)
    assert row.state=="verified" and plan["classification"]=="no_change"
    assert adapter.calls.count("product_create")==adapter.calls.count("variants_create")==1


def rescan(w, **changes):
    p,shared,collection,output,_=w
    shared.write_text(json.dumps({**json.loads(shared.read_text()),**changes}))
    rows=scan_collection(str(collection),"https://invalid.example/",str(output),force_update=True,update_csv=True,log=lambda *a,**k:None)
    summary=ingest_rows_to_db(rows,log=lambda *a,**k:None)
    assert summary["products_failed"]==0
    db.session.expire_all()
    return p


def test_reviewed_variant_price_update_retains_all_ids(variable_world):
    row,adapter=create(variable_world)
    ids=copy.deepcopy(drafts.evidence(row)["variant_maps"])
    document=json.loads(variable_world[1].read_text())
    modifiers={key:dict(value,price="20.00") for key,value in document.get("variation_modifiers",{}).items()}
    rescan(variable_world,price="20.00",variation_modifiers=modifiers)
    plan,token=drafts.preview(variable_world[0],adapter)
    assert plan["classification"]=="update" and token
    adapter.calls.clear(); adapter.inputs.clear()
    drafts.confirm(token,True,adapter)
    assert row.state=="verified",drafts.evidence(row).get("error")
    assert ids==drafts.evidence(row)["variant_maps"]
    assert "variants_create" not in adapter.calls and "product_create" not in adapter.calls and "binary_upload" not in adapter.calls
    assert all(set(v)=={"id","price"} for batch in adapter.inputs for v in batch)
    assert drafts.preview(variable_world[0],adapter)[0]["classification"]=="no_change"


def test_additions_preserve_existing_skus_and_remote_only_variants(variable_world):
    row,adapter=create(variable_world)
    before=copy.deepcopy(drafts.evidence(row)["variant_maps"])
    extras=adapter._variant(900,[{"name":o["name"],"value":"Remote only"} for o in variable.local_plan(variable_world[0])["options"]])
    extras["sku"]=extras["inventoryItem"]["sku"]="REMOTE-ONLY"
    adapter.remote["variants"]["nodes"].append(extras)
    doc=json.loads(variable_world[1].read_text())
    attrs=doc["attributes"]
    first=list(attrs)[-1]; attrs[first].append("Extra")
    if doc["collection_type"]=="Single Variable":
        for theme in attrs["Theme"]:
            directory=variable_world[2]/theme/"Extra"; directory.mkdir()
            Image.new("RGB",(5,5),"blue").save(directory/"extra.png")
        # A gallery addition is intentionally unsupported; use the existing
        # known image for this safe additive-variant acceptance fixture.
        for directory in (variable_world[2]/t/"Extra" for t in attrs["Theme"]):
            (directory/"extra.png").unlink()
    rescan(variable_world,attributes=attrs)
    plan,token=drafts.preview(variable_world[0],adapter)
    if doc["collection_type"]=="Single Variable":
        # Existing scanner may select a newly discoverable source; never sneak
        # an unreviewed gallery change through this proof of concept.
        assert plan["blockers"] or token
    assert token, plan["blockers"]
    assert sum(v["action"]=="Create" for v in plan["variants"])==2
    adapter.calls.clear(); drafts.confirm(token,True,adapter)
    assert row.state=="verified",drafts.evidence(row).get("error")
    assert all(drafts.evidence(row)["variant_maps"][k]==v for k,v in before.items())
    assert any(v["id"]==extras["id"] for v in adapter.remote["variants"]["nodes"])
    assert adapter.calls.count("variants_create")==1 and "product_create" not in adapter.calls


def test_destructive_options_and_identity_conflicts_block_writes(variable_world):
    row,adapter=create(variable_world)
    adapter.remote["options"][0]["name"]="Unexpected rename"
    with pytest.raises(ShopifyError,match="destructive restructuring"):
        drafts.preview(variable_world[0],adapter)
    adapter.remote["options"][0]["name"]=drafts.evidence(row)["option_maps"][0]["name"]
    adapter.remote["variants"]["nodes"][0]["inventoryItem"]["id"]="gid://shopify/InventoryItem/999"
    with pytest.raises(ShopifyError,match="identity/SKU/options changed"):
        drafts.preview(variable_world[0],adapter)


def test_signed_review_and_local_changes_are_protected(variable_world):
    p=variable_world[0]; adapter=VariableAdapter()
    _,token=drafts.preview(p,adapter)
    with pytest.raises(ShopifyError,match="expired or is invalid"): drafts.confirm(token+"bad",True,adapter)
    with pytest.raises(ShopifyError,match="does not match"): drafts.confirm(token,True,adapter,expected_product_id=999)
    rescan(variable_world,price="30.00")
    with pytest.raises(ShopifyError,match="changed after Review"): drafts.confirm(token,True,adapter)
    assert "product_create" not in adapter.calls


def test_missing_native_permission_does_not_poison_verified_metadata(variable_world,monkeypatch):
    monkeypatch.setenv("SHOPIFY_NATIVE_DIMENSIONS_ENABLED","true")
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS","true")
    adapter=VariableAdapter(); adapter.native_permission=False
    _,token=drafts.preview(variable_world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state==row.verification_state=="verified"
    assert all(p["state"]=="blocked" for p in drafts.evidence(row)["variant_native"].values())
    assert "native_write" not in adapter.calls
    with current_app.test_request_context(): assert states_for([variable_world[0]])[variable_world[0].id]["label"]=="Needs Attention"


def test_uncertain_native_stops_remaining_writes_and_reconciles_without_retry(variable_world,monkeypatch):
    monkeypatch.setenv("SHOPIFY_NATIVE_DIMENSIONS_ENABLED","true")
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS","true")
    adapter=VariableAdapter(); adapter.failure_stage="native"
    _,token=drafts.preview(variable_world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=="uncertain" and adapter.calls.count("native_write")==1
    with pytest.raises(ShopifyError,match="Recovery required"): drafts.preview(variable_world[0],adapter)
    drafts.reconcile(row,adapter=adapter)
    assert row.state=="verified" and adapter.calls.count("native_write")==1
    assert drafts.preview(variable_world[0],adapter)[0]["classification"]=="update"


def test_live_intent_allows_unpublished_draft_creation_and_offline_pages(variable_world,monkeypatch):
    rescan(variable_world,live=True)
    adapter=VariableAdapter()
    plan,token=drafts.preview(variable_world[0],adapter)
    assert token and plan['intent']=='Active' and plan['publication']['pending']
    row=drafts.confirm(token,True,adapter)
    assert row.state=='verified' and adapter.remote['status']=='DRAFT'
    assert not any('publication_write' in call for call in adapter.calls)
    from app.shopify_connection import ShopifyAdapter
    monkeypatch.setattr(ShopifyAdapter,"test_connection",lambda *a:pytest.fail("GET contacted Shopify"))
    client=_client(current_app._get_current_object())
    for path in ("/products",f"/products/{variable_world[0].id}",f"/products/{variable_world[0].id}/shopify/review"):
        assert client.get(path).status_code==200


def test_sale_prices_use_effective_variant_regular_compare_at(variable_world):
    rescan(variable_world,sale_price="5.00")
    row,adapter=create(variable_world)
    plan,_=drafts.preview(variable_world[0],adapter)
    for wanted in plan["variants"]:
        actual=next(v for v in variable_world[0].variations if v.sku==wanted["sku"])
        assert wanted["managed"]["price"]=="5.00" and wanted["managed"]["compareAtPrice"]==str(actual.regular_price)


def test_variants_get_authoritative_ancestor_and_storefront_memberships(variable_world,registry):
    from app.models import ShopifyCollectionIdentity
    rescan(variable_world,categories=["Cards > Birthday Cards"],storefront_collections=["Personalised"],tags=["Birthday"])
    members={}; joins=[]
    for i,key in enumerate(("categories:cards","categories:birthday","storefront_collections:personalised")):
        gid=f"gid://shopify/Collection/{900+i}"; members[gid]=set()
        db.session.add(ShopifyCollectionIdentity(store_id="gid://shopify/Shop/42",source_key=key,remote_id=gid,state="verified",evidence_json='{"version":1}'))
    db.session.commit()
    adapter=VariableAdapter()
    def state(config,gid,pid="gid://shopify/Product/0"):
        return {"id":gid,"title":"Reviewed group","ruleSet":None,"sources":[],"hasProduct":pid in members[gid],"metafield":None}
    def join(config,gid,pid):
        joins.append(gid); members[gid].add(pid)
        return {"collectionAddProducts":{"collection":{"id":gid},"userErrors":[]}}
    adapter.collection_state=state; adapter.join_collection=join
    plan,token=drafts.preview(variable_world[0],adapter)
    assert token and len(plan["collections"])==3
    row=drafts.confirm(token,True,adapter)
    assert row.state=="verified",drafts.evidence(row).get("error")
    assert len(joins)==3 and all(row.shopify_product_id in values for values in members.values())
    assert adapter.remote["tags"]==["Birthday"]
    assert drafts.preview(variable_world[0],adapter)[0]["classification"]=="no_change"


def test_known_async_media_reordering_is_processing_not_recovery(variable_world):
    row,adapter=create(variable_world)
    p=variable_world[0]
    adapter.remote["media"]["nodes"].reverse()  # Review restores authoritative gallery order.
    adapter.deferred_order=True
    plan,token=drafts.preview(p,adapter)
    assert plan["media_actions"]["reorder"]
    drafts.confirm(token,True,adapter)
    assert row.state=="processing",drafts.evidence(row).get("error")
    ordered=drafts.evidence(row)["created_media"]
    by_id={m["id"]:m for m in adapter.remote["media"]["nodes"]}
    adapter.remote["media"]["nodes"]=[by_id[m["id"]] for m in ordered]
    assert drafts.preview(p,adapter)[0]["classification"]=="no_change"
    assert row.state=="verified"


def test_remote_duplicate_and_missing_scope_do_not_create(variable_world):
    adapter=VariableAdapter()
    adapter.find_skus=lambda *a:{"nodes":[{"id":"gid://shopify/ProductVariant/999"}],"pageInfo":{"hasNextPage":False}}
    with pytest.raises(ShopifyError,match="SKU candidate"): drafts.preview(variable_world[0],adapter)
    assert ShopifyProductIdentity.query.count()==0 and "product_create" not in adapter.calls
    adapter=VariableAdapter(); connection=adapter.test_connection
    adapter.test_connection=lambda cfg:{**connection(cfg),"scopes":["read_products"]}
    plan,token=drafts.preview(variable_world[0],adapter)
    assert not token and any("write_products" in b for b in plan["blockers"])


def test_stale_modifier_projection_requires_targeted_rescan(variable_world):
    shared=variable_world[1]
    shared.write_text(json.dumps({**json.loads(shared.read_text()),"price":"45.00"}))
    with pytest.raises(ShopifyError,match="targeted rescan"): drafts.preview(variable_world[0],VariableAdapter())


def test_variable_transport_preserves_api_and_no_deletion_contract():
    from app.shopify_connection import VARIABLE_READ,VARIABLE_NATIVE_READ,VARIABLE_CREATE,VARIANTS_CREATE,VARIANTS_UPDATE,NATIVE_INVENTORY_READ
    assert "productOptions" not in VARIABLE_READ and "options {" in VARIABLE_READ
    assert "packedDimensions" not in VARIABLE_READ and "packedDimensions" in VARIABLE_NATIVE_READ
    assert "descriptionHtml" not in VARIABLE_NATIVE_READ and "$namespace" not in VARIABLE_NATIVE_READ
    assert "PRESERVE_STANDALONE_VARIANT" in VARIANTS_CREATE
    assert "allowPartialUpdates: false" in VARIANTS_UPDATE
    assert "ProductVariantsBulkInput" in VARIANTS_CREATE and "ProductCreateInput" in VARIABLE_CREATE
    assert "inventoryItem(id:" in NATIVE_INVENTORY_READ
    assert not any("Delete" in q or "REMOVE_STANDALONE" in q for q in (VARIANTS_CREATE,VARIANTS_UPDATE,VARIABLE_CREATE))


def test_variable_transport_uses_stable_api_except_narrow_packed_reads(monkeypatch):
    import requests
    from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration
    from test_shopify_foundation import Session, Response, TOKEN
    response=Response({"data":{"product":{"id":"gid://shopify/Product/100","variants":{"nodes":[],"pageInfo":{"hasNextPage":False}}},
        "inventoryItem":{"id":"gid://shopify/InventoryItem/250"},
        "productCreate":{},"productVariantsBulkCreate":{},"productVariantsBulkUpdate":{}}})
    session=Session(response)
    monkeypatch.setattr(requests,"Session",lambda:session)
    config=ShopifyConfiguration("fictional.myshopify.com",TOKEN,"2026-10")
    adapter=ShopifyAdapter()
    adapter.read_variable(config,"gid://shopify/Product/100","tlc_dashboard")
    adapter.create_variable(config,{"status":"DRAFT","productOptions":[]},[])
    adapter.create_variants(config,"gid://shopify/Product/100",[])
    adapter.update_variants(config,"gid://shopify/Product/100",[])
    response.headers={"X-Shopify-API-Version":"2027-01"}
    adapter.read_variable(config,"gid://shopify/Product/100","tlc_dashboard",native=True)
    adapter.read_packed_inventory(config,"gid://shopify/InventoryItem/250")
    assert all("/2026-10/" in url for url,_ in session.calls[:4])
    assert all("/2027-01/" in url for url,_ in session.calls[4:])
    assert session.calls[4][1]["json"]["variables"]=={"id":"gid://shopify/Product/100","after":None}
    assert all(request["timeout"]==(5,15) and request["allow_redirects"] is False for _,request in session.calls)
    assert config.api_version=="2026-10"


def test_shared_status_cache_and_native_freshness_are_bounded(variable_world,monkeypatch):
    from app import shopify_status
    from app.models import ShopifyProductComparison
    from datetime import timedelta
    monkeypatch.setenv("SHOPIFY_NATIVE_DIMENSIONS_ENABLED","true")
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS","true")
    row,adapter=create(variable_world)
    adapter.calls.clear()
    for _ in range(3):
        result=shopify_status.refresh([variable_world[0]],adapter=adapter)
        assert result["full_comparisons"]==0
    assert not adapter.calls
    summary=ShopifyProductComparison.query.filter_by(store_id=row.store_id,source_identity=row.source_identity).one()
    data=json.loads(summary.summary_json)
    data["native"]["checked_at"]=(drafts.now()-timedelta(minutes=11)).isoformat()
    summary.summary_json=json.dumps(data); db.session.commit()
    result=shopify_status.refresh([variable_world[0]],adapter=adapter)
    assert result["full_comparisons"]==1 and "native_read" in adapter.calls
    assert "native_write" not in adapter.calls and "product_create" not in adapter.calls


def test_country_shipping_and_variant_media_are_independently_verified(variable_world):
    row,adapter=create(variable_world)
    data=drafts.evidence(row)
    assert data["verification"]["fields"]==data["verification"]["media"]=="verified"
    for wanted in data["plan"]["variants"]:
        mapped=data["variant_maps"][wanted["identity"]]
        actual=next(v for v in adapter.remote["variants"]["nodes"] if v["id"]==mapped["id"])
        assert actual["inventoryItem"]["countryCodeOfOrigin"]=="GB" and actual["inventoryItem"]["requiresShipping"] is True
        if wanted["media_source"]:
            receipt=next(m for m in data["media"] if m["source"]==wanted["media_source"])
            assert actual["media"]["nodes"]==[{"id":receipt["id"]}]
    assert all(payload.get("inventoryItem",{}).get("tracked",False) is False for batch in adapter.inputs for payload in batch if isinstance(payload,dict))


def test_uncertain_product_create_does_not_allow_another_create(variable_world):
    adapter=VariableAdapter(); adapter.failure_stage="product_create"
    _,token=drafts.preview(variable_world[0],adapter); row=drafts.confirm(token,True,adapter)
    assert row.state=="uncertain" and row.shopify_product_id is None
    with pytest.raises(ShopifyError,match="Recovery required"): drafts.preview(variable_world[0],adapter)
    assert adapter.calls.count("product_create")==1
    adapter.find_draft=lambda *a:{"nodes":[{"id":"gid://shopify/Product/100"}],"pageInfo":{"hasNextPage":False}}
    with pytest.raises(ShopifyError,match="incomplete/uncertain"): drafts.reconcile(row,adapter=adapter)
    assert row.shopify_product_id=="gid://shopify/Product/100"
    assert drafts.recovery_token(row)  # explicit signed media review, never automatic adoption


def test_variant_inventory_identity_corruption_never_clears_recovery(variable_world):
    row,adapter=create(variable_world)
    row.state="uncertain"; db.session.commit()
    adapter.remote["variants"]["nodes"][0]["inventoryItem"]["id"]="gid://shopify/InventoryItem/999"
    with pytest.raises(ShopifyError,match="identity/SKU/options changed"):
        drafts.reconcile(row,adapter=adapter)
    assert row.state=="uncertain"
