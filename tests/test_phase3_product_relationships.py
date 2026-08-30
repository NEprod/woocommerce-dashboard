import json
import sqlite3

import pytest
from alembic import command

from app import create_app, db
from app.database import _alembic_config
from app.models import CatalogueOperation, Collection, Product, ProductRelationship, Settings, User
from app.product_relationships import (
    RelationshipValidationError,
    apply_mutual_cross_sells,
    apply_update,
    preview_mutual_cross_sells,
    preview_update,
    rebuild_relationship_projection,
    recover_relationship_transactions,
    resolve_relationship_targets,
    relationship_source,
    relationship_workspace,
    search_products,
)
from app.utils.operation_control import reset_operation_control_for_tests
from config import Config


PRODUCTS = [
    (101, "AKITA-CARD", "Akita Inu Birthday Card", "active", True, "Complete card"),
    (102, "AKITA-PRINT", "Akita Inu Print", "active", False, "Complete print"),
    (103, "AKITA-LIGHT", "Akita Inu Neon Light", "archived", True, "Complete light"),
    (104, "AKITA-MUG", "Akita Inu Mug", "active", True, None),
    (105, "AKITA-MISSING", "Akita Inu Missing", "missing", True, "Unavailable"),
    (106, "PREMIUM", "Premium Companion", "active", True, "Premium"),
]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


@pytest.fixture
def relationship_app(tmp_path, monkeypatch):
    instance, catalogue, output = tmp_path / "instance", tmp_path / "catalogue", tmp_path / "output"
    for path in (instance, catalogue, output):
        path.mkdir()
    collection_dir = catalogue / "Fictional Akita Collection"
    _write(collection_dir / "product_info.json", {"collection_type": "Variable Collection", "sku_prefix": "AKITA", "tags": ["shared"], "crosssells": ["PREMIUM"]})
    for _, sku, *_ in PRODUCTS:
        _write(collection_dir / sku / "product_info.json", {"short_description": f"Sparse {sku}"})
    single_dir = catalogue / "Single Variable Fixture"
    _write(single_dir / "product_info.json", {"collection_type": "Single Variable", "sku_prefix": "SINGLE", "title": "Single", "relationships": {"cross_sells": ["AKITA-CARD"], "upsells": []}})

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.session.add_all([User(email="relationships@example.test", username="relationship-admin", password="unused"), Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://uploads.invalid/")])
        collection = Collection(name="Fictional Akita Collection", root_path=str(collection_dir), shared_json_path=str(collection_dir / "product_info.json"), source_relpath="Fictional Akita Collection", shared_json_relpath="Fictional Akita Collection/product_info.json", sku_prefix="AKITA", collection_type="Variable Collection")
        single = Collection(name="Single Variable Fixture", root_path=str(single_dir), shared_json_path=str(single_dir / "product_info.json"), source_relpath="Single Variable Fixture", shared_json_relpath="Single Variable Fixture/product_info.json", sku_prefix="SINGLE", collection_type="Single Variable")
        db.session.add_all([collection, single]); db.session.flush()
        for identity, sku, title, status, published, description in PRODUCTS:
            rel = f"Fictional Akita Collection/{sku}"
            db.session.add(Product(id=identity, collection_id=collection.id, sku=sku, title=title, product_type="simple", catalogue_status=status, published=published, description=description, source_relpath=rel, product_dir=str(catalogue / rel), shared_json_path=str(collection_dir / "product_info.json"), override_json_path=str(catalogue / rel / "product_info.json"), shared_json_relpath="Fictional Akita Collection/product_info.json", override_json_relpath=f"{rel}/product_info.json"))
        db.session.add(Product(id=107, collection_id=single.id, sku="SINGLE-001", title="Single Variable", product_type="variable", catalogue_status="active", published=True, description="Single", source_relpath="Single Variable Fixture", product_dir=str(single_dir), shared_json_path=str(single_dir / "product_info.json"), shared_json_relpath="Single Variable Fixture/product_info.json"))
        db.session.commit()
        rebuild_relationship_projection()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"; session["_fresh"] = True
    return client


def test_migration_is_sku_projection_and_preserves_product(tmp_path):
    database = tmp_path / "site.db"; config = _alembic_config(f"sqlite:///{database}")
    command.upgrade(config, "0004_lifecycle")
    connection = sqlite3.connect(database); connection.execute("INSERT INTO product (id,title,sku) VALUES (41,'Preserved','KEEP')"); connection.commit(); connection.close()
    command.upgrade(config, "head")
    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(product_relationship)")}
    assert {"source_product_id", "target_sku", "resolved_target_product_id", "relationship_type", "position"} <= columns
    assert connection.execute("SELECT sku FROM product WHERE id=41").fetchone() == ("KEEP",)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005_relationships"
    connection.close()


def test_legacy_resolution_and_new_block_precedence(relationship_app):
    with relationship_app.app_context():
        source = db.session.get(Product, 101)
        assert relationship_source(source)["relationships"]["cross_sell"] == ["PREMIUM"]
        path = relationship_source(source)["owner"]["path"]
        data = json.loads(path.read_text()); data["relationships"] = {"cross_sells": ["AKITA-PRINT"], "upsells": ["AKITA-LIGHT"]}; _write(path, data)
        resolved = relationship_source(source)
        assert resolved["source"] == "authored"
        assert resolved["relationships"] == {"cross_sell": ["AKITA-PRINT"], "upsell": ["AKITA-LIGHT"]}


def test_single_variable_uses_collection_root(relationship_app):
    with relationship_app.app_context():
        product = db.session.get(Product, 107); source = relationship_source(product)
        assert source["owner"]["kind"] == "collection"
        assert source["owner"]["relative"] == "Single Variable Fixture/product_info.json"
        assert source["relationships"]["cross_sell"] == ["AKITA-CARD"]


def test_search_is_local_bounded_and_excludes_source(relationship_app):
    with relationship_app.app_context():
        items = search_products(101, "Akita Inu")
        assert {item["sku"] for item in items} == {"AKITA-PRINT", "AKITA-LIGHT", "AKITA-MUG", "AKITA-MISSING"}
        assert len(items) <= 100


def test_preview_uses_skus_and_blocks_self_duplicates_missing_unknown(relationship_app):
    with relationship_app.app_context():
        source = db.session.get(Product, 101)
        for values in (["AKITA-CARD"], ["AKITA-PRINT", "AKITA-PRINT"], ["AKITA-MISSING"], ["UNKNOWN"]):
            assert preview_update(source, "cross_sell", values)["continuation_allowed"] is False


def test_single_save_preserves_sparse_metadata_order_and_scanner_columns(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    with relationship_app.app_context():
        source = db.session.get(Product, 101); source.cross_sell_ids = "scanner-cross"; source.upsell_ids = "scanner-up"; db.session.commit()
        apply_update(source, "cross_sell", ["AKITA-LIGHT", "AKITA-PRINT"])
        document = json.loads(relationship_source(source)["owner"]["path"].read_text())
        assert document["short_description"] == "Sparse AKITA-CARD"
        assert document["relationships"]["cross_sells"] == ["AKITA-LIGHT", "AKITA-PRINT"]
        edges = _edges(101, "cross_sell")
        assert [(row.target_sku, row.position, row.resolved_target_product_id) for row in edges] == [("AKITA-LIGHT", 0, 103), ("AKITA-PRINT", 1, 102)]
        db.session.refresh(source); assert (source.cross_sell_ids, source.upsell_ids) == ("scanner-cross", "scanner-up")
        assert relationship_source(source)["owner"]["marker"].read_text() == "1"


def _edges(source_id, kind):
    return ProductRelationship.query.filter_by(source_product_id=source_id, relationship_type=kind).order_by(ProductRelationship.position).all()


def test_broken_sku_survives_projection_rebuild(relationship_app):
    with relationship_app.app_context():
        source = db.session.get(Product, 101); path = relationship_source(source)["owner"]["path"]
        data = json.loads(path.read_text()); data["relationships"] = {"cross_sells": ["REMOVED-SKU"], "upsells": []}; _write(path, data)
        rebuild_relationship_projection(); edge = _edges(101, "cross_sell")[0]
        assert edge.target_sku == "REMOVED-SKU" and edge.resolved_target_product_id is None
        assert relationship_workspace(source)["cross_sell"]["items"][0]["broken"] is True


def test_projection_rebuilds_after_database_projection_loss(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    with relationship_app.app_context():
        source = db.session.get(Product, 101); apply_update(source, "upsell", ["PREMIUM", "AKITA-PRINT"])
        ProductRelationship.query.delete(); db.session.commit(); assert ProductRelationship.query.count() == 0
        rebuild_relationship_projection()
        assert [row.target_sku for row in _edges(101, "upsell")] == ["PREMIUM", "AKITA-PRINT"]


def test_mutual_cross_sells_are_atomic_and_ordered(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    with relationship_app.app_context():
        preview = preview_mutual_cross_sells(["AKITA-CARD", "AKITA-PRINT", "AKITA-LIGHT"])
        assert preview["exact_relationship_count"] == 6
        apply_mutual_cross_sells(preview["selected_skus"])
        assert [row.target_sku for row in _edges(101, "cross_sell")][-2:] == ["AKITA-PRINT", "AKITA-LIGHT"]
        for sku in preview["selected_skus"]:
            document = json.loads(relationship_source(Product.query.filter_by(sku=sku).one())["owner"]["path"].read_text())
            assert set(document["relationships"]["cross_sells"]) >= set(preview["selected_skus"]) - {sku}


def test_mutual_failure_rolls_every_file_back(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    with relationship_app.app_context():
        products = [Product.query.filter_by(sku=sku).one() for sku in ["AKITA-CARD", "AKITA-PRINT", "AKITA-LIGHT"]]
        originals = {row.sku: relationship_source(row)["owner"]["path"].read_bytes() for row in products}
        calls = {"count": 0}
        def fail(stage, _plan):
            if stage == "before_promote":
                calls["count"] += 1
                if calls["count"] == 2: raise OSError("injected")
        relationship_app.config["RELATIONSHIP_FAILURE_INJECTOR"] = fail
        with pytest.raises(OSError): apply_mutual_cross_sells([row.sku for row in products])
        relationship_app.config.pop("RELATIONSHIP_FAILURE_INJECTOR")
        assert {row.sku: relationship_source(row)["owner"]["path"].read_bytes() for row in products} == originals
        assert CatalogueOperation.query.order_by(CatalogueOperation.started_at.desc()).first().recovery_state == "rolled_back"


def test_interrupted_manifest_recovers(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    with relationship_app.app_context():
        rollback_block = {"enabled": True}; calls = {"count": 0}
        def fail(stage, _plan):
            if stage == "before_promote":
                calls["count"] += 1
                if calls["count"] == 2: raise OSError("promotion")
            if stage == "before_rollback" and rollback_block["enabled"]: raise OSError("rollback")
        relationship_app.config["RELATIONSHIP_FAILURE_INJECTOR"] = fail
        with pytest.raises(RelationshipValidationError): apply_mutual_cross_sells(["AKITA-CARD", "AKITA-PRINT"])
        assert list((relationship_app.instance_path and __import__('pathlib').Path(relationship_app.instance_path) / "relationship-transactions").glob("*.json"))
        rollback_block["enabled"] = False
        assert recover_relationship_transactions()["recovered"] == 1
        relationship_app.config.pop("RELATIONSHIP_FAILURE_INJECTOR")


def test_terminal_success_manifest_is_finalized_not_rolled_back(relationship_app):
    with relationship_app.app_context():
        operation = CatalogueOperation(id="committed-relationship", operation_type="product_relationship_update", status="succeeded", scope="{}")
        db.session.add(operation); db.session.commit()
        directory = __import__("pathlib").Path(relationship_app.instance_path) / "relationship-transactions"
        directory.mkdir(exist_ok=True)
        manifest = directory / "committed-relationship.json"
        _write(manifest, {"version": 1, "operation_id": operation.id, "state": "projection_pending", "plans": []})
        assert recover_relationship_transactions() == {"recovered": 1, "recovery_required": 0}
        assert not manifest.exists()
        assert db.session.get(CatalogueOperation, operation.id).status == "succeeded"


def test_routes_require_confirmation_and_use_skus(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    client = _client(relationship_app)
    preview = client.post("/api/products/101/relationships/preview", json={"relationship_type": "cross_sell", "target_skus": ["AKITA-PRINT"], "mode": "add"})
    assert preview.status_code == 200 and preview.json["preview"]["continuation_allowed"]
    assert client.post("/api/products/101/relationships/confirm", json={"relationship_type": "cross_sell", "target_skus": ["AKITA-PRINT"]}).status_code == 422
    done = client.post("/api/products/101/relationships/confirm", json={"relationship_type": "cross_sell", "target_skus": ["AKITA-PRINT"], "confirm": True})
    assert done.status_code == 200 and done.json["summary"]["woo_activity"] is False


def test_no_woo_or_scanner_is_invoked(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.routes.start_scan", lambda *args, **kwargs: pytest.fail("scanner invoked"))
    with relationship_app.app_context():
        apply_update(db.session.get(Product, 101), "cross_sell", ["AKITA-PRINT"])


def test_success_records_one_bounded_operation_and_discord_summary(relationship_app, monkeypatch):
    notices = []
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda summary, operation_id: notices.append((summary, operation_id)))
    with relationship_app.app_context():
        result = apply_update(db.session.get(Product, 101), "upsell", ["PREMIUM"])
        operation = db.session.get(CatalogueOperation, result["operation_id"])
        summary = json.loads(operation.scope)["operation_summary"]
        assert operation.status == "succeeded"
        assert summary["relationship_type"] == "upsell"
        assert summary["upsell_count"] == 1 and summary["woo_activity"] is False
        assert notices == [(summary, operation.id)]


def test_deleted_target_retains_repairable_sku_edge(relationship_app, monkeypatch):
    monkeypatch.setattr("app.product_relationships.notify_product_relationships_completed", lambda *args, **kwargs: None)
    with relationship_app.app_context():
        source = db.session.get(Product, 101)
        apply_update(source, "cross_sell", ["AKITA-PRINT"])
        db.session.delete(db.session.get(Product, 102)); db.session.commit()
        resolve_relationship_targets()
        edge = _edges(101, "cross_sell")[0]
        assert edge.target_sku == "AKITA-PRINT" and edge.resolved_target_product_id is None
        assert relationship_workspace(source)["cross_sell"]["items"][0]["broken"] is True


def test_product_detail_relationship_landmarks_and_keyboard_hooks(relationship_app):
    response = _client(relationship_app).get("/products/101")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'aria-labelledby="relationships-title"' in html
    assert "Product Relationships" in html and "Legacy resolved metadata" in html
    script = (__import__("pathlib").Path("app/static/assets/js/product-relationships.js")).read_text(encoding="utf-8")
    assert all(value in script for value in ("ArrowDown", "ArrowUp", "Enter", "data-target-sku"))
