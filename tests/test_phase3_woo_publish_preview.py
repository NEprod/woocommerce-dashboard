import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic import command
from sqlalchemy import event

from app import create_app, db
from app.database import _alembic_config
from app.models import (
    CatalogueOperation, CatalogueOperationItem, Category, Collection, Product, ProductImage,
    ProductRelationship, Settings, Tag, User, Variation, VariationAttribute,
    VariationImage, WooProductIdentity, WooVariationIdentity,
)
from app.woo_publish_preview import (
    BUILDER_VERSION, PreviewError, PreviewWooReader, _comparison,
    _product_payload, _variation_payload, cached_plan, generate_publish_plan,
    plan_is_stale, reset_preview_cache_for_tests, scope_estimate, store_identity,
)
from app.woo_controlled_publish import (
    ControlledPublishError,
    MAX_PUBLISH_PRODUCTS,
    PublishGateway,
    execute_publish_operation,
    prepare_publish_confirmation,
    resume_confirmation,
    start_publish_operation,
)
from app.utils.operation_control import reset_operation_control_for_tests
from app.woocommerce_connection import PublisherWooClient, WooConfiguration, WooConnectionError
from config import Config


class FakeWooClient:
    def __init__(self, *, by_id=None, by_sku=None, taxonomy=None):
        self.base_url = "https://shop.example.test"
        self.by_id = by_id or {}
        self.by_sku = by_sku or {}
        self.taxonomy = taxonomy or {}
        self.request_count = 0
        self.methods = []

    def request_json(self, method, url, **kwargs):
        self.methods.append(method); self.request_count += 1
        if method != "GET": raise AssertionError("Woo write attempted")
        parsed = urlsplit(url); path = parsed.path; query = parse_qs(parsed.query)
        if path.endswith("/products/categories"): return self.taxonomy.get("categories", []), object()
        if path.endswith("/products/tags"): return self.taxonomy.get("tags", []), object()
        if path.endswith("/products/attributes"): return self.taxonomy.get("attributes", []), object()
        if "/variations" in path: return [], object()
        tail = path.rsplit("/", 1)[-1]
        if tail.isdigit():
            value = self.by_id.get(int(tail), "missing")
            if value == "missing": raise WooConnectionError("not_found", "Not found", status_code=404)
            return value, object()
        sku = query.get("sku", [""])[0]
        return self.by_sku.get(sku, []), object()


class FakePublisherClient:
    publisher_policy = True

    def __init__(self, *, taxonomy=None, uncertain_product_create=False):
        self.base_url = "https://shop.example.test"
        self.taxonomy = taxonomy or _taxonomy()
        self.request_count = 0
        self.methods = []
        self.writes = []
        self.products = {}
        self.variations = {}
        self.next_product_id = 501
        self.next_variation_id = 601
        self.uncertain_product_create = uncertain_product_create

    def _taxonomy_kind(self, path):
        if path.endswith("/products/categories") or "/products/categories/" in path: return "categories"
        if path.endswith("/products/tags") or "/products/tags/" in path: return "tags"
        if path.endswith("/products/attributes") or "/products/attributes/" in path and "/terms" not in path: return "attributes"
        return None

    def request_json(self, method, url, **kwargs):
        method = method.upper(); self.methods.append(method); self.request_count += 1
        if method == "DELETE": raise AssertionError("DELETE attempted")
        parsed = urlsplit(url); path = parsed.path; query = parse_qs(parsed.query)
        body = kwargs.get("json_body") or {}
        taxonomy_kind = self._taxonomy_kind(path)
        if taxonomy_kind:
            rows = self.taxonomy.setdefault(taxonomy_kind, [])
            tail = path.rsplit("/", 1)[-1]
            if method == "GET" and tail.isdigit():
                return next(row for row in rows if row["id"] == int(tail)), object()
            if method == "GET":
                slug = query.get("slug", [None])[0]
                return ([row for row in rows if row.get("slug") == slug] if slug else rows), object()
            new = {"id": max([row["id"] for row in rows] or [10]) + 1, **body}; rows.append(new); self.writes.append((method, path, body)); return new, object()
        if "/terms" in path:
            key = path.split("/attributes/", 1)[1].split("/terms", 1)[0]
            rows = self.taxonomy.setdefault(f"terms:{key}", [])
            tail = path.rsplit("/", 1)[-1]
            if method == "GET" and tail.isdigit(): return next(row for row in rows if row["id"] == int(tail)), object()
            if method == "GET":
                slug = query.get("slug", [None])[0]; return ([row for row in rows if row.get("slug") == slug] if slug else rows), object()
            new = {"id": max([row["id"] for row in rows] or [80]) + 1, **body}; rows.append(new); self.writes.append((method, path, body)); return new, object()
        if "/variations" in path:
            parent_id = int(path.split("/products/", 1)[1].split("/", 1)[0])
            rows = self.variations.setdefault(parent_id, {})
            tail = path.rsplit("/", 1)[-1]
            if method == "GET" and tail.isdigit(): return rows[int(tail)], object()
            if method == "GET":
                sku = query.get("sku", [None])[0]; values = list(rows.values()); return ([row for row in values if row.get("sku") == sku] if sku else values), object()
            if method == "POST":
                remote = {"id": self.next_variation_id, **body}; self.next_variation_id += 1; rows[remote["id"]] = remote
            else:
                remote_id = int(tail); remote = {**rows[remote_id], **body}; rows[remote_id] = remote
            self.writes.append((method, path, body)); return remote, object()
        tail = path.rsplit("/", 1)[-1]
        if method == "GET" and tail.isdigit(): return self.products[int(tail)], object()
        if method == "GET":
            sku = query.get("sku", [None])[0]; values = list(self.products.values()); return ([row for row in values if row.get("sku") == sku] if sku else values), object()
        if method == "POST":
            remote = {"id": self.next_product_id, "cross_sell_ids": [], "upsell_ids": [], **body}; self.next_product_id += 1; self.products[remote["id"]] = remote; self.writes.append((method, path, body))
            if self.uncertain_product_create:
                self.uncertain_product_create = False
                raise WooConnectionError("read_timeout", "Response timed out")
            return remote, object()
        remote_id = int(tail); remote = {**self.products[remote_id], **body}; self.products[remote_id] = remote; self.writes.append((method, path, body)); return remote, object()


@pytest.fixture
def preview_app(tmp_path, monkeypatch):
    instance = tmp_path / "instance"; catalogue = tmp_path / "catalogue"; output = tmp_path / "output"
    for path in (instance, catalogue, output): path.mkdir()
    original = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    monkeypatch.setenv("WOO_STORE_URL", "https://shop.example.test")
    monkeypatch.setenv("WOO_CONSUMER_KEY", "ck_fictional_test_only")
    monkeypatch.setenv("WOO_CONSUMER_SECRET", "cs_fictional_test_only")
    monkeypatch.setenv("SECRET_KEY", "phase3-m3-fictional-preview-secret")
    app = create_app(); app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.session.add_all([User(id=1, email="preview@example.test", username="preview-admin", password="unused"), Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://media.example.test/")])
        collection = Collection(id=1, name="Preview Cards", root_path=str(catalogue / "Preview Cards"), shared_json_path=str(catalogue / "Preview Cards/product_info.json"), source_relpath="Preview Cards", sku_prefix="PREVIEW", collection_type="Variable Collection")
        db.session.add(collection); db.session.flush()
        category = Category(name="Cards", slug="cards"); tag = Tag(name="Birthday", slug="birthday")
        db.session.add_all([category, tag]); db.session.flush()
        products = [
            Product(id=1, collection_id=1, title="Create Card", sku="CREATE-1", product_type="simple", catalogue_status="active", published=False, regular_price=10, description="Create description", short_description="Create short", source_relpath="Preview Cards/Create"),
            Product(id=2, collection_id=1, title="Existing Card", sku="EXIST-1", product_type="simple", catalogue_status="active", published=True, regular_price=12, description="Existing description", short_description="Existing short", source_relpath="Preview Cards/Existing"),
            Product(id=3, collection_id=1, title="Link Card", sku="LINK-1", product_type="simple", catalogue_status="active", published=True, regular_price=14, source_relpath="Preview Cards/Link"),
            Product(id=4, collection_id=1, title="Variable Card", sku="VARIABLE-1", product_type="variable", catalogue_status="active", published=True, regular_price=20, source_relpath="Preview Cards/Variable"),
        ]
        for product in products: product.categories.append(category); product.tags.append(tag)
        db.session.add_all(products); db.session.flush()
        db.session.add(ProductImage(product_id=1, url="https://media.example.test/create.webp", position=0))
        variation = Variation(id=41, product_id=4, sku="VARIABLE-1-A", source_identity="Preview Cards/Variable/A", catalogue_status="active", regular_price=21, menu_order=0)
        db.session.add(variation); db.session.flush(); db.session.add_all([VariationAttribute(variation_id=41, name="Size", value="A5"), VariationImage(variation_id=41, url="https://media.example.test/a.webp", position=0)])
        db.session.add(ProductRelationship(source_product_id=1, target_sku="LINK-1", resolved_target_product_id=3, relationship_type="cross_sell", position=0))
        db.session.add(CatalogueOperation(id="connection-health", operation_type="woo_connection_test", status="succeeded", scope=json.dumps({"operation_summary": {"state": "connected", "health_state": "connected", "selected_namespace": "wc/v3"}})))
        db.session.commit()
    try: yield app
    finally:
        with app.app_context(): db.session.remove()
        reset_preview_cache_for_tests(); reset_operation_control_for_tests(); Config.SQLALCHEMY_DATABASE_URI = original


def _client(app):
    client = app.test_client()
    with client.session_transaction() as session: session["_user_id"] = "1"; session["_fresh"] = True
    return client


def _taxonomy():
    return {"categories": [{"id": 11, "name": "Cards", "slug": "cards"}], "tags": [{"id": 12, "name": "Birthday", "slug": "birthday"}], "attributes": []}


def test_access_page_is_authenticated_offline_and_has_no_publish_control(preview_app, monkeypatch):
    assert preview_app.test_client().get("/woocommerce/preview").status_code == 401
    monkeypatch.setattr("app.woo_publish_preview.ReadOnlyWooClient", lambda *a, **k: pytest.fail("Woo contacted by GET page"))
    html = _client(preview_app).get("/woocommerce/preview").get_data(as_text=True)
    assert "Build Publish Preview" in html and "Generate Preview" not in html
    assert ">Publish<" not in html and "Planning only" in html
    assert html.count("<h1") == 1 and 'id="main-content"' in html


def test_scope_estimate_is_local_and_large_scope_requires_confirmation(preview_app):
    with preview_app.app_context():
        estimate = scope_estimate({"kind": "collection", "collection_id": 1})
        assert estimate == {"parent_products": 4, "variations": 1, "images": 2, "relationships": 1, "estimated_woo_reads": 8, "large_scope": False}


def test_payload_builder_maps_intent_prices_taxonomy_images_and_excludes_unsupported(preview_app):
    with preview_app.test_request_context():
        product = db.session.get(Product, 1)
        payload, trace = _product_payload(product, {"categories": [{"slug": "cards", "woo_id": 11}], "tags": [{"slug": "birthday", "woo_id": 12}], "attributes": []})
        assert payload["type"] == "simple" and payload["status"] == "draft"
        assert payload["regular_price"] == "10.00"
        assert payload["categories"] == [{"id": 11}] and payload["tags"] == [{"id": 12}]
        assert payload["images"][0]["src"].endswith("create.webp")
        assert "woo_id" not in payload and "grouped_products" not in payload
        assert trace["publishing_intent"] == "Draft"


def test_variation_payload_preserves_attributes_price_and_owned_image(preview_app):
    with preview_app.app_context():
        payload = _variation_payload(db.session.get(Variation, 41))
        assert payload["sku"] == "VARIABLE-1-A" and payload["regular_price"] == "21.00"
        assert payload["attributes"] == [{"name": "Size", "option": "A5"}]
        assert payload["image"]["src"].endswith("a.webp")


def test_comparison_ignores_woo_generated_fields_and_detects_managed_change():
    payload = {"name": "Card", "sku": "SKU", "status": "draft"}
    remote, differences = _comparison(payload, {**payload, "id": 9, "permalink": "secretless", "date_modified": "tomorrow"})
    assert not differences and "id" not in remote
    assert [row["field"] for row in _comparison(payload, {**payload, "name": "Old"})[1]] == ["name"]


def test_generate_plan_classifies_create_link_and_update_with_get_only(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    fake = FakeWooClient(by_id={202: {"id": 202, "sku": "EXIST-1", "type": "simple", "name": "Remote old"}}, by_sku={"LINK-1": [{"id": 303, "sku": "LINK-1", "type": "simple", "name": "Link Card"}]}, taxonomy=_taxonomy())
    with preview_app.app_context():
        store = store_identity(); db.session.add(WooProductIdentity(product_id=2, stable_identity="Preview Cards/Existing", sku="EXIST-1", store_key=store["key"], store_host=store["host"], woo_product_id=202, sync_state="linked", verification_state="verified")); db.session.commit()
        plan = generate_publish_plan({"kind": "selected", "product_ids": [1, 2, 3]}, client=fake)
        actions = {row["sku"]: row["action"] for row in plan["products"]}
        assert actions == {"CREATE-1": "create", "EXIST-1": "update", "LINK-1": "link_candidate"}
        assert set(fake.methods) == {"GET"} and plan["summary"]["woo_writes"] == 0
        assert plan["summary"]["checklist"]["woo_connection_healthy"] is True
        assert plan["summary"]["capability"]["selected_namespace"] == "wc/v3"
        assert next(row for row in plan["products"] if row["sku"] == "LINK-1")["remote_summary"] == {"id": 303, "name": "Link Card", "sku": "LINK-1", "type": "simple"}
        assert plan["products"][0]["relationships"]["payload"]["pending_cross_sell_target_skus"] == ["LINK-1"]
        operation = db.session.get(CatalogueOperation, plan["operation_id"])
        persisted = json.loads(operation.scope)["operation_summary"]
        assert "products" not in persisted and "payload" not in persisted
        assert "ck_fictional" not in operation.scope and "cs_fictional" not in operation.scope


def test_stored_id_404_is_recovery_required(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    with preview_app.app_context():
        store = store_identity(); db.session.add(WooProductIdentity(product_id=2, stable_identity="Existing", sku="EXIST-1", store_key=store["key"], store_host=store["host"], woo_product_id=999)); db.session.commit()
        plan = generate_publish_plan({"kind": "product", "product_id": 2}, client=FakeWooClient(taxonomy=_taxonomy()))
        assert plan["products"][0]["action"] == "recovery_required"


@pytest.mark.parametrize("matches,state", [
    ([{"id": 1, "sku": "LINK-1", "type": "simple"}, {"id": 2, "sku": "LINK-1", "type": "simple"}], "blocked"),
    ([{"id": 1, "sku": "LINK-1", "type": "variable"}], "blocked"),
])
def test_duplicate_remote_sku_and_type_conflict_block(preview_app, monkeypatch, matches, state):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    with preview_app.app_context():
        plan = generate_publish_plan({"kind": "product", "product_id": 3}, client=FakeWooClient(by_sku={"LINK-1": matches}, taxonomy=_taxonomy()))
        assert plan["products"][0]["action"] == state


def test_store_scoping_prevents_identity_reuse(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    with preview_app.app_context():
        db.session.add(WooProductIdentity(product_id=1, stable_identity="Create", sku="CREATE-1", store_key="other-store", store_host="other.invalid", woo_product_id=44)); db.session.commit()
        plan = generate_publish_plan({"kind": "product", "product_id": 1}, client=FakeWooClient(taxonomy=_taxonomy()))
        assert plan["products"][0]["identity_state"] == "store_mismatch" and plan["products"][0]["action"] == "blocked"


def test_variation_identity_is_store_and_parent_scoped(preview_app):
    with preview_app.app_context():
        store = store_identity(); row = WooVariationIdentity(variation_id=41, product_id=4, stable_identity="Variable/A", sku="VARIABLE-1-A", store_key=store["key"], store_host=store["host"], woo_parent_product_id=404, woo_variation_id=405)
        db.session.add(row); db.session.commit()
        assert row.product_id == 4 and row.woo_parent_product_id == 404


def test_digest_is_deterministic_timing_free_and_stale_after_local_change(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    fake1 = FakeWooClient(taxonomy=_taxonomy()); fake2 = FakeWooClient(taxonomy=_taxonomy())
    with preview_app.app_context():
        first = generate_publish_plan({"kind": "product", "product_id": 1}, client=fake1)
        reset_operation_control_for_tests()
        second = generate_publish_plan({"kind": "product", "product_id": 1}, client=fake2)
        assert first["digest"] == second["digest"] and first["summary"]["builder_version"] == BUILDER_VERSION
        db.session.get(Product, 1).title = "Changed title"; db.session.commit()
        assert plan_is_stale(first) is True


def test_route_generation_and_detail_render_with_only_reviewed_publish_entry(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.ReadOnlyWooClient", lambda *a, **k: FakeWooClient(taxonomy=_taxonomy()))
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    client = _client(preview_app)
    response = client.post("/woocommerce/preview/generate", data={"scope_kind": "product", "product_id": "1"})
    assert response.status_code == 302 and "/woocommerce/preview/operations/" in response.location
    html = client.get(response.location).get_data(as_text=True)
    assert "Pass 1" in html and "Pass 2" in html and "Safety checklist" in html and "Review Final Confirmation" in html
    assert "Publish Selected Products" not in html
    filtered = client.get(f"{response.location}?action=no_change").get_data(as_text=True)
    assert "No products in this state" in filtered
    operation_id = response.location.rsplit("/", 1)[-1]
    detail = client.get(f"/woocommerce/preview/products/1?operation_id={operation_id}")
    assert detail.status_code == 200 and "Sanitised Pass 1 product payload" in detail.get_data(as_text=True)
    api = client.get(f"/api/woocommerce/preview/products/1?operation_id={operation_id}")
    assert api.status_code == 200 and api.get_json()["product"]["sku"] == "CREATE-1"


def test_no_woo_ids_or_payloads_written_to_catalogue(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    with preview_app.app_context():
        catalogue = Path(db.session.query(Settings).first().product_folder)
        before = sorted(path.relative_to(catalogue) for path in catalogue.rglob("*"))
        generate_publish_plan({"kind": "product", "product_id": 1}, client=FakeWooClient(taxonomy=_taxonomy()))
        after = sorted(path.relative_to(catalogue) for path in catalogue.rglob("*"))
        assert before == after == []
        assert not db.session.get(Product, 1).woo_id


def test_readonly_client_rejects_all_write_methods(preview_app):
    reader = PreviewWooReader(FakeWooClient())
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(AssertionError): reader.client.request_json(method, "https://shop.example.test/wp-json/wc/v3/products")


def test_identity_migration_is_minimal_store_scoped_and_reversible(tmp_path):
    database = tmp_path / "identity.db"; config = _alembic_config(f"sqlite:///{database}")
    command.upgrade(config, "0006_relationship_workspace")
    command.upgrade(config, "head")
    import sqlite3
    connection = sqlite3.connect(database)
    tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert {"woo_product_identity", "woo_variation_identity"} <= tables
    assert connection.execute("select version_num from alembic_version").fetchone()[0] == "0007_woo_sync_identity"
    product_columns = {row[1] for row in connection.execute("pragma table_info(woo_product_identity)")}
    assert {"store_key", "store_host", "woo_product_id", "last_published_digest", "last_remote_digest", "verification_state"} <= product_columns
    assert "payload" not in product_columns and "credentials" not in product_columns
    connection.close()
    command.downgrade(config, "0006_relationship_workspace")
    connection = sqlite3.connect(database)
    assert "woo_product_identity" not in {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    connection.close()


def test_global_attribute_and_terms_are_deduplicated_and_planned(preview_app, monkeypatch):
    from app.models import ProductAttribute
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    taxonomy = _taxonomy(); taxonomy["attributes"] = [{"id": 77, "name": "Size", "slug": "size"}]
    fake = FakeWooClient(taxonomy=taxonomy)
    original = fake.request_json
    def request(method, url, **kwargs):
        if "/attributes/77/terms" in url:
            fake.methods.append(method); fake.request_count += 1
            return [{"id": 88, "name": "A5", "slug": "a5"}], object()
        return original(method, url, **kwargs)
    fake.request_json = request
    with preview_app.app_context():
        db.session.add_all([ProductAttribute(product_id=1, name="Size", values='["A5", "A4"]', visible=True, is_global=True, position=0), ProductAttribute(product_id=2, name="Size", values='["A5"]', visible=True, is_global=True, position=0)])
        db.session.commit()
        plan = generate_publish_plan({"kind": "selected", "product_ids": [1, 2]}, client=fake)
        terms = plan["taxonomy"]["terms"]
        assert [(row["name"], row["state"]) for row in terms] == [("A4", "create_required"), ("A5", "existing")]
        assert sum("/attributes/77/terms" in key[0] for key in []) == 0  # one reader request is asserted by total below
        assert fake.request_count <= 7


def test_existing_parent_variations_are_compared_read_only(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    remote_parent = {"id": 404, "sku": "VARIABLE-1", "type": "variable", "name": "Variable Card", "status": "publish"}
    fake = FakeWooClient(by_id={404: remote_parent}, taxonomy=_taxonomy())
    original = fake.request_json
    def request(method, url, **kwargs):
        if "/products/404/variations" in url:
            fake.methods.append(method); fake.request_count += 1
            return [{"id": 405, "sku": "VARIABLE-1-A", "regular_price": "21.00", "attributes": [{"name": "Size", "option": "A5"}]}], object()
        return original(method, url, **kwargs)
    fake.request_json = request
    with preview_app.app_context():
        store = store_identity(); db.session.add_all([WooProductIdentity(product_id=4, stable_identity="Variable", sku="VARIABLE-1", store_key=store["key"], store_host=store["host"], woo_product_id=404), WooVariationIdentity(variation_id=41, product_id=4, stable_identity="Variable/A", sku="VARIABLE-1-A", store_key=store["key"], store_host=store["host"], woo_parent_product_id=404, woo_variation_id=405)]); db.session.commit()
        variation = generate_publish_plan({"kind": "product", "product_id": 4}, client=fake)["products"][0]["variations"][0]
        assert variation["woo_id"] == 405 and variation["action"] in {"no_change", "update"}
        assert isinstance(variation["differences"], list) and variation["remote_managed"]["sku"] == "VARIABLE-1-A"
        assert set(fake.methods) == {"GET"}


def test_large_fixture_has_bounded_queries_requests_and_operation_state(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    with preview_app.app_context():
        collection = db.session.get(Collection, 1)
        products = [Product(collection_id=collection.id, title=f"Scale {index:03d}", sku=f"SCALE-{index:03d}", product_type="variable", catalogue_status="active", published=False, source_relpath=f"Scale/{index:03d}") for index in range(500)]
        db.session.add_all(products); db.session.flush()
        variations = []
        for index, product in enumerate(products):
            for offset in range(10): variations.append(Variation(product_id=product.id, sku=f"SCALE-{index:03d}-{offset:02d}", source_identity=f"Scale/{index:03d}/{offset:02d}", catalogue_status="active", regular_price=10 + offset, menu_order=offset))
            for offset in range(1, 5):
                target = products[(index + offset) % 500]
                db.session.add(ProductRelationship(source_product_id=product.id, target_sku=target.sku, resolved_target_product_id=target.id, relationship_type="cross_sell", position=offset - 1))
        db.session.add_all(variations); db.session.commit()
        ids = [row.id for row in products]
        queries = []
        def count_query(*args): queries.append(args[2])
        event.listen(db.engine, "before_cursor_execute", count_query)
        fake = FakeWooClient(taxonomy={"categories": [], "tags": [], "attributes": []})
        try: plan = generate_publish_plan({"kind": "selected", "product_ids": ids}, confirm_large=True, client=fake)
        finally: event.remove(db.engine, "before_cursor_execute", count_query)
        assert len(plan["products"]) == 500 and sum(len(row["variations"]) for row in plan["products"]) == 5000
        assert sum(len(values) for row in plan["products"] for values in row["relationships"]["groups"].values()) == 2000
        # SQLite's parameter limit causes a small fixed number of select-in
        # batches for this graph, but query growth is not per variation/edge.
        assert len(queries) <= 120 and fake.request_count <= 503
        operation = db.session.get(CatalogueOperation, plan["operation_id"])
        assert len(operation.scope.encode()) <= 4000
        assert len(json.dumps(plan, default=str).encode()) < 12 * 1024 * 1024


def _eligible_create_confirmation(preview_app, monkeypatch, *, publisher=None):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = publisher or FakePublisherClient()
    with preview_app.app_context():
        store = store_identity()
        db.session.add(WooProductIdentity(
            product_id=3, stable_identity="Preview Cards/Link", sku="LINK-1",
            store_key=store["key"], store_host=store["host"], woo_product_id=303,
            sync_state="synced", verification_state="verified",
        ))
        db.session.commit()
        preview = generate_publish_plan({"kind": "product", "product_id": 1}, client=publisher)
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [1], client=publisher)
    return publisher, preview, confirmation


def test_controlled_confirmation_requires_current_explicit_bounded_eligible_preview(preview_app, monkeypatch):
    publisher, preview, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    assert confirmation["product_ids"] == [1]
    assert confirmation["counts"]["create"] == 1 and confirmation["counts"]["blockers"] == 0
    with preview_app.app_context():
        with pytest.raises(ControlledPublishError, match="stale"):
            prepare_publish_confirmation(preview["operation_id"], "wrong", [1], client=publisher)
        with pytest.raises(ControlledPublishError, match="limited"):
            prepare_publish_confirmation(preview["operation_id"], preview["digest"], list(range(1, MAX_PUBLISH_PRODUCTS + 2)), client=publisher)
        WooProductIdentity.query.filter_by(product_id=3).delete()
        db.session.commit()
        publisher.products[303] = {"id": 303, "sku": "LINK-1", "type": "simple", "name": "Link Card", "status": "publish"}
        link_preview = generate_publish_plan({"kind": "product", "product_id": 3}, client=publisher)
        with pytest.raises(ControlledPublishError, match="link candidate"):
            prepare_publish_confirmation(link_preview["operation_id"], link_preview["digest"], [3], client=publisher)


def test_simple_draft_two_pass_publish_verifies_identity_relationships_and_no_json_write(preview_app, monkeypatch):
    publisher, _preview, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    with preview_app.app_context():
        catalogue = Path(db.session.get(Settings, 1).product_folder)
        before = list(catalogue.rglob("*"))
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        operation = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(operation.scope)["operation_summary"]
        identity = WooProductIdentity.query.filter_by(product_id=1, store_key=confirmation["store_identity"]).one()
        assert operation.status == "succeeded" and summary["verified_products"] == 1
        assert identity.woo_product_id == 501 and identity.verification_state == "verified"
        assert publisher.products[501]["status"] == "draft"
        assert publisher.products[501]["cross_sell_ids"] == [303]
        assert all(method in {"GET", "POST", "PUT"} for method in publisher.methods)
        assert not any(method == "DELETE" for method in publisher.methods)
        assert list(catalogue.rglob("*")) == before
        scope = json.loads(operation.scope)
        assert "ck_fictional" not in operation.scope and "cs_fictional" not in operation.scope
        assert "raw_response" not in scope and "payload" not in scope


def test_variable_parent_precedes_variation_and_both_identities_are_verified(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient()
    with preview_app.app_context():
        preview = generate_publish_plan({"kind": "product", "product_id": 4}, client=publisher)
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [4], client=publisher)
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        parent = WooProductIdentity.query.filter_by(product_id=4).one()
        variation = WooVariationIdentity.query.filter_by(variation_id=41).one()
        assert parent.woo_product_id == 501 and variation.woo_parent_product_id == 501
        assert variation.woo_variation_id == 601 and variation.verification_state == "verified"
        write_paths = [path for method, path, _ in publisher.writes if method in {"POST", "PUT"}]
        assert write_paths.index("/wp-json/wc/v3/products") < write_paths.index("/wp-json/wc/v3/products/501/variations")
        assert db.session.get(CatalogueOperation, operation_id).status == "succeeded"


def test_uncertain_create_reconciles_exact_sku_without_duplicate(preview_app, monkeypatch):
    publisher, _preview, confirmation = _eligible_create_confirmation(
        preview_app, monkeypatch, publisher=FakePublisherClient(uncertain_product_create=True)
    )
    with preview_app.app_context():
        start_publish_operation(confirmation, client=publisher, run_async=False)
        assert len(publisher.products) == 1
        assert sum(method == "POST" and path.endswith("/products") for method, path, _ in publisher.writes) == 1
        assert WooProductIdentity.query.filter_by(product_id=1).one().woo_product_id == 501


def test_pending_relationship_does_not_erase_verified_pass_one(preview_app, monkeypatch):
    publisher, _preview, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    confirmation["products"][0]["relationships"]["groups"]["cross_sell"][0].update({"woo_id": None, "sku": "MISSING-TARGET"})
    with preview_app.app_context():
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        operation = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(operation.scope)["operation_summary"]
        assert operation.status == "partial" and summary["verified_products"] == 1
        assert summary["pending_relationship_count"] == 1
        assert WooProductIdentity.query.filter_by(product_id=1).one().verification_state == "verified"


def test_publisher_policy_forbids_delete_and_readonly_client_forbids_write():
    configuration = WooConfiguration("https://shop.example.test", "ck_test", "cs_test")
    with pytest.raises(WooConnectionError, match="DELETE"):
        PublisherWooClient(configuration).request_json("DELETE", "https://shop.example.test/wp-json/wc/v3/products/1", authenticated=True)
    with pytest.raises(ControlledPublishError, match="publisher-only"):
        PublishGateway(FakeWooClient(), "wc/v3")


def test_publish_routes_require_authentication_csrf_and_acknowledgement(preview_app, monkeypatch):
    publisher, preview, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    unauthenticated = preview_app.test_client().post("/woocommerce/publish/confirm")
    assert unauthenticated.status_code == 401
    monkeypatch.setattr("app.routes.prepare_publish_confirmation", lambda *a, **k: confirmation)
    monkeypatch.setattr("app.routes.start_publish_operation", lambda *a, **k: "fictional-publish-operation")
    client = _client(preview_app)
    page = client.post("/woocommerce/publish/confirm", data={"preview_operation_id": preview["operation_id"], "preview_digest": preview["digest"], "product_ids": "1"})
    assert page.status_code == 200 and "Explicit acknowledgements" in page.get_data(as_text=True)
    refused = client.post("/woocommerce/publish/start", data={"preview_operation_id": preview["operation_id"], "preview_digest": preview["digest"], "product_ids": "1"})
    assert refused.status_code == 302
    accepted = client.post("/woocommerce/publish/start", data={"preview_operation_id": preview["operation_id"], "preview_digest": preview["digest"], "product_ids": "1", "acknowledge_write": "yes"})
    assert accepted.status_code == 302 and accepted.location.endswith("/operations/fictional-publish-operation")
    assert client.get("/woocommerce/publish/confirm").status_code == 405

    preview_app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/woocommerce/publish/start", data={"acknowledge_write": "yes"}).status_code == 400
    preview_app.config["WTF_CSRF_ENABLED"] = False


def test_update_and_no_change_use_verified_id_and_skip_unchanged_write(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient()
    with preview_app.app_context():
        store = store_identity()
        db.session.add(WooProductIdentity(product_id=2, stable_identity="Preview Cards/Existing", sku="EXIST-1", store_key=store["key"], store_host=store["host"], woo_product_id=202, sync_state="synced", verification_state="verified"))
        db.session.commit()
        publisher.products[202] = {"id": 202, "sku": "EXIST-1", "type": "simple", "name": "Old title"}
        preview = generate_publish_plan({"kind": "product", "product_id": 2}, client=publisher)
        assert preview["products"][0]["action"] == "update"
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [2], client=publisher)
        start_publish_operation(confirmation, client=publisher, run_async=False)
        assert publisher.products[202]["name"] == "Existing Card"
        update_writes = len(publisher.writes)

        reset_operation_control_for_tests()
        preview = generate_publish_plan({"kind": "product", "product_id": 2}, client=publisher)
        assert preview["products"][0]["action"] == "no_change"
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [2], client=publisher)
        start_publish_operation(confirmation, client=publisher, run_async=False)
        assert len(publisher.writes) == update_writes


def test_taxonomy_and_global_terms_are_created_once_and_reused(preview_app, monkeypatch):
    from app.models import ProductAttribute
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient(taxonomy={"categories": [], "tags": [], "attributes": []})
    with preview_app.app_context():
        ProductRelationship.query.filter_by(source_product_id=1).delete()
        db.session.add(ProductAttribute(product_id=2, name="Size", values='["A5"]', visible=True, is_global=True, position=0))
        db.session.commit()
        preview = generate_publish_plan({"kind": "selected", "product_ids": [1, 2]}, client=publisher)
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [1, 2], client=publisher)
        start_publish_operation(confirmation, client=publisher, run_async=False)
        created_routes = [path for method, path, _ in publisher.writes if method == "POST"]
        assert created_routes.count("/wp-json/wc/v3/products/categories") == 1
        assert created_routes.count("/wp-json/wc/v3/products/tags") == 1
        assert created_routes.count("/wp-json/wc/v3/products/attributes") == 1
        assert sum(path.endswith("/terms") for path in created_routes) == 1
        assert len(publisher.products) == 2


def test_taxonomy_failure_blocks_only_affected_product(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient(taxonomy={"categories": [], "tags": _taxonomy()["tags"], "attributes": []})
    with preview_app.app_context():
        unaffected = db.session.get(Product, 2)
        unaffected.categories.clear()
        store = store_identity()
        db.session.add(WooProductIdentity(
            product_id=3, stable_identity="Preview Cards/Link", sku="LINK-1",
            store_key=store["key"], store_host=store["host"], woo_product_id=303,
            sync_state="synced", verification_state="verified",
        ))
        db.session.commit()
        preview = generate_publish_plan({"kind": "selected", "product_ids": [1, 2]}, client=publisher)
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [1, 2], client=publisher)
        publisher.taxonomy["categories"] = [
            {"id": 71, "name": "Cards", "slug": "cards"},
            {"id": 72, "name": "Cards duplicate", "slug": "cards"},
        ]
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        summary = json.loads(db.session.get(CatalogueOperation, operation_id).scope)["operation_summary"]
        results = {row["product_id"]: row for row in summary["product_results"]}
        assert results[1]["status"] == "failed"
        assert results[2]["status"] == "verified"
        assert summary["taxonomy"]["failed"] == 1


def test_same_batch_relationship_resolves_new_ids_in_authored_order(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient()
    with preview_app.app_context():
        preview = generate_publish_plan({"kind": "selected", "product_ids": [1, 3]}, client=publisher)
        assert preview["products"][0]["relationships"]["pending_count"] == 1
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [1, 3], client=publisher)
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        identities = {row.product_id: row.woo_product_id for row in WooProductIdentity.query.all()}
        assert publisher.products[identities[1]]["cross_sell_ids"] == [identities[3]]
        summary = json.loads(db.session.get(CatalogueOperation, operation_id).scope)["operation_summary"]
        assert summary["pending_relationship_count"] == 0 and summary["verified_products"] == 2


def test_operation_detail_renders_bounded_publish_summary_and_safe_resume(preview_app, monkeypatch):
    publisher, _preview, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    with preview_app.app_context():
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
    html = _client(preview_app).get(f"/operations/{operation_id}").get_data(as_text=True)
    assert "Controlled two-pass publishing" in html
    assert "Verified publication summary" in html
    assert "Woo ID 501" in html
    assert "Consumer" not in html and "ck_fictional" not in html and "cs_fictional" not in html
    product_html = _client(preview_app).get("/products/1").get_data(as_text=True)
    assert "Woo ID verified" in product_html and ">501<" in product_html


def test_local_change_prevents_resume_and_concurrent_operation_blocks_start(preview_app, monkeypatch):
    from app.utils.operation_control import acquire_catalogue_operation, CatalogueOperationActive
    publisher, _preview, confirmation = _eligible_create_confirmation(preview_app, monkeypatch)
    with preview_app.app_context():
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        db.session.get(Product, 1).title = "Changed after reviewed plan"
        db.session.commit()
        with pytest.raises(ControlledPublishError, match="stale"):
            resume_confirmation(operation_id, client=publisher)
        reset_operation_control_for_tests()
        lease = acquire_catalogue_operation("woo_connection_test", {"test": True})
        with pytest.raises(CatalogueOperationActive):
            start_publish_operation(confirmation, client=publisher, run_async=False)
        from app.utils.operation_control import finish_catalogue_operation
        finish_catalogue_operation(lease.id, status="succeeded")


def test_safe_resume_reconciles_verified_parent_without_duplicate_create(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient()
    with preview_app.app_context():
        preview = generate_publish_plan({"kind": "product", "product_id": 1}, client=publisher)
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], [1], client=publisher)
        first_operation = start_publish_operation(confirmation, client=publisher, run_async=False)
        assert db.session.get(CatalogueOperation, first_operation).status == "partial"
        assert sum(method == "POST" and path.endswith("/products") for method, path, _ in publisher.writes) == 1

        retry = resume_confirmation(first_operation, client=publisher)
        assert retry["products"][0]["action"] == "no_change"
        second_operation = start_publish_operation(retry, client=publisher, run_async=False)
        assert db.session.get(CatalogueOperation, second_operation).status == "partial"
        assert sum(method == "POST" and path.endswith("/products") for method, path, _ in publisher.writes) == 1


def test_ten_parent_publish_has_bounded_requests_queries_and_operation_state(preview_app, monkeypatch):
    monkeypatch.setattr("app.woo_publish_preview.notify_woo_publish_preview_completed", lambda *a, **k: None)
    monkeypatch.setattr("app.woo_controlled_publish.notify_woo_publish_completed", lambda *a, **k: (True, "sent"))
    publisher = FakePublisherClient(taxonomy={"categories": [], "tags": [], "attributes": []})
    with preview_app.app_context():
        ProductRelationship.query.delete()
        collection = db.session.get(Collection, 1)
        products = [
            Product(collection_id=collection.id, title=f"Controlled Batch {index}", sku=f"CONTROLLED-{index}", product_type="simple", catalogue_status="active", published=False, source_relpath=f"Controlled/{index}")
            for index in range(10)
        ]
        db.session.add_all(products); db.session.commit()
        ids = [product.id for product in products]
        preview = generate_publish_plan({"kind": "selected", "product_ids": ids}, client=publisher)
        confirmation = prepare_publish_confirmation(preview["operation_id"], preview["digest"], ids, client=publisher)
        queries = []
        def count_query(*args): queries.append(args[2])
        event.listen(db.engine, "before_cursor_execute", count_query)
        try:
            operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        finally:
            event.remove(db.engine, "before_cursor_execute", count_query)
        operation = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(operation.scope)["operation_summary"]
        assert summary["verified_products"] == 10
        assert publisher.request_count <= 100 and len(queries) <= 300
        assert len(operation.scope.encode()) < 64 * 1024


@pytest.mark.parametrize("status,expected", [
    ("pending", "Controlled publishing is queued"),
    ("running", "Controlled publishing is in progress"),
    ("failed", "Publishing stopped before any Woo write"),
    ("partial", "Publishing completed with attention"),
    ("interrupted", "Controlled publishing interrupted"),
])
def test_publish_operation_detail_has_safe_defaults_before_terminal_summary(preview_app, status, expected):
    with preview_app.app_context():
        operation = CatalogueOperation(
            id=f"publish-{status}", operation_type="woo_controlled_publish", status=status,
            scope=json.dumps({"store_host": "shop.example.test"}), marker_state="not_applicable",
            recovery_state="none",
        )
        db.session.add(operation); db.session.commit()
    response = _client(preview_app).get(f"/operations/publish-{status}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and expected in html
    assert "Verified variations</dt><dd>0" in html
    assert "Taxonomy created / reused</dt><dd>0 / 0" in html


def test_publish_result_fragment_is_authenticated_and_running_safe(preview_app):
    with preview_app.app_context():
        db.session.add(CatalogueOperation(
            id="publish-fragment", operation_type="woo_controlled_publish", status="running",
            scope="{}", marker_state="not_applicable", recovery_state="none",
        )); db.session.commit()
    assert preview_app.test_client().get("/operations/publish-fragment/woo-publish-result").status_code == 401
    response = _client(preview_app).get("/operations/publish-fragment/woo-publish-result")
    assert response.status_code == 200 and response.headers["Cache-Control"].startswith("no-store")
    assert "Controlled publishing in progress" in response.get_data(as_text=True)


def test_publish_http_400_persists_structured_non_uncertain_diagnostic(preview_app, monkeypatch):
    class RejectingPublisher(FakePublisherClient):
        def request_json(self, method, url, **kwargs):
            if method.upper() == "POST" and urlsplit(url).path.endswith("/products"):
                self.request_count += 1
                raise WooConnectionError(
                    "bad_request", "Invalid parameter(s): regular_price", status_code=400,
                    remote_error={
                        "code": "woocommerce_rest_invalid_product",
                        "message": "Invalid parameter(s): regular_price",
                        "data": {"status": 400, "params": {"regular_price": "Must be numeric"}},
                    },
                )
            return super().request_json(method, url, **kwargs)

    publisher, _preview, confirmation = _eligible_create_confirmation(
        preview_app, monkeypatch, publisher=RejectingPublisher()
    )
    with preview_app.app_context():
        operation_id = start_publish_operation(confirmation, client=publisher, run_async=False)
        operation = db.session.get(CatalogueOperation, operation_id)
        summary = json.loads(operation.scope)["operation_summary"]
        diagnostic = summary["woo_errors"][0]
        item = CatalogueOperationItem.query.filter_by(operation_id=operation_id).one()
        assert operation.status == "failed" and not summary["recovery_required"]
        assert diagnostic["http_status"] == 400
        assert diagnostic["remote_code"] == "woocommerce_rest_invalid_product"
        assert diagnostic["fields"] == {"regular_price": "Must be numeric"}
        assert diagnostic["retry_state"] == "payload_correction_required"
        assert diagnostic["remote_verified"] is False and diagnostic["uncertain"] is False
        assert item.database_state == "remote_publish_failed"
        assert summary["write_request_count"] == 1
        assert "raw_response" not in operation.scope and "consumer_secret" not in operation.scope
    html = _client(preview_app).get(f"/operations/{operation_id}").get_data(as_text=True)
    assert "woocommerce_rest_invalid_product" in html
    assert "regular_price" in html and "Payload Correction Required" in html
    assert "Database failed" not in html


def test_publish_discord_includes_one_bounded_safe_diagnostic(monkeypatch):
    from app.utils import discord
    sent = []
    monkeypatch.setattr(discord, "send_discord_message", lambda **payload: sent.append(payload) or (True, "sent"))
    summary = {
        "selected_products": 1, "created": 0, "updated": 0, "verified_products": 0,
        "failed_products": 1, "duration_ms": 12, "counts": {}, "taxonomy": {},
        "woo_errors": [{
            "object_label": "Parent product", "sku": "SAFE-1", "http_status": 400,
            "category": "bad_request", "message": "Invalid parameter(s): regular_price",
        }],
    }
    assert discord.notify_woo_publish_completed(summary, operation_id="fictional-operation") == (True, "sent")
    rendered = json.dumps(sent)
    assert len(sent) == 1 and "Parent product" in rendered and "HTTP 400" in rendered
    assert "regular_price" in rendered and "consumer_secret" not in rendered


@pytest.mark.parametrize("status,category,retry_state,uncertain", [
    (400, "bad_request", "payload_correction_required", False),
    (401, "authentication_rejected", "configuration_review_required", False),
    (403, "forbidden", "configuration_review_required", False),
    (429, "rate_limited", "retry_later", False),
    (503, "server_error", "reconciliation_required", True),
])
def test_publish_write_failures_have_stage_aware_retry_classification(status, category, retry_state, uncertain):
    class RejectingClient:
        publisher_policy = True
        base_url = "https://shop.example.test"
        request_count = 0
        def request_json(self, *args, **kwargs):
            raise WooConnectionError(category, "Controlled remote error", status_code=status)

    gateway = PublishGateway(RejectingClient(), "wc/v3")
    gateway.set_stage("publishing_variations")
    with pytest.raises(WooConnectionError) as caught:
        gateway.write("POST", "products/12/variations", {"sku": "SAFE-VAR-1"})
    diagnostic = caught.value.publish_diagnostic
    assert diagnostic["stage"] == "publishing_variations"
    assert diagnostic["object_type"] == "variation" and diagnostic["sku"] == "SAFE-VAR-1"
    assert diagnostic["retry_state"] == retry_state and diagnostic["uncertain"] is uncertain
    assert gateway.write_count == 1
