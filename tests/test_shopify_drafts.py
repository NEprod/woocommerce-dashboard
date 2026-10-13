"""M3A fictional draft writes only; no store/network/source mutation."""
import copy
import json
import sqlite3
import requests
from pathlib import Path

import pytest
from PIL import Image
from alembic import command
from sqlalchemy.exc import IntegrityError

from app import db
from app import shopify_drafts as drafts
from app.models import Product, ProductImage, Settings, ShopifyProductIdentity, CatalogueOperation, ShopifyCollectionIdentity
from app.database import _alembic_config, ensure_database, migration_head
from app.shopify_connection import ShopifyError, ShopifyAdapter
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import TOKEN, Session, Response, isolated_transport


class FakeDraftAdapter:
    def collection_state(self, config, remote_id, product_id="gid://shopify/Product/0"):
        return {"id": remote_id, "title": "Preview Cards", "ruleSet": None,
            "hasProduct": bool(self.remote), "metafield": None, "sources": []}

    def find_sku(self, config, sku):
        return {"nodes": [], "pageInfo": {"hasNextPage": False}}

    def __init__(self, failure=None, processing=False):
        self.failure, self.processing, self.calls, self.remote = failure, processing, [], None

    def test_connection(self, config):
        self.calls.append("connection_read")
        return {"shop_id": "gid://shopify/Shop/42", "scopes": ["read_products", "write_products"]}

    def stage_images(self, config, images):
        row = ShopifyProductIdentity.query.one()
        assert row.state == "pending" and row.shopify_product_id is None
        assert json.loads(row.media_evidence_json)["create_attempted"] is False
        assert db.session.get(CatalogueOperation, row.operation_id).status == "running"
        self.calls.append("stage_write")
        if self.failure == "stage": raise ShopifyError("Staged targets failed")
        return {"stagedUploadsCreate": {"stagedTargets": [
            {"url": "https://storage.googleapis.com/upload", "resourceUrl": f"https://storage.googleapis.com/image/{i}", "parameters": []}
            for i in range(len(images))], "userErrors": []}}

    def upload_image(self, config, target, image, content):
        self.calls.append("binary_upload")
        assert content and image["filename"].endswith((".png", ".jpg", ".webp"))
        if self.failure == "upload": raise ShopifyError("Upload failed")

    def create_draft(self, config, product, media):
        row = ShopifyProductIdentity.query.one()
        assert json.loads(row.media_evidence_json)["create_attempted"] is True
        assert row.state == "creating"
        self.calls.append("product_create")
        assert product["status"] == "DRAFT" and "publications" not in product
        self.remote = dict(product, id="gid://shopify/Product/100", metafield={"value": product["metafields"][0]["value"]},
            variants={"nodes": [{"id": "gid://shopify/ProductVariant/200", "sku": "", "price": "0.00"}], "pageInfo": {"hasNextPage": False}},
            media={"nodes": [{"id": f"gid://shopify/MediaImage/{300+i}", "alt": m["alt"], "status": "PROCESSING" if self.processing else "READY",
                "mediaContentType": "IMAGE", "image": None if self.processing else {"url": f"https://cdn.shopify.com/image-{i}.png?v=123"}} for i, m in enumerate(media)], "pageInfo": {"hasNextPage": False}})
        self.remote.setdefault("vendor", "")
        self.remote.setdefault("tags", [])
        self.remote.setdefault("seo", {"title": None, "description": None})
        for key in ("length", "width", "height"):
            self.remote[key] = next((dict(m, id=f"gid://shopify/Metafield/{400+len(key)}")
                for m in product["metafields"] if m["key"] == "product_" + key), None)
        self.remote["simpleVariants"] = copy.deepcopy(self.remote["variants"])
        self.remote["simpleVariants"]["nodes"][0].update(compareAtPrice=None, inventoryItem={
            "id": "gid://shopify/InventoryItem/250", "sku": "", "requiresShipping": True,
            "countryCodeOfOrigin": None, "tracked": False, "measurement": {"weight": {"value": 0, "unit": "GRAMS"}}})
        self.remote["simpleVariants"]["nodes"][0]["inventoryPolicy"]="DENY"
        if self.failure == "create": raise ShopifyError("Creation outcome uncertain")
        return {"productCreate": {"product": copy.deepcopy(self.remote), "userErrors": []}}

    def set_simple_variant(self, config, product_id, variant_id, sku, price):
        self.calls.append("variant_write")
        assert ShopifyProductIdentity.query.one().shopify_product_id == product_id
        if self.failure == "variant": raise ShopifyError("Variant outcome uncertain")
        self.remote["variants"]["nodes"][0].update(sku=sku, price=price)
        return {"productVariantsBulkUpdate": {"userErrors": [], "productVariants": self.remote["variants"]["nodes"]}}

    def read_draft(self, config, product_id):
        self.calls.append("product_read")
        return copy.deepcopy(self.remote)

    def country_codes(self, config):
        return {"GB", "CN", "US"}

    def update_simple_variant(self, config, product_id, variant):
        current = self.remote["simpleVariants"]["nodes"][0]
        self.set_simple_variant(config, product_id, variant["id"],
            variant.get("inventoryItem", {}).get("sku", current["sku"]), variant.get("price", current["price"]))
        current.update({k: v for k, v in variant.items() if k != "inventoryItem"})
        current["inventoryItem"].update(variant.get("inventoryItem", {}))
        current["sku"] = current["inventoryItem"]["sku"]
        return {"productVariantsBulkUpdate": {"userErrors": [], "productVariants": [current]}}

    def read_simple(self, config, product_id, namespace):
        remote = self.read_draft(config, product_id)
        if remote is not None:
            remote.setdefault("updatedAt", "2026-10-12T00:00:00Z")
        return remote

    def find_draft(self, config, handle):
        self.calls.append("find_read")
        return {"nodes": [copy.deepcopy(self.remote)] if self.remote else [], "pageInfo": {"hasNextPage": False}}


@pytest.fixture
def world(preview_app, monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "fictional.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", TOKEN)
    with preview_app.app_context():
        product = db.session.get(Product, 1)
        root = Path(Settings.query.first().product_folder)
        folder = root / product.source_relpath
        folder.mkdir(parents=True)
        shared = folder.parent / "product_info.json"
        shared.write_text(json.dumps({"collection_type": "Simple", "price": "3.50", "description": "Reviewed description", "variation_attributes": [], "live": False}))
        if product.collection and product.collection.source_relpath:
            db.session.add(ShopifyCollectionIdentity(store_id="gid://shopify/Shop/42",
                source_key="collection:" + product.collection.source_relpath, remote_id="gid://shopify/Collection/800",
                state="verified", evidence_json='{"version":1}'))
        product.images.clear()
        for i, (name, fmt) in enumerate([("original.png", "PNG"), ("second.jpg", "JPEG")]):
            Image.new("RGB", (5, 5), "red").save(folder / name, format=fmt)
            product.images.append(ProductImage(url=name, position=i, alt_text=f"Image {i}"))
        db.session.commit()
        yield product, folder, shared


def test_preview_is_read_only_and_confirmation_required(world):
    product, folder, shared = world
    adapter = FakeDraftAdapter()
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    plan, token = drafts.preview(product, adapter)
    assert adapter.calls == ["connection_read"] and ShopifyProductIdentity.query.count() == 0
    assert [i["filename"] for i in plan["images"]] == ["original.png", "second.jpg"]
    assert plan["status"] == "DRAFT" and plan["price"] == "3.50"
    with pytest.raises(ShopifyError, match="acknowledgement"):
        drafts.confirm(token, False, adapter)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_complete_draft_and_media_verified_without_local_changes(world):
    product, folder, shared = world
    adapter = FakeDraftAdapter()
    before = shared.read_bytes(), [(p.name, p.read_bytes()) for p in folder.iterdir()]
    plan, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == row.verification_state == "verified" and row.verified_at
    assert row.shopify_product_id == "gid://shopify/Product/100"
    data = drafts.evidence(row)
    assert [m["id"] for m in data["media"]] == ["gid://shopify/MediaImage/300", "gid://shopify/MediaImage/301"]
    assert data["media"][0]["url"] == "https://cdn.shopify.com/image-0.png?v=123"
    assert TOKEN not in row.media_evidence_json
    assert db.session.get(CatalogueOperation, row.operation_id).status == "succeeded"
    assert before == (shared.read_bytes(), [(p.name, p.read_bytes()) for p in folder.iterdir()])
    assert product.sku == "CREATE-1" and product.woo_id is None
    with pytest.raises(ShopifyError, match="no duplicate creation"):
        drafts.confirm(token, True, adapter)
    assert adapter.calls.count("product_create") == 1


def test_store_key_stable_across_verified_alias_changes(world, monkeypatch):
    product, _, _ = world
    adapter = FakeDraftAdapter()
    first, token = drafts.preview(product, adapter)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "alias.myshopify.com")
    second, _ = drafts.preview(product, adapter)
    assert first["store_key"] == second["store_key"]
    with pytest.raises(ShopifyError, match="changed"):
        drafts.confirm(token, True, adapter)
    assert "product_create" not in adapter.calls


def test_alias_preserves_legacy_identity_and_duplicate_recovery_guards(world, monkeypatch):
    product, _, _ = world
    adapter = FakeDraftAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    # Simulate an existing M3A domain-keyed record. Never migrate/rewrite it.
    row.store_key = drafts.digest({"id": row.store_id, "domain": row.store_domain})
    db.session.commit()
    before = row.store_key, row.store_domain, row.shopify_product_id
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "alias.myshopify.com")
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "no_change" and token is None
    drafts.reconcile(row, adapter=adapter)
    assert before == (row.store_key, row.store_domain, row.shopify_product_id)
    assert row.state == "verified" and adapter.calls.count("product_create") == 1
    adapter.test_connection = lambda config: {"shop_id": "gid://shopify/Shop/99", "scopes": ["write_products"]}
    with pytest.raises(ShopifyError, match="does not match"):
        drafts.reconcile(row, adapter=adapter)


@pytest.mark.parametrize("change", ["metadata", "image"])
def test_stale_source_or_image_refuses_writes(world, change):
    product, folder, shared = world
    adapter = FakeDraftAdapter()
    _, token = drafts.preview(product, adapter)
    if change == "metadata": shared.write_text(json.dumps({"collection_type": "Simple", "price": "4.00", "live": False}))
    else: Image.new("RGB", (6, 6), "blue").save(folder / "original.png")
    with pytest.raises(ShopifyError, match="changed"):
        drafts.confirm(token, True, adapter)
    assert "product_create" not in adapter.calls and ShopifyProductIdentity.query.count() == 0


@pytest.mark.parametrize("failure", ["stage", "upload", "create", "variant"])
def test_partial_failures_and_duplicate_guard(world, failure):
    product, _, _ = world
    adapter = FakeDraftAdapter(failure)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == ("failed_before_create" if failure in {"stage", "upload"} else "uncertain")
    assert db.session.get(CatalogueOperation, row.operation_id).status in {"failed", "partial"}
    if failure in {"create", "variant"}:
        with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
        assert adapter.calls.count("product_create") == 1
    if failure == "variant": assert row.shopify_product_id == "gid://shopify/Product/100"


def test_async_processing_rechecks_reads_only(world):
    product, _, _ = world
    adapter = FakeDraftAdapter(processing=True)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == "processing" and row.verified_at is None
    for i, node in enumerate(adapter.remote["media"]["nodes"]):
        node.update(status="READY", image={"url": f"https://cdn.shopify.com/{i}.jpg?format=auto"})
    previous = adapter.calls[:]
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "verified" and adapter.calls[len(previous):] == ["connection_read", "product_read"]


def test_uncertain_create_review_and_explicit_media_adoption(world):
    product, _, _ = world
    adapter = FakeDraftAdapter("create")
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.shopify_product_id is None
    # Operator fixes the retained draft in Shopify Admin, not this implementation.
    adapter.remote["variants"]["nodes"][0].update(sku=product.sku, price="3.50")
    adapter.remote["simpleVariants"]["nodes"][0].update(sku=product.sku, price="3.50")
    adapter.remote["simpleVariants"]["nodes"][0]["inventoryPolicy"] = "CONTINUE"
    adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["sku"] = product.sku
    db.session.expire_all()  # Cache loss does not permit another create.
    row = db.session.get(ShopifyProductIdentity, row.id)
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "uncertain" and row.shopify_product_id
    approval = drafts.recovery_token(row)
    assert approval
    drafts.reconcile(row, adapter=adapter, approval=approval)
    assert row.state == "verified" and drafts.evidence(row)["media_mapping_approved_at"]
    assert adapter.calls.count("product_create") == 1


def test_absent_uncertain_create_never_authorizes_retry(world):
    product, _, _ = world
    adapter = FakeDraftAdapter("create")
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.remote = None
    with pytest.raises(ShopifyError, match="empty read"):
        drafts.reconcile(row, adapter=adapter)
    assert row.state == "uncertain"
    with pytest.raises(ShopifyError): drafts.preview(product, adapter)


def test_recovery_rejects_stale_approval_and_wrong_correlation(world):
    product, _, _ = world
    adapter = FakeDraftAdapter("create")
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    drafts.reconcile(row, adapter=adapter)
    approval = drafts.recovery_token(row)
    adapter.remote["title"] = "Changed"
    with pytest.raises(ShopifyError, match="changed"):
        drafts.reconcile(row, adapter=adapter, approval=approval)
    adapter.remote["metafield"]["value"] = "different"
    with pytest.raises(ShopifyError, match="correlation"):
        drafts.reconcile(row, adapter=adapter)


def test_ui_csrf_auth_and_no_get_writes(preview_app, world, monkeypatch):
    product, _, _ = world
    adapter = FakeDraftAdapter()
    monkeypatch.setattr(drafts, "ShopifyAdapter", lambda: adapter)
    client = _client(preview_app)
    assert client.get("/shopify/drafts").status_code == 200 and adapter.calls == []
    result = client.post("/shopify/drafts", data={"action": "preview", "product_id": product.id})
    assert result.status_code == 200 and b"original.png" in result.data and TOKEN.encode() not in result.data
    preview_app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/shopify/drafts", data={"action": "confirm"}).status_code == 400


def test_draft_route_requires_authentication(preview_app):
    assert preview_app.test_client().get("/shopify/drafts").status_code == 401


def test_original_upload_bytes_without_token_or_redirect(monkeypatch):
    response = Response(status=204)
    session = Session(response)
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
    from app.shopify_connection import ShopifyConfiguration
    config = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
    target = {"url": "https://storage.googleapis.com/upload", "resourceUrl": "https://storage.googleapis.com/image", "parameters": [{"name": "key", "value": "signed"}]}
    ShopifyAdapter().upload_image(config, target, {"filename": "original.png", "mime": "image/png"}, b"original-bytes")
    kwargs = session.calls[0][1]
    assert kwargs["files"]["file"] == ("original.png", b"original-bytes", "image/png")
    assert "headers" not in kwargs and kwargs["allow_redirects"] is False and session.trust_env is False
    target["url"] = "https://127.0.0.1/upload"
    with pytest.raises(ShopifyError): ShopifyAdapter().upload_image(config, target, {}, b"unused")
    assert len(session.calls) == 1


def test_fresh_and_upgrade_preserve_woo(tmp_path):
    fresh = tmp_path / "fresh.db"
    assert ensure_database(f"sqlite:///{fresh}").revision == migration_head()
    with sqlite3.connect(fresh) as con:
        assert con.execute("select count(*) from shopify_product_identity").fetchone()[0] == 0
    old = tmp_path / "old.db"
    config = _alembic_config(f"sqlite:///{old}")
    command.upgrade(config, "0009_woo_live_state")
    with sqlite3.connect(old) as con:
        con.execute("insert into product (id,title) values (1,'Old')")
        con.execute("insert into woo_product_identity (product_id,stable_identity,store_key,store_host,woo_product_id,live_state_json) values (1,'source','woo-store','example.test',9,'unchanged')")
        before = con.execute("select * from woo_product_identity").fetchall()
    ensure_database(f"sqlite:///{old}")
    with sqlite3.connect(old) as con:
        assert before == con.execute("select * from woo_product_identity").fetchall()
        assert con.execute("select count(*) from shopify_product_identity").fetchone()[0] == 0


@pytest.mark.parametrize("collision", ["product", "source", "remote"])
def test_sqlite_enforces_store_uniqueness(world, collision):
    product, _, _ = world
    def row(pid, source, remote, store="store"):
        return ShopifyProductIdentity(product_id=pid, source_identity=source, sku="SKU", store_key=store,
            store_id="gid://shopify/Shop/42", store_domain="fictional.myshopify.com", shopify_product_id=remote,
            state="pending", verification_state="unverified", reviewed_digest="a" * 64, media_evidence_json='{"version":1}')
    db.session.add(row(1, "first", "gid://shopify/Product/1")); db.session.commit()
    values = {"product": (1, "second", "gid://shopify/Product/2"), "source": (2, "first", "gid://shopify/Product/2"), "remote": (2, "second", "gid://shopify/Product/1")}[collision]
    db.session.add(row(*values))
    with pytest.raises(IntegrityError): db.session.commit()
    db.session.rollback()
    db.session.add(row(1, "first", "gid://shopify/Product/1", "different-store")); db.session.commit()


def test_database_evidence_bound(world):
    product, _, _ = world
    _, token = drafts.preview(product, FakeDraftAdapter())
    row = drafts.confirm(token, True, FakeDraftAdapter())
    row.media_evidence_json = "x" * 65537
    with pytest.raises(IntegrityError): db.session.commit()
    db.session.rollback()


def test_restart_preserves_pending_guard_and_allows_only_reconciliation(world):
    product, _, _ = world
    adapter = FakeDraftAdapter("create")
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    op = db.session.get(CatalogueOperation, row.operation_id)
    op.status = "running"; db.session.commit()
    with pytest.raises(ShopifyError, match="still active"):
        drafts.reconcile(row, adapter=adapter)
    from app.utils.operation_control import recover_interrupted_operations
    assert recover_interrupted_operations() == 1
    db.session.expire_all()
    assert op.status == "interrupted" and op.recovery_state == "review_required"
    with pytest.raises(ShopifyError): drafts.preview(product, adapter)
    drafts.reconcile(row, adapter=adapter)
    assert row.shopify_product_id and adapter.calls.count("product_create") == 1
    assert op.status == "interrupted"  # Recovery never rewrites historical operations.


def test_webp_original_is_supported_without_conversion(world):
    product, folder, _ = world
    Image.new("RGB", (5, 5), "blue").save(folder / "third.webp", format="WEBP")
    product.images.append(ProductImage(url="third.webp", position=2, alt_text="WebP original"))
    db.session.commit()
    before = (folder / "third.webp").read_bytes()
    plan = drafts.local_plan(product)
    assert plan["images"][2]["mime"] == "image/webp"
    assert drafts.source_bytes(product, plan["images"][2]["source"])[0] == before


def test_failed_media_preserves_identity_and_observations(world):
    product, _, _ = world
    adapter = FakeDraftAdapter(processing=True)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.remote["media"]["nodes"][0]["status"] = "FAILED"
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "uncertain" and row.verification_state == "unverified"
    assert drafts.evidence(row)["observed_media"][0]["id"] == "gid://shopify/MediaImage/300"
    assert "processing failed" in drafts.evidence(row)["error"]
    with pytest.raises(ShopifyError): drafts.preview(product, adapter)


@pytest.mark.parametrize("failure", [requests.Timeout(TOKEN), requests.ConnectionError(TOKEN)])
def test_mutation_transport_never_retries_and_redacts(monkeypatch, failure):
    session = Session(error=failure)
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
    from app.shopify_connection import ShopifyConfiguration
    with pytest.raises(ShopifyError) as exc:
        ShopifyAdapter().create_draft(ShopifyConfiguration("fictional.myshopify.com", TOKEN), {}, [])
    assert len(session.calls) == 1 and TOKEN not in str(exc.value)
    assert "Reconcile" in str(exc.value) and "blindly retry" in str(exc.value)


def test_fixed_mutation_contracts_and_read_only_boundary(monkeypatch):
    session = Session(Response({"data": {}}))
    monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
    from app.shopify_connection import ShopifyConfiguration, CREATE_QUERY
    config = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
    service = ShopifyAdapter()
    service.stage_images(config, [{"filename": "original.jpg", "mime": "image/jpeg", "size": 12}])
    service.set_simple_variant(config, "gid://shopify/Product/1", "gid://shopify/ProductVariant/2", "SKU", "3.50")
    assert session.calls[0][1]["json"]["variables"]["input"][0] == {
        "filename": "original.jpg", "mimeType": "image/jpeg", "resource": "PRODUCT_IMAGE", "httpMethod": "POST", "fileSize": "12"}
    assert session.calls[1][1]["json"]["variables"]["variants"][0]["inventoryItem"] == {"sku": "SKU"}
    with pytest.raises(ShopifyError): service._read(config, CREATE_QUERY)
    assert len(session.calls) == 2


def test_draft_route_requires_administrator(preview_app):
    from app.models import User
    client = _client(preview_app)
    with preview_app.app_context():
        db.session.get(User, 1).is_admin = False
        db.session.commit()
    assert client.get("/shopify/drafts").status_code == 403


def test_malformed_recovery_evidence_never_verifies(world):
    product, _, _ = world
    adapter = FakeDraftAdapter(processing=True)
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.remote["media"]["nodes"] = None
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "uncertain" and row.verification_state == "unverified"
    assert "incomplete" in drafts.evidence(row)["error"]


def test_identity_survives_projection_and_history_removal(world):
    product, _, _ = world
    adapter = FakeDraftAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    source, remote = row.source_identity, row.shopify_product_id
    product_id, operation_id = product.id, row.operation_id
    db.session.execute(db.text("DELETE FROM catalogue_operation WHERE id = :id"), {"id": row.operation_id})
    db.session.execute(db.text("DELETE FROM product WHERE id = :id"), {"id": product.id})
    db.session.commit(); db.session.expire_all()
    assert db.session.get(Product, product_id) is None
    assert db.session.get(CatalogueOperation, operation_id) is None
    # SQLite FK enforcement is connection-specific in the existing app. Neither
    # enabled SET NULL nor disabled enforcement may cascade-delete the reservation.
    assert row.source_identity == source and row.shopify_product_id == remote
