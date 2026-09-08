"""Reviewed definition sync, isolated registry/SQLite and existing fake Woo transport."""
import copy
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app import db, taxonomy_workspace as registry, woo_taxonomy_sync as sync
from app.models import WooTaxonomyIdentity
from app.woocommerce_connection import WooConfiguration, WooConnectionError
from app.taxonomy_assignments import resolve, options
from app.utils.json_utils import merge_product_json
from app.routes import _prune
from test_taxonomy_workspace import registry_app, entry, field
from test_phase3_woo_publish_preview import FakePublisherClient
from test_phase2_milestone5 import milestone5_app, milestone5_client


class DefinitionsWoo(FakePublisherClient):
    """Extend the existing publisher fake only for Brands/index and detached readbacks."""
    def __init__(self):
        super().__init__(taxonomy={"categories": [], "attributes": [], "brands": []})
        self.configuration = WooConfiguration(self.base_url, "fictional-key", "fictional-secret")
        self.brands = True
        self.incomplete = False
        self.uncertain = False
        self.wrong_readback = False

    def _taxonomy_kind(self, path):
        return "brands" if "/products/brands" in path else super()._taxonomy_kind(path)

    def request_json(self, method, url, **kwargs):
        path = urlsplit(url).path
        assert method in {"GET", "POST", "PUT"}
        assert "/products/" in path or path.endswith("/wc/v3/")
        if path.endswith("/wc/v3/"):
            self.methods.append(method)
            return {"routes": {"/wc/v3/products/brands": {"methods": ["GET", "POST"]}} if self.brands else {}}, object()
        if method == "PUT":
            kind = (f"terms:{path.split('/attributes/', 1)[1].split('/terms', 1)[0]}" if '/terms/' in path else self._taxonomy_kind(path))
            target = next(r for r in self.taxonomy[kind] if r['id'] == int(path.rsplit('/', 1)[1]))
            target.update(kwargs['json_body'])
            self.methods.append(method)
            self.writes.append((method, path, copy.deepcopy(kwargs['json_body'])))
            return copy.deepcopy(target), object()
        try:
            value, response = super().request_json(method, url, **kwargs)
        except StopIteration:
            raise WooConnectionError("not_found", "fictional missing", status_code=404) from None
        value = copy.deepcopy(value)
        for item in value if isinstance(value, list) else [value]:
            if '/terms' in path:
                item.setdefault('menu_order', 0)
            elif '/attributes' in path:
                item.setdefault('order_by', 'menu_order')
        if method == "POST" and self.uncertain:
            raise WooConnectionError("read_timeout", "Response lost after create")
        if method == "GET" and path.rsplit("/", 1)[-1].isdigit() and self.wrong_readback:
            value["slug"] = "wrong"
        if self.incomplete and path.endswith("/categories"):
            response = SimpleNamespace(headers={"X-WP-Total": "200"})
        return value, response


@pytest.fixture
def world(registry_app, monkeypatch):
    app, client, root, data = registry_app
    fake = DefinitionsWoo()
    monkeypatch.setattr(sync, "make_client", lambda write=False: fake)
    return app, client, root, data, fake


def finish(client, ids):
    page = client.get("/taxonomy/sync")
    preview = client.post("/taxonomy/sync/preview", data={"csrf_token": field(page, "csrf_token")})
    assert preview.status_code == 200, preview.get_data(as_text=True)
    review = client.post("/taxonomy/sync/review", data={"csrf_token": field(preview, "csrf_token"), "review": field(preview, "review"), "selected": ids})
    assert review.status_code == 200, review.get_data(as_text=True)
    result = client.post("/taxonomy/sync/confirm", data={"csrf_token": field(review, "csrf_token"), "review": field(review, "review"), "acknowledge": "yes"})
    assert result.status_code == 200, result.get_data(as_text=True)
    assert 'Woo taxonomy sync result' in result.text
    return review


def test_create_dependency_rounds_verified_readback_and_no_products(world):
    app, client, root, data, fake = world
    before = (root / "registry.json").read_bytes()
    with app.app_context():
        first = sync.plan(fake)
        child = next(r for r in first["rows"] if r["id"] == "categories::notelets")
        assert child["action"] is None
        assert any(r["kind"] == "terms:finish" for r in first["unavailable"])
    finish(client, ["categories::cards", "attributes::finish"])
    finish(client, ["categories::notelets", "terms:finish:matte"])
    with app.app_context():
        identities = WooTaxonomyIdentity.query.all()
        assert len(identities) == 4 and all(i.state == "verified" and i.verified_at for i in identities)
        final = sync.plan(fake)
        assert all(r["state"] == "verified" for r in final["rows"])
    assert [path for _, path, _ in fake.writes] == ["/wp-json/wc/v3/products/categories", "/wp-json/wc/v3/products/attributes", "/wp-json/wc/v3/products/categories", "/wp-json/wc/v3/products/attributes/11/terms"]
    assert fake.writes[2][2]["parent"] == 11
    assert (root / "registry.json").read_bytes() == before


def test_safe_match_pa_global_and_scoped_term(world):
    app, client, root, data, fake = world
    fake.taxonomy["attributes"] = [{"id": 41, "name": "Finish", "slug": "pa_finish"}]
    fake.taxonomy["terms:41"] = [{"id": 61, "name": "Matte", "slug": "matte"}]
    fake.taxonomy["terms:99"] = [{"id": 62, "name": "Matte", "slug": "matte"}]
    finish(client, ["attributes::finish"])
    finish(client, ["terms:finish:matte"])
    with app.app_context():
        term = WooTaxonomyIdentity.query.filter_by(kind="terms").one()
        assert term.remote_scope == 41 and term.woo_id == 61
    assert not fake.writes


def test_reviewed_brand_create_and_woo_only_hierarchical_import(world):
    app, client, root, data, fake = world
    data["storefront_collections"] = [entry("paper-garden")]
    (root / "registry.json").write_bytes(registry.validate(data))
    finish(client, ["storefront_collections::paper-garden"])
    assert fake.writes[-1][1].endswith("/products/brands")
    fake.taxonomy["categories"] = [{"id": 31, "name": "Paper", "slug": "paper", "parent": 0}, {"id": 32, "name": "Sheets", "slug": "sheets", "parent": 31}]
    fake.taxonomy["attributes"] = [{"id": 51, "name": "Colour", "slug": "pa_colour"}]
    fake.taxonomy["terms:51"] = [{"id": 71, "name": "Blue", "slug": "blue"}]
    fake.taxonomy["brands"].append({"id": 81, "name": "Sea Birds", "slug": "sea-birds"})
    with app.app_context():
        assert next(r for r in sync.plan(fake)["rows"] if r["id"] == "remote:categories::32")["action"] is None
    finish(client, ["remote:categories::31", "remote:attributes::51", "remote:storefront_collections::81"])
    finish(client, ["remote:categories::32", "remote:terms:attr-colour:71"])
    saved = json.loads((root / "registry.json").read_text())
    assert next(r for r in saved["categories"] if r["key"] == "cat-sheets")["parent"] == "cat-paper"
    assert next(r for r in saved["attributes"] if r["key"] == "attr-colour")["terms"][0]["name"] == "Blue"
    assert len(list(root.glob(".registry-backup-*.json"))) == 2
    assert not any("woo" in key.lower() for kind in registry.registry.KINDS for row in saved[kind] for key in row)
    assert len(fake.writes) == 1  # only the explicitly created Brand


@pytest.mark.parametrize("change", ["duplicate", "conflict", "missing", "local", "store"])
def test_conflict_stale_and_store_isolation(world, change):
    app, client, root, data, fake = world
    finish(client, ["categories::cards"])
    if change == "duplicate":
        fake.taxonomy["categories"].append({"id": 21, "name": "Cards", "slug": "cards", "parent": 0})
    elif change == "conflict":
        fake.taxonomy["categories"][0]["slug"] = "wrong"
    elif change == "missing":
        fake.taxonomy["categories"] = []
    elif change == "local":
        data["categories"][0]["slug"] = "new-cards"
        (root / "registry.json").write_bytes(registry.validate(data))
    else:
        fake.base_url = "https://another.example.test"
        fake.configuration = WooConfiguration(fake.base_url, "key", "secret")
    with app.app_context():
        item = next(r for r in sync.plan(fake)["rows"] if r["id"] == "categories::cards")
        assert item["state"] == ("safe_match" if change == "store" else "stale")
        assert WooTaxonomyIdentity.query.count() == 1
        if change != "store":
            assert item["action"] is None


def test_incomplete_discovery_and_brands_unavailable_do_not_establish_absence(world):
    app, client, root, data, fake = world
    fake.incomplete = True
    fake.brands = False
    with app.app_context():
        preview = sync.plan(fake)
        assert {r["kind"] for r in preview["unavailable"]} >= {"categories", "storefront_collections"}
        assert not any(r["kind"] in {"categories", "storefront_collections"} and r.get("action") for r in preview["rows"])
        assert next(r for r in preview["rows"] if r["kind"] == "attributes")["action"] == "create"
    assert not fake.writes


@pytest.mark.parametrize("fault", ["lost_response", "wrong_readback"])
def test_uncertain_create_never_repeats_blindly(world, fault):
    app, client, root, data, fake = world
    with app.app_context():
        preview = sync.plan(fake)
        fake.uncertain = fault == "lost_response"
        fake.wrong_readback = fault == "wrong_readback"
        with pytest.raises(sync.SyncError):
            sync.execute(preview["digest"], ["categories::cards"], fake)
        identity = WooTaxonomyIdentity.query.one()
        assert identity.state == "uncertain" and identity.woo_id is None
        fake.uncertain = fake.wrong_readback = False
        fresh = sync.plan(fake)
        item = next(r for r in fresh["rows"] if r["id"] == "categories::cards")
        assert item["action"] == "link"
    finish(client, ["categories::cards"])
    assert len(fake.writes) == 1


def test_route_auth_csrf_ack_freshness_and_no_page_load_reads(world):
    app, client, root, data, fake = world
    assert app.test_client().get("/taxonomy/sync").status_code in {302, 401}
    page = client.get("/taxonomy/sync")
    assert not fake.methods
    assert client.post("/taxonomy/sync/preview").status_code == 400
    preview = client.post("/taxonomy/sync/preview", data={"csrf_token": field(page, "csrf_token")})
    review = client.post("/taxonomy/sync/review", data={"csrf_token": field(preview, "csrf_token"), "review": field(preview, "review"), "selected": ["categories::cards"]})
    values = {"csrf_token": field(review, "csrf_token"), "review": field(review, "review")}
    assert client.post("/taxonomy/sync/confirm", data=values).status_code == 409
    data["tags"].append(entry("changed"))
    (root / "registry.json").write_bytes(registry.validate(data))
    assert client.post("/taxonomy/sync/confirm", data={**values, "acknowledge": "yes"}).status_code == 409
    assert not fake.writes


def test_local_ranges_resolution_replacement_and_offline_workspace(world, monkeypatch):
    app, client, root, data, fake = world
    data["storefront_collections"] = [entry("paper-garden")]
    (root / "registry.json").write_bytes(registry.validate(data))
    shared = {"storefront_collections": ["Paper-Garden", "Legacy range"]}
    assert merge_product_json(shared, {})["storefront_collections"] == shared["storefront_collections"]
    assert merge_product_json(shared, {"storefront_collections": []})["storefront_collections"] == []
    assert _prune({"storefront_collections": []}) == {"storefront_collections": []}
    with app.app_context():
        resolved = resolve(shared)
        assert resolved["storefront_collections"][0]["definition"]
        assert resolved["storefront_collections"][1]["definition"] is None
    monkeypatch.setattr(sync, "make_client", lambda *a, **k: (_ for _ in ()).throw(sync.SyncError("Woo offline")))
    assert client.get("/taxonomy").status_code == 200
    assert client.get("/taxonomy/options").json["storefront_collections"][0]["value"] == "Paper-Garden"
    assert client.get("/taxonomy/advanced").status_code == 200
    assert not fake.methods


def test_range_actual_sparse_metadata_save(milestone5_app, milestone5_client, monkeypatch):
    from app.utils.operation_control import finish_catalogue_operation
    app, _, _, ids, shared, override = milestone5_app
    before = shared.read_bytes()
    monkeypatch.setattr("app.routes.start_scan", lambda *a, **k: finish_catalogue_operation(k["operation_id"], status="succeeded"))
    page = milestone5_client.get(f"/edit_products/{ids['variable']}/edit/override")
    assert b"Assign Storefront Collection" in page.data
    for ranges in [["Legacy range"], []]:
        result = milestone5_client.post("/edit_products/PRINT-001/save", json={"kind": "override", "replace": True, "data": {"storefront_collections": ranges}})
        assert result.status_code == 200, result.json
        assert json.loads(override.read_text()) == {"storefront_collections": ranges}
    assert shared.read_bytes() == before


def test_no_delete_or_product_endpoint_and_guard_unchanged(world):
    app, client, root, data, fake = world
    api = sync.API(fake)
    for method, route in [("DELETE", "products/categories/1"), ("PUT", "products/brands/1"), ("POST", "products"), ("POST", "products/1/variations")]:
        with pytest.raises(sync.SyncError):
            api.request(method, route, body={"name": "Forbidden"})
    assert not fake.methods


def test_ambiguous_untrusted_match_and_bounded_selection(world):
    app, client, root, data, fake = world
    fake.taxonomy["categories"] = [{"id": 11, "name": "Cards", "slug": "cards", "parent": 0}, {"id": 12, "name": "Cards", "slug": "other", "parent": 0}]
    with app.app_context():
        preview = sync.plan(fake)
        row = next(r for r in preview["rows"] if r["id"] == "categories::cards")
        assert row["state"] == "conflict" and row["action"] is None
        for ids in [[], ["categories::cards"], ["attributes::finish"] * 21]:
            with pytest.raises(sync.SyncError):
                sync.selected(preview, ids)
    assert not fake.writes


def test_import_failure_preserves_registry_and_does_not_trust_identity(world, monkeypatch):
    app, client, root, data, fake = world
    fake.taxonomy["brands"] = [{"id": 51, "name": "Sky Birds", "slug": "sky-birds"}]
    before = (root / "registry.json").read_bytes()
    monkeypatch.setattr(registry, "save_reviewed", lambda *a, **k: (_ for _ in ()).throw(registry.RegistryEditError("Read-only test storage")))
    with app.app_context():
        preview = sync.plan(fake)
        with pytest.raises(sync.SyncError, match="Read-only"):
            sync.execute(preview["digest"], ["remote:storefront_collections::51"], fake)
        assert WooTaxonomyIdentity.query.count() == 0
    assert (root / "registry.json").read_bytes() == before
    assert not fake.writes


def test_migration_and_unique_current_store_mapping(world):
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import IntegrityError
    app, client, root, data, fake = world
    finish(client, ["attributes::finish"])
    with app.app_context():
        assert db.session.execute(text("select version_num from alembic_version")).scalar() == "0008_woo_taxonomy_identity"
        constraints = inspect(db.engine).get_unique_constraints("woo_taxonomy_identity")
        assert {c["name"] for c in constraints} == {"uq_taxonomy_local", "uq_taxonomy_remote"}
        old = WooTaxonomyIdentity.query.one()
        db.session.add(WooTaxonomyIdentity(store_key=old.store_key, kind=old.kind, scope_key="", local_key="different", remote_scope=0, woo_id=old.woo_id, state="verified", local_digest=old.local_digest))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
