import json
from pathlib import Path

import pytest

from app import create_app, db
from app.models import CatalogueOperation, Collection, Product, ProductAsset, Settings, User
from app.utils.operation_control import (
    finish_catalogue_operation,
    reset_operation_control_for_tests,
)
from config import Config


@pytest.fixture
def override_workflow(tmp_path, monkeypatch):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    catalogue = tmp_path / "catalogue"
    collection_root = catalogue / "Fictional Collection"
    output = tmp_path / "output"
    collection_root.mkdir(parents=True)
    output.mkdir()
    shared_path = collection_root / "product_info.json"
    shared_path.write_text(
        json.dumps(
            {
                "collection_type": "Simple",
                "sku_prefix": "FIX",
                "title": "Fictional shared title",
            }
        ),
        encoding="utf-8",
    )

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    reset_operation_control_for_tests()
    calls = []
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            user = User(
                email="override@example.invalid",
                username="override-admin",
                password="unused-test-password",
                is_admin=True,
            )
            collection = Collection(
                name="Fictional Collection",
                root_path=str(collection_root),
                sku_prefix="FIX",
                shared_json_path=str(shared_path),
                source_relpath="Fictional Collection",
            )
            db.session.add_all(
                [
                    user,
                    collection,
                    Settings(
                        product_folder=str(catalogue),
                        output_folder=str(output),
                        url_prefix="https://example.invalid/catalogue/",
                    ),
                ]
            )
            db.session.flush()
            products = []
            for index in range(1, 5):
                folder = collection_root / f"Product {index}"
                folder.mkdir()
                product = Product(
                    collection_id=collection.id,
                    sku=f"FIX-{index:03d}",
                    title=f"Fictional Product {index}",
                    product_type="simple",
                    catalogue_status="active",
                    product_dir=str(folder),
                    source_relpath=f"Fictional Collection/Product {index}",
                )
                db.session.add(product)
                db.session.flush()
                db.session.add(
                    ProductAsset(
                        product_id=product.id,
                        path=str(shared_path),
                        kind="info",
                        label="shared",
                        is_primary=True,
                    )
                )
                products.append(product)
            db.session.commit()
            product_ids = [product.id for product in products]
            user_id = user.id

        def record_scan(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr("app.routes.start_scan", record_scan)
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        yield {
            "app": app,
            "client": client,
            "anonymous": app.test_client(),
            "collection_root": collection_root,
            "product_ids": product_ids,
            "calls": calls,
        }
    finally:
        with app.app_context():
            db.session.remove()
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _finish_current_creation(app):
    with app.app_context():
        operation = CatalogueOperation.query.filter_by(status="running").one()
        finish_catalogue_operation(operation.id, status="succeeded")
        return operation.id


def test_create_override_is_authenticated_and_reaches_editor(override_workflow):
    workflow = override_workflow
    product_id = workflow["product_ids"][0]
    anonymous = workflow["anonymous"].post(
        f"/api/override/create/{product_id}", json={"rel": "Product 1"}
    )
    assert anonymous.status_code == 401

    response = workflow["client"].post(
        f"/api/override/create/{product_id}", json={"rel": "Product 1"}
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["created"] is True
    assert body["run_id"]
    assert body["edit_url"] == f"/edit_products/{product_id}/edit/override"

    target = workflow["collection_root"] / "Product 1" / "product_info.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {}
    editor = workflow["client"].get(body["edit_url"])
    assert editor.status_code == 200
    assert str(target) in editor.get_data(as_text=True)
    raw = workflow["client"].get(f"/assets/info/{product_id}/override")
    assert raw.status_code == 200
    assert raw.get_json() == {}

    with workflow["app"].app_context():
        assert CatalogueOperation.query.count() == 1
        assert ProductAsset.query.filter_by(
            product_id=product_id, kind="info", label="override"
        ).count() == 1
    assert len(workflow["calls"]) == 1


def test_duplicate_create_override_is_idempotent(override_workflow):
    workflow = override_workflow
    product_id = workflow["product_ids"][0]
    url = f"/api/override/create/{product_id}"

    first = workflow["client"].post(url, json={"rel": "Product 1"})
    assert first.status_code == 200
    for _ in range(5):
        duplicate = workflow["client"].post(url, json={"rel": "Product 1"})
        assert duplicate.status_code == 200
        assert duplicate.get_json() == {
            "ok": True,
            "created": False,
            "run_id": None,
            "edit_url": f"/edit_products/{product_id}/edit/override",
        }

    folder = workflow["collection_root"] / "Product 1"
    assert list(folder.glob("product_info.json")) == [folder / "product_info.json"]
    with workflow["app"].app_context():
        assert CatalogueOperation.query.count() == 1
        assert ProductAsset.query.filter_by(
            product_id=product_id, kind="info", label="override"
        ).count() == 1
    assert len(workflow["calls"]) == 1


def test_multiple_isolated_override_creations_have_only_valid_destinations(
    override_workflow,
):
    workflow = override_workflow
    statuses = []
    for index, product_id in enumerate(workflow["product_ids"], start=1):
        response = workflow["client"].post(
            f"/api/override/create/{product_id}",
            json={"rel": f"Product {index}"},
        )
        statuses.append(response.status_code)
        assert workflow["client"].get(response.get_json()["edit_url"]).status_code == 200
        _finish_current_creation(workflow["app"])

    assert statuses == [200, 200, 200, 200]
    with workflow["app"].app_context():
        assert CatalogueOperation.query.count() == 4
    assert len(workflow["calls"]) == 4


def test_meaningful_override_save_preserves_existing_update_contract(
    override_workflow,
):
    workflow = override_workflow
    product_id = workflow["product_ids"][0]
    create = workflow["client"].post(
        f"/api/override/create/{product_id}", json={"rel": "Product 1"}
    )
    assert create.status_code == 200
    creation_operation = _finish_current_creation(workflow["app"])

    target = workflow["collection_root"] / "Product 1" / "product_info.json"
    response = workflow["client"].post(
        "/edit_products/FIX-001/save",
        json={"kind": "override", "data": {"title": "Meaningful override"}},
    )
    assert response.status_code == 200
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "title": "Meaningful override"
    }
    assert (target.parent / ".update").exists()
    assert len(list(target.parent.glob("product_info.json.bak.*"))) == 1
    with workflow["app"].app_context():
        operations = CatalogueOperation.query.order_by(CatalogueOperation.started_at).all()
        assert len(operations) == 2
        assert operations[0].id == creation_operation
        assert operations[1].operation_type == "product_update"
    assert len(workflow["calls"]) == 2


def test_override_create_template_prevents_duplicate_browser_submission():
    template = Path("app/templates/edit_products.html").read_text(encoding="utf-8")
    browser_script = Path("app/static/assets/js/products-browser.js").read_text(
        encoding="utf-8"
    )
    client_script = Path("app/static/assets/js/override-client.js").read_text(
        encoding="utf-8"
    )
    assert "products-browser.js" in template
    assert 'chooseButton.disabled = true' in browser_script
    assert 'chooseButton.setAttribute("aria-busy", "true")' in browser_script
    assert 'chooseButton.dataset.submitting === "true"' in browser_script
    assert "ProductsOverrideClient" in browser_script
    assert "requestOverrideJson" in browser_script
    assert "editorDestination" in browser_script
    assert "async function readJson" in client_script


def test_override_create_404_does_not_create_a_file_or_operation(override_workflow):
    workflow = override_workflow
    response = workflow["client"].post(
        "/api/override/create/999999", json={"rel": "Product 1"}
    )

    assert response.status_code == 404
    with workflow["app"].app_context():
        assert CatalogueOperation.query.count() == 0
        assert ProductAsset.query.filter_by(kind="info", label="override").count() == 0
    assert not (workflow["collection_root"] / "Product 1" / "product_info.json").exists()
    assert workflow["calls"] == []


def test_override_create_conflict_does_not_duplicate_file_or_operation(
    override_workflow,
):
    workflow = override_workflow
    first_id, second_id = workflow["product_ids"][:2]
    first = workflow["client"].post(
        f"/api/override/create/{first_id}", json={"rel": "Product 1"}
    )
    assert first.status_code == 200

    conflict = workflow["client"].post(
        f"/api/override/create/{second_id}", json={"rel": "Product 2"}
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["error"] == "catalogue_operation_active"
    with workflow["app"].app_context():
        assert CatalogueOperation.query.count() == 1
        assert ProductAsset.query.filter_by(
            product_id=second_id, kind="info", label="override"
        ).count() == 0
    assert not (workflow["collection_root"] / "Product 2" / "product_info.json").exists()
    assert len(workflow["calls"]) == 1
