"""Deployment-root and per-parent transaction regressions; no live services."""
import json
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from app import db, onboarding
from app.models import CatalogueOperation, CatalogueOperationItem, Product, Settings, Variation
from app.product_relationships import RelationshipValidationError, relationship_owner
from app.utils.file_markers import PENDING_FILE
from app.utils.ingest import ingest_rows_to_db
from test_onboarding_deployment import configured
from test_taxonomy_workspace import registry_app, field
from test_marker_recovery import _simple_collection, _image
from test_transactional_ingest import _write_product, _rows, _add_operation


@pytest.mark.parametrize("initial", [True, False])
@pytest.mark.parametrize("fallback", [None, "/old-host/catalogue"])
def test_deployment_root_real_append(configured, initial, fallback):
    app, client, root, catalogue, output = configured
    collection, first, _ = _simple_collection(catalogue.parent)
    second = collection / "Second Token"
    _image(second / "second.png")
    with app.app_context():
        db.session.execute(text("update settings set product_folder=:value"), {"value": fallback})
        db.session.commit()
        assert Settings.query.one().product_folder == str(catalogue)
        if not initial:
            (Path(app.instance_path) / "onboarding.json").unlink()
    token = field(client.get("/scanner"), "csrf_token")
    response = client.post("/scanner/start", json={"mode": "append", "confirm_operation": True}, headers={"X-CSRFToken": token})
    assert response.status_code == 202
    operation_id = response.json["operation_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with app.app_context():
            if db.session.get(CatalogueOperation, operation_id).status != "running":
                break
        time.sleep(.02)
    with app.app_context():
        operation = db.session.get(CatalogueOperation, operation_id)
        items = CatalogueOperationItem.query.filter_by(operation_id=operation_id).all()
        assert operation.status == "succeeded", [(i.sku, i.error) for i in items]
        assert operation.products_succeeded == 2
        assert Product.query.count() == 2
        assert len(items) == 2
        for product in Product.query.all():
            owner = relationship_owner(product)
            assert owner["path"].is_relative_to(catalogue)
            assert product.source_relpath
        assert all(i.database_state == "committed" and i.marker_state == "finalized" for i in items)
        assert db.session.execute(text("select product_folder from settings")).scalar() == fallback
        if initial:
            assert onboarding.can_complete(operation)
    for folder in (first, second):
        assert (folder / ".scanned").exists()
        assert not (folder / PENDING_FILE).exists()
    if initial:
        assert client.post("/setup/complete", data={"csrf_token": token, "operation_id": operation_id}).status_code == 302
        with app.app_context():
            assert not onboarding.pending()


@pytest.mark.parametrize("record_operation", [True, False])
def test_first_failure_does_not_autobegin_next_parent(configured, record_operation):
    app, _, _, catalogue, _ = configured
    collection = catalogue / "Transactional Collection"
    collection.mkdir()
    (collection / "product_info.json").write_text(json.dumps({"collection_type": "Variable Collection", "sku_prefix": "FIC-TX-"}))
    for name, sku in [("First", "FIC-TX-0001"), ("Second", "FIC-TX-0002")]:
        _write_product(catalogue, name, sku)
    with app.app_context():
        operation_id = "first-parent-failure" if record_operation else None
        if operation_id:
            _add_operation(operation_id)
        boundary_states = []
        def log(message, *args, **kwargs):
            if "rolled back:" in message:
                boundary_states.append(db.session().in_transaction())
        def inject(stage, sku):
            if stage == "variation_images" and sku == "FIC-TX-0001":
                raise ValueError("first parent rejected safely")
        result = ingest_rows_to_db(_rows("FIC-TX-0001") + _rows("FIC-TX-0002"), operation_id=operation_id, log=log, failure_injector=inject)
        assert result["products_failed"] == 1, result
        assert result["products_created"] == 1
        assert boundary_states == [False]
        assert Product.query.filter_by(sku="FIC-TX-0001").first() is None
        assert Product.query.filter_by(sku="FIC-TX-0002").one().variations
        assert Variation.query.count() == 1
        if operation_id:
            failed = CatalogueOperationItem.query.filter_by(operation_id=operation_id, status="failed").one()
            assert failed.database_state == "rolled_back"
            assert failed.error == "first parent rejected safely"


@pytest.mark.parametrize("escape", ["absolute", "traversal", "symlink"])
def test_relationship_authority_remains_confined(configured, escape):
    app, _, root, catalogue, _ = configured
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "product_info.json").write_text("{}")
    reference = str(outside / "product_info.json")
    if escape == "traversal":
        reference = "../outside/product_info.json"
    elif escape == "symlink":
        (catalogue / "escape").symlink_to(outside, target_is_directory=True)
        reference = "escape/product_info.json"
    with app.app_context():
        product = Product(sku="SAFE-1", title="Fictional", override_json_path=reference)
        with pytest.raises(RelationshipValidationError, match="unavailable or unsafe"):
            relationship_owner(product)


@pytest.mark.parametrize("fallback", [None, "/old-host/catalogue"])
def test_reconstruction_uses_same_deployment_root(configured, fallback):
    from test_reconstruction import _write_catalogue
    app, client, _, catalogue, _ = configured
    _write_catalogue(catalogue)
    with app.app_context():
        db.session.execute(text("update settings set product_folder=:value"), {"value": fallback})
        db.session.commit()
    token = field(client.get("/scanner"), "csrf_token")
    response = client.post("/catalogue/reconstruct", data={"csrf_token": token, "confirm_reconstruction": "yes"})
    assert response.status_code == 302
    with app.app_context():
        operation = db.session.get(CatalogueOperation, response.headers["Location"].rsplit("/", 1)[1])
        assert operation.status == "succeeded", operation.error
        assert onboarding.can_complete(operation)
        assert Product.query.count() > 0
        assert all(relationship_owner(p)["path"].is_relative_to(catalogue) for p in Product.query.all())


def test_root_lookup_preserves_explicit_blank_and_local_fallback(configured):
    from app.utils.ingest import _ingest_catalogue_root
    app, _, _, catalogue, _ = configured
    with app.app_context():
        app.config["PRODUCT_FOLDER"] = ""
        assert _ingest_catalogue_root() == ""
        assert not db.session().in_transaction()
        app.config["PRODUCT_FOLDER"] = None
        assert _ingest_catalogue_root() == str(catalogue)
        assert not db.session().in_transaction()


def test_failed_first_parent_marker_recovery_then_retry(configured, monkeypatch):
    from app.utils import ingest
    app, client, _, catalogue, _ = configured
    collection, first, _ = _simple_collection(catalogue.parent)
    second = collection / "Second Token"
    _image(second / "second.png")
    with app.app_context():
        db.session.execute(text("update settings set product_folder=NULL"))
        db.session.commit()
    original = ingest._checkpoint
    def fail(stage, sku):
        if stage == "product_images" and sku == "FIC-S-0001":
            raise ValueError("first parent rejected safely")
    monkeypatch.setattr(ingest, "_checkpoint", lambda injector, stage, sku: fail(stage, sku))
    token = field(client.get("/scanner"), "csrf_token")
    def run():
        response = client.post("/scanner/start", json={"mode": "append", "confirm_operation": True}, headers={"X-CSRFToken": token})
        assert response.status_code == 202
        operation_id = response.json["operation_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with app.app_context():
                if db.session.get(CatalogueOperation, operation_id).status != "running":
                    return operation_id
            time.sleep(.02)
        pytest.fail("Scan did not finish")
    failed_id = run()
    with app.app_context():
        operation = db.session.get(CatalogueOperation, failed_id)
        assert operation.products_failed == 1 and operation.products_succeeded == 1
        assert not onboarding.can_complete(operation)
        failed = CatalogueOperationItem.query.filter_by(operation_id=failed_id, sku="FIC-S-0001").one()
        assert failed.database_state == "rolled_back"
        assert failed.marker_state == "database_recovery_required"
        assert Product.query.filter_by(sku="FIC-S-0001").first() is None
        survivor_id = Product.query.filter_by(sku="FIC-S-0002").one().id
    assert (first / PENDING_FILE).exists() and (first / ".update").exists()
    assert not (first / ".scanned").exists()
    survivor_marker = (second / ".scanned").read_bytes()
    assert client.post("/setup/complete", data={"csrf_token": token, "operation_id": failed_id}).status_code == 409
    monkeypatch.setattr(ingest, "_checkpoint", original)
    retry_id = run()
    with app.app_context():
        assert onboarding.can_complete(db.session.get(CatalogueOperation, retry_id))
        assert Product.query.count() == 2
        assert Product.query.filter_by(sku="FIC-S-0002").one().id == survivor_id
        assert Product.query.filter_by(sku="FIC-S-0001").count() == 1
    assert (first / ".scanned").exists() and not (first / PENDING_FILE).exists()
    assert (second / ".scanned").read_bytes() == survivor_marker
