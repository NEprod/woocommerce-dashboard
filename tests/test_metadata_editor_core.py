"""Real temporary source/scan fixtures; no Shopify writes or Discord delivery."""
import copy
import json
import re
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.models import Settings, User, Product, ProductAsset, CatalogueOperation, ShopifyProductIdentity
from app.metadata_save import review_token
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from app.utils.operation_control import finish_catalogue_operation
from config import Config


@pytest.fixture
def core(tmp_path, monkeypatch):
    root, output = tmp_path / "catalogue", tmp_path / "output"
    output.mkdir()
    for name in ("Simple Collection", "Variable Collection", "Single Variable"):
        shutil.copytree(Path(__file__).parent / "fixtures/catalogue" / name, root / name)
    def image(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (3, 4), "navy").save(path)
    image(root / "Simple Collection/Blue Token/blue.png")
    image(root / "Simple Collection/Green Token/green.jpg")
    image(root / "Variable Collection/Badge One/badge.png")
    image(root / "Single Variable/parent/main.png")
    for theme in ("Moon", "Sun"):
        for size in ("Mini", "Maxi"):
            image(root / "Single Variable" / theme / size / "image.png")
    previous = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "site.db")
    app = create_app(recover=False)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.session.add_all([Settings(product_folder=str(root), output_folder=str(output), url_prefix="https://invalid.example/"),
                            User(email="core@example.test", username="core-admin", password="unused", is_admin=True)])
        db.session.commit()
        for name in ("Simple Collection", "Variable Collection", "Single Variable"):
            rows = scan_collection(root/name, "https://invalid.example/", output, log=lambda *a, **k: None)
            ingest_rows_to_db(rows, log=lambda *a, **k: None)
        parents = {p.source_relpath:p.id for p in Product.query.all()}
        user_id = User.query.one().id
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id); session["_fresh"] = True
    starts, notices = [], []
    def completed(*args, **kwargs):
        starts.append(kwargs)
        finish_catalogue_operation(kwargs["operation_id"], status="succeeded")
    monkeypatch.setattr("app.routes.start_scan", completed)
    monkeypatch.setattr("app.routes.notify_editor_saved", lambda *a, **k: notices.append((a,k)))
    try:
        yield app, client, root, output, parents, starts, notices
    finally:
        with app.app_context(): db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = previous


def payload(core, name, *, kind="override", data=None, removals=None):
    app, _, _, _, ids, *_ = core
    with app.app_context():
        product = db.session.get(Product, ids[name])
        return product.sku, {"kind":kind, "revision":review_token(product, kind),
            "data":data or {}, "remove_paths":removals or [], "acknowledge_shared":True}


def save(core, name="Simple Collection/Blue Token", **kwargs):
    sku, body = payload(core, name, **kwargs)
    return core[1].post(f"/edit_products/{sku}/save", json=body)


@pytest.mark.parametrize("name,kind", [
    ("Simple Collection/Blue Token","override"),
    ("Variable Collection/Badge One","override"),
    ("Variable Collection/Badge One","shared"),
    ("Single Variable","shared")])
def test_all_layout_owners_save_without_reallocating_identities(core, name, kind):
    app, _, root, _, ids, starts, _ = core
    with app.app_context():
        p = db.session.get(Product, ids[name])
        baseline = (p.sku, [(v.id,v.sku,v.source_identity) for v in p.variations])
    index = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("sku_index.json")}
    response = save(core, name, kind=kind, data={"description":"Reviewed copy"})
    assert response.status_code == 200, response.json
    assert response.json["json_saved"] and response.json["scan_pending"]
    assert starts[-1]["scan_mode"] == "shared_collection"
    with app.app_context():
        p = db.session.get(Product, ids[name])
        assert (p.sku, [(v.id,v.sku,v.source_identity) for v in p.variations]) == baseline
    assert index == {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("sku_index.json")}


def test_unknown_legacy_internal_empties_and_unedited_objects_survive(core):
    target = core[2]/"Variable Collection/Badge One/product_info.json"
    source = json.loads(target.read_text())
    source.update(future={"opaque":[],"null":None}, production_notes="private", shipping_class="old",
        tags=[], variation_modifiers={"Finish=Gloss":{"price":13.25}}, image_attributes=["Finish"])
    target.write_text(json.dumps(source))
    result = save(core,"Variable Collection/Badge One",data={"description":"New copy"})
    assert result.status_code == 200, result.json
    assert json.loads(target.read_text()) == {**source,"description":"New copy"}
    assert target.stat().st_mode & 0o777 == 0o600


def test_removing_override_restores_inheritance_without_rewriting_shared(core):
    target = core[2]/"Simple Collection/Blue Token/product_info.json"
    source = json.loads(target.read_text()); source["weight"] = 88; target.write_text(json.dumps(source))
    shared = core[2]/"Simple Collection/product_info.json"; previous = shared.read_bytes()
    response = save(core,removals=[["weight"]])
    assert response.status_code == 200 and "weight" not in json.loads(target.read_text())
    assert shared.read_bytes() == previous


@pytest.mark.parametrize("change", ["source","shared","token","owner"])
def test_stale_or_wrong_review_rejected_before_write(core,change):
    name="Simple Collection/Blue Token"
    sku, body=payload(core,name,data={"description":"Draft"})
    target=core[2]/name/"product_info.json"
    if change in {"source","shared"}:
        path=target if change=="source" else core[2]/"Simple Collection/product_info.json"
        doc=json.loads(path.read_text());doc["description"]="Concurrent edit";path.write_text(json.dumps(doc))
    elif change=="token":body["revision"]+="tampered"
    else:
        with core[0].app_context():sku=db.session.get(Product,core[4]["Simple Collection/Green Token"]).sku
    before=target.read_bytes()
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code==409 and target.read_bytes()==before
    assert not core[5] and not core[6] and not list(target.parent.glob("*.bak.*"))


def test_shared_confirmation_and_child_preservation(core):
    name="Simple Collection/Blue Token";target=core[2]/name/"product_info.json";before=target.read_bytes()
    sku,body=payload(core,name,kind="shared",data={"weight":55})
    body["acknowledge_shared"]=False
    assert core[1].post(f"/edit_products/{sku}/save",json=body).status_code==400
    body["acknowledge_shared"]=True
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code==200 and target.read_bytes()==before
    assert not (target.parent.parent/".update").exists()
    assert core[5][-1]["collection_relpath"]=="Simple Collection"


@pytest.mark.parametrize("stage", ["backup","json","marker","scan"])
def test_failures_report_precise_local_outcome(core,monkeypatch,stage):
    target=core[2]/"Simple Collection/Blue Token/product_info.json";before=target.read_bytes()
    def failure(*a,**k):raise OSError("injected failure")
    if stage=="backup":monkeypatch.setattr("app.routes.create_metadata_backup",failure)
    elif stage=="scan":monkeypatch.setattr("app.routes.start_scan",failure)
    else:
        from app.utils.atomic_files import atomic_write_text
        def write(path,text,**kwargs):
            if (Path(path).name==".update") == (stage=="marker"):raise OSError("injected failure")
            return atomic_write_text(path,text,**kwargs)
        monkeypatch.setattr("app.routes.atomic_write_text",write)
    response=save(core,data={"weight":55})
    assert response.status_code==500
    saved=stage in {"marker","scan"}
    assert response.json["json_saved"] is saved and response.json["scan_pending"] is saved
    assert (target.read_bytes()!=before) is saved
    assert (target.parent/".update").exists() is (stage=="scan")
    assert not core[6]
    with core[0].app_context():assert CatalogueOperation.query.filter_by(id=response.json["operation_id"]).one().status=="failed"


def test_noop_has_no_backup_marker_scan_or_notice(core):
    response=save(core)
    assert response.status_code==200 and response.json["unchanged"]
    assert not core[5] and not core[6]
    assert not list(core[2].rglob("*.bak.*")) and not list(core[2].rglob(".update"))


@pytest.mark.parametrize("escape", ["symlink","traversal"])
def test_escaped_owner_cannot_be_saved(core,tmp_path,escape):
    name="Simple Collection/Blue Token";sku,body=payload(core,name,data={"weight":55})
    outside=tmp_path/"private.json";outside.write_text('{}')
    if escape=="symlink":
        target=core[2]/name/"product_info.json";target.unlink();target.symlink_to(outside)
    else:
        with core[0].app_context():
            p=db.session.get(Product,core[4][name]);p.override_json_relpath="../private.json";db.session.commit()
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code in {400,409} and outside.read_text()=='{}'
    assert not core[5]


def test_admin_csrf_and_single_owner_controls(core):
    app,client,_,_,ids,*_=core
    page=client.get(f"/edit_products/{ids['Simple Collection/Blue Token']}/edit/override")
    assert page.status_code==200
    app.config["WTF_CSRF_ENABLED"]=True
    sku,body=payload(core,"Simple Collection/Blue Token",data={"weight":55})
    assert client.post(f"/edit_products/{sku}/save",json=body).status_code==400
    token=re.search(r'<meta name="csrf-token" content="([^"]+)"',client.get(f"/edit_products/{ids['Simple Collection/Blue Token']}/edit/override").text).group(1)
    assert client.post(f"/edit_products/{sku}/save",json=body,headers={"X-CSRFToken":token}).status_code==200
    assert client.get(f"/edit_products/{ids['Single Variable']}/edit/override").status_code==302
    with app.app_context():User.query.one().is_admin=False;db.session.commit()
    assert client.get(f"/edit_products/{ids['Single Variable']}/edit/shared").status_code==403


def test_form_coverage_source_and_readonly_images(core):
    app,client,_,_,ids,*_=core
    page=client.get(f"/edit_products/{ids['Variable Collection/Badge One']}/edit/shared").text
    for key in ("description","short_description","price","sale_price","weight","country_of_origin",
        "requires_shipping","vendor","handle","shopify_product_type_key","manage_stock","backorders","meta_title","meta_description"):
        assert f'data-field="{key}"' in page
    for key in ("variation_modifiers","image_attributes","attributes"):
        assert key in page
    assert "Indexed effective variations" in page and "No retained media" in page
    assert "Review Woo state" not in page and 'data-field="shipping_class"' not in page
    assert 'data-field="shopify_product_type"' not in page  # no arbitrary Product Type input
    assert 'data-field="sale_start_date"' in page and 'data-review-sale disabled' in page and 'id="saleReviewModal"' in page
    assert "metadata-section-nav" in page and "data-acknowledge-shared" not in page  # this collection has one child


def test_managed_fingerprint_only_changes_for_effective_managed_values(core):
    from app.shopify_products import source_fingerprint
    app,_,root,_,ids,*_=core
    name="Simple Collection/Blue Token"
    with app.app_context():before=source_fingerprint(db.session.get(Product,ids[name]),[])
    assert save(core,data={"production_notes":"Private", "future":[]}).status_code==200
    with app.app_context():assert source_fingerprint(db.session.get(Product,ids[name]),[])==before
    assert save(core,data={"weight":77}).status_code==200
    with app.app_context():assert source_fingerprint(db.session.get(Product,ids[name]),[])!=before


@pytest.mark.parametrize("name,kind", [("Simple Collection/Blue Token","override"),
    ("Variable Collection/Badge One","override"),("Single Variable","shared")])
def test_local_save_runs_real_scanner_and_projection_without_remote_writes(core,monkeypatch,name,kind):
    from app.utils.scan_runner import start_scan, get_progress
    monkeypatch.setattr("app.routes.start_scan",start_scan)
    monkeypatch.setattr("app.shopify_connection.ShopifyAdapter._request",lambda *a,**k:pytest.fail("Local save called Shopify"))
    app,_,root,_,ids,*_=core
    with app.app_context():
        p=db.session.get(Product,ids[name]);sku=p.sku
        variants=[(v.id,v.sku,v.source_identity,v.regular_price) for v in p.variations]
        evidence=json.dumps({"version":1,"media":[],"variant_id":"gid://shopify/ProductVariant/123",
            "inventory_id":"gid://shopify/InventoryItem/456","plan":{}})
        identity=ShopifyProductIdentity(product_id=p.id, source_identity=p.source_relpath, sku=p.sku,
            store_key="fictional",store_id="gid://shopify/Shop/1",store_domain="fictional.myshopify.com",
            shopify_product_id="gid://shopify/Product/1",state="verified",verification_state="verified",
            reviewed_digest="a"*64,media_evidence_json=evidence)
        db.session.add(identity);db.session.commit();identity_id=identity.id
    source_images={str(f):f.read_bytes() for f in (root/name).rglob("*.png")}
    marker=root/name/".scanned";previous=json.loads(marker.read_text())
    response=save(core,name,kind=kind,data={"weight":77,"description":"Fresh source copy"})
    assert response.status_code==200,response.json
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        with app.app_context():progress=get_progress(response.json["run_id"])
        if progress["status"] in {"done","error"}:break
        time.sleep(0.03)
    assert progress["status"]=="done",progress
    with app.app_context():
        p=db.session.get(Product,ids[name]);db.session.refresh(p)
        assert str(p.weight)=="77.000" or float(p.weight)==77
        assert p.description=="Fresh source copy" and p.sku==sku
        assert [(v.id,v.sku,v.source_identity,v.regular_price) for v in p.variations]==variants
        identity=db.session.get(ShopifyProductIdentity,identity_id)
        assert identity.media_evidence_json==evidence and identity.shopify_product_id=="gid://shopify/Product/1"
    assert not (marker.parent/".update").exists()
    assert json.loads(marker.read_text())["sku"]==previous["sku"]
    assert all(Path(path).read_bytes()==data for path,data in source_images.items())


def test_atomic_guard_catches_filesystem_edit_during_temporary_write(core,monkeypatch):
    import app.utils.atomic_files as atomic
    target=core[2]/"Simple Collection/Blue Token/product_info.json"
    real=atomic.os.fsync;changed=False
    def sync(fd):
        nonlocal changed
        real(fd)
        # The source backup fsync precedes the temp-source fsync. Inject only
        # while the replacement temp exists, immediately before its final guard.
        if list(target.parent.glob(".product_info.json.*.tmp")) and not changed:
            changed=True;target.write_text('{"description":"Concurrent filesystem edit"}')
    monkeypatch.setattr(atomic.os,"fsync",sync)
    response=save(core,data={"weight":88})
    assert response.status_code==409 and not response.json["json_saved"]
    assert json.loads(target.read_text())=={"description":"Concurrent filesystem edit"}
    assert not list(target.parent.glob(".product_info.json.*.tmp")) and not core[5]


def test_marker_failure_can_retry_only_scan_without_rewriting_json(core,monkeypatch):
    from app.utils.atomic_files import atomic_write_text
    def write(path,text,**kwargs):
        if Path(path).name==".update":raise OSError("marker unavailable")
        return atomic_write_text(path,text,**kwargs)
    monkeypatch.setattr("app.routes.atomic_write_text",write)
    failed=save(core,data={"weight":77})
    assert failed.status_code==500 and failed.json["json_saved"]
    target=core[2]/"Simple Collection/Blue Token/product_info.json";before=target.read_bytes()
    backups=list(target.parent.glob("*.bak.*"))
    monkeypatch.setattr("app.routes.atomic_write_text",atomic_write_text)
    sku,body=payload(core,"Simple Collection/Blue Token")
    body.update(revision=failed.json["revision"],retry_scan=True)
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code==200 and target.read_bytes()==before
    assert list(target.parent.glob("*.bak.*"))==backups and (target.parent/".update").exists()
    assert len(core[5])==1 and not core[6]  # retry doesn't duplicate editor notification


def test_scheduled_dates_and_customisation_remain_definitions_not_remote_actions(core):
    from app.commerce import catalogue_snapshot
    from app.shopify_simple_fields import contract
    from app.shopify_connection import ShopifyError
    data={"sale_price":"5.00","sale_start_date":"2027-12-01","sale_end_date":"2027-12-31",
          "customisation":{"fields":{"name":{"type":"text","label":"Dog's Name","required":True}}}}
    assert save(core,data=data).status_code==200
    with core[0].app_context():
        from app.models import ShopifySaleSchedule
        assert ShopifySaleSchedule.query.count()==0  # Saving never authorises a worker.
        assert not CatalogueOperation.query.filter(CatalogueOperation.scope.contains('sale_authorisation')).count()
        p=db.session.get(Product,core[4]["Simple Collection/Blue Token"])
        snap=catalogue_snapshot(p)
        managed,_=contract(snap,{"title":p.title,"description":"","price":"9.25","sku":p.sku})
        assert managed['variant']['price']=='9.25' and managed['variant']['compareAtPrice'] is None
        assert snap.data["customisation"]["fields"]["name"]["type"]=="text"


@pytest.mark.parametrize('name,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','shared'),('Single Variable','shared')])
def test_status_channels_and_sale_save_cannot_authorise_shopify(core,monkeypatch,name,kind):
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._request',lambda *a,**k:pytest.fail('Local edit invoked Shopify'))
    result=save(core,name,kind=kind,data={'shopify_status':'ARCHIVED','live':False,'shopify_channels':{'online-store':False},
        'requires_shipping':False,'sale_price':'1.00','sale_start_date':'2027-12-01','sale_end_date':'2027-12-31'})
    assert result.status_code==200,result.json
    from app.models import ShopifySaleSchedule
    with core[0].app_context(): assert ShopifySaleSchedule.query.count()==0


def test_existing_layout_or_prefix_change_is_blocked(core):
    for data in ({"collection_type":"Variable Collection"},{"sku_prefix":"NEW-"}):
        response=save(core,kind="shared",data=data)
        assert response.status_code==400
    assert not core[5] and not core[6]


@pytest.mark.parametrize("dependency", ["child", "registry"])
def test_shared_impact_review_rejects_changed_dependencies(core,monkeypatch,dependency):
    sku,body=payload(core,"Simple Collection/Blue Token",kind="shared",data={"weight":55})
    if dependency=="child":
        path=core[2]/"Simple Collection/Green Token/product_info.json"
        path.write_text('{"description":"New child override"}')
    else:
        import app.taxonomy_assignments as assignments
        actual=assignments.options
        monkeypatch.setattr(assignments,"options",lambda **k:{**actual(**k),"digest":"changed"})
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code==409 and not core[5]


def test_existing_owner_fields_and_missing_sources_are_visible_without_mutation(core):
    name="Variable Collection/Badge One"
    target=core[2]/name/"badge.png";target.unlink()
    html=core[1].get(f"/edit_products/{core[4][name]}/edit/override").text
    assert "Local source unavailable" in html or "Url Only" in html
    assert "Edit Shared Collection Data" in html and "data-override-toggle" in html
    assert not core[5] and not core[6]


@pytest.mark.parametrize("name", ["Simple Collection/Blue Token","Variable Collection/Badge One","Single Variable"])
def test_editor_resolution_uses_the_same_json_owner_as_scanner(core,name):
    from app.taxonomy_assignments import product_document
    with core[0].app_context():
        product=db.session.get(Product,core[4][name])
        assert product_document(product)["title"]==product.title


def test_failed_local_scan_receipt_survives_reload_and_retry(core,monkeypatch):
    def fail(*a,**k):raise OSError("scanner unavailable")
    monkeypatch.setattr("app.routes.start_scan",fail)
    result=save(core,data={"weight":79})
    assert result.json["json_saved"] and result.status_code==500
    page=core[1].get(f"/edit_products/{core[4]['Simple Collection/Blue Token']}/edit/override")
    assert page.status_code==200 and "Local scan/projection needs completion" in page.text
    with core[0].app_context():
        op=db.session.get(CatalogueOperation,result.json["operation_id"])
        assert json.loads(op.scope)["operation_summary"]["json_saved"] is True


def test_single_owner_products_browser_has_no_override_offer(core):
    response=core[1].get("/api/edit_products")
    assert response.status_code==200
    rows=response.json.get("products",response.json.get("items",[]))
    assert rows
    row=next(p for p in rows if p["id"]==core[4]["Single Variable"])
    assert row["can_override"] is False and row["override_edit_url"] is None


def test_raw_line_ending_changes_invalidate_the_source_review(core):
    target=core[2]/"Simple Collection/Blue Token/product_info.json"
    sku,body=payload(core,"Simple Collection/Blue Token",data={"weight":50})
    original=target.read_bytes()
    target.write_bytes(original.replace(b"\n",b"\r\n"))
    assert original!=target.read_bytes()
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code==409 and not core[5]


def test_legacy_override_removal_uses_verified_owner_not_stale_asset_path(core):
    name="Simple Collection/Blue Token"
    _,body=payload(core,name)
    owned=core[2]/name/"product_info.json"
    other=core[2]/"Variable Collection/Badge One/product_info.json";before=other.read_bytes()
    with core[0].app_context():
        asset=ProductAsset.query.filter_by(product_id=core[4][name],kind="info",label="override").first()
        asset.path=str(other);db.session.commit()
    response=core[1].post(f"/api/delete-override/{core[4][name]}",json={"revision":body["revision"]})
    assert response.status_code==200,response.json
    assert other.read_bytes()==before and not owned.exists()
    assert list(owned.parent.glob("product_info.json.bak.*"))


@pytest.mark.parametrize("token", [None,{},[],123,"x"*20000], ids=["missing","object","list","number","oversize"])
def test_malformed_review_is_rejected_without_side_effects(core,token):
    sku,body=payload(core,"Simple Collection/Blue Token",data={"weight":55})
    body["revision"]=token
    response=core[1].post(f"/edit_products/{sku}/save",json=body)
    assert response.status_code==409 and not core[5] and not core[6]
