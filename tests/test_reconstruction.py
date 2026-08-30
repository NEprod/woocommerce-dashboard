import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app import create_app, db
from app.database import restore_database
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Collection,
    Product,
    ProductRelationship,
    Settings,
    User,
    Variation,
)
from app.utils.operation_control import (
    CatalogueOperationActive,
    acquire_catalogue_operation,
    finish_catalogue_operation,
    reset_operation_control_for_tests,
)
from app.utils.reconstruction import (
    detect_setup_state,
    run_reconstruction,
)
from config import Config


@pytest.fixture
def reconstruction_app(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    database = instance / "site.db"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    catalogue.mkdir()
    output.mkdir()

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    reset_operation_control_for_tests()
    try:
        app = create_app()
        app.config.update(TESTING=True, LOGIN_DISABLED=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add_all(
                [
                    User(
                        id=91,
                        email="reconstruction@invalid.example",
                        username="reconstruction-admin",
                        password="fixture-only",
                    ),
                    Settings(
                        id=92,
                        product_folder=str(catalogue),
                        output_folder=str(output),
                        url_prefix="https://invalid.example/",
                    ),
                    CatalogueOperation(
                        id="historical-operation",
                        operation_type="append",
                        status="succeeded",
                        scope="{}",
                    ),
                ]
            )
            db.session.commit()
        yield app, catalogue, database
    finally:
        with app.app_context():
            db.session.remove()
        reset_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _write_catalogue(catalogue):
    simple = catalogue / "Simple Collection"
    first = simple / "Existing Simple"
    first.mkdir(parents=True)
    (simple / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Simple",
                "sku_prefix": "REC-S-",
                "title": "Reconstruction Simple",
                "price": "5.00",
            }
        ),
        encoding="utf-8",
    )
    (first / "product_info.json").write_text(
        json.dumps({"title": "Existing Simple", "relationships": {"cross_sells": ["REC-V-0007"], "upsells": []}}), encoding="utf-8"
    )
    (first / ".scanned").write_text(
        json.dumps(
            {
                "sku": "REC-S-0042",
                "title": "Existing Simple",
                "images_used": [],
                "scan_date": "2026-01-02T03:04:05",
            }
        ),
        encoding="utf-8",
    )
    (simple / "sku_index.json").write_text(
        json.dumps({"counter": 42}), encoding="utf-8"
    )

    variable = catalogue / "Variable Collection"
    product = variable / "Existing Variable"
    product.mkdir(parents=True)
    (variable / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Variable Collection",
                "sku_prefix": "REC-V-",
                "title": "Reconstruction Variable",
                "price": "8.00",
                "attributes": {"Size": ["Small", "Large"]},
                "image_attributes": ["Size"],
            }
        ),
        encoding="utf-8",
    )
    (product / "product_info.json").write_text(
        json.dumps({"relationships": {"cross_sells": [], "upsells": ["REC-S-0042"]}}),
        encoding="utf-8",
    )
    (product / ".scanned").write_text(
        json.dumps(
            {
                "sku": "REC-V-0007",
                "title": "Existing Variable",
                "images_used": [],
                "variation_count": 2,
                "variations": [
                    {"attributes": {"Size": "Small"}, "sku": "REC-V-0007-3"},
                    {"attributes": {"Size": "Large"}, "sku": "REC-V-0007-9"},
                ],
                "scan_date": "2026-01-02T03:04:05",
            }
        ),
        encoding="utf-8",
    )
    (variable / "sku_index.json").write_text(
        json.dumps({"counter": 7}), encoding="utf-8"
    )
    (product / "sku_index.json").write_text(
        json.dumps({"variation_counter": 9}), encoding="utf-8"
    )
    return first, product


def _projection_snapshot():
    return {
        "collections": [
            (row.id, row.source_relpath)
            for row in Collection.query.order_by(Collection.id)
        ],
        "products": [
            (
                row.id,
                row.sku,
                row.source_relpath,
                row.catalogue_status,
                row.woo_id,
                row.woo_synced_at,
            )
            for row in Product.query.order_by(Product.id)
        ],
        "variations": [
            (
                row.id,
                row.product_id,
                row.sku,
                row.source_identity,
                row.catalogue_status,
                row.woo_id,
                row.woo_synced_at,
            )
            for row in Variation.query.order_by(Variation.id)
        ],
    }


def test_setup_state_distinguishes_new_reconstruction_ready_and_ambiguous(
    reconstruction_app,
):
    app, catalogue, _database = reconstruction_app
    with app.app_context():
        state = detect_setup_state()
        assert state.code == "new_catalogue"
        assert state.marker_count == 0
        assert state.projection_products == 0
        assert state.recommended_action == "append"
        assert state.full_scan_automatic is False

    first, _variable = _write_catalogue(catalogue)
    with app.app_context():
        state = detect_setup_state()
        assert state.code == "reconstruction_required"
        assert state.marker_count == 2
        assert state.product_count == 2
        assert state.recommended_action == "reconstruction"
        assert state.identities_preserved is True

    (first / ".scanned").write_text("{malformed", encoding="utf-8")
    with app.app_context():
        state = detect_setup_state()
        assert state.code == "ambiguous"
        assert state.safe_to_run is False
        assert state.recommended_action is None


def test_reconstruction_preserves_all_existing_identities_and_state(
    reconstruction_app, monkeypatch
):
    app, catalogue, _database = reconstruction_app
    simple_folder, variable_folder = _write_catalogue(catalogue)
    marker_before = {
        simple_folder: (simple_folder / ".scanned").read_bytes(),
        variable_folder: (variable_folder / ".scanned").read_bytes(),
    }
    indexes_before = {
        path: path.read_bytes()
        for path in (
            catalogue / "Simple Collection" / "sku_index.json",
            catalogue / "Variable Collection" / "sku_index.json",
            variable_folder / "sku_index.json",
        )
    }
    (simple_folder / ".update").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(
        "app.utils.discord.notify_ingest_product",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Discord must be suppressed")
        ),
    )

    with app.app_context():
        result = run_reconstruction()
        assert result.status == "succeeded"
        assert result.backup_path.is_file()
        assert result.backup_path.parent == Path(db.engine.url.database).parent / "backups"
        assert {row.sku for row in Product.query.all()} == {
            "REC-S-0042",
            "REC-V-0007",
        }
        assert {row.sku for row in Variation.query.all()} == {
            "REC-V-0007-3",
            "REC-V-0007-9",
        }
        variable = Product.query.filter_by(sku="REC-V-0007").one()
        assert variable.collection.source_relpath == "Variable Collection"
        assert all(row.product_id == variable.id for row in variable.variations)
        simple = Product.query.filter_by(sku="REC-S-0042").one()
        assert {
            (row.relationship_type, row.target_sku, row.position)
            for row in ProductRelationship.query.all()
        } == {
            ("cross_sell", "REC-V-0007", 0),
            ("upsell", "REC-S-0042", 0),
        }
        assert ProductRelationship.query.filter_by(source_product_id=simple.id).one().resolved_target_product_id == variable.id
        assert User.query.filter_by(id=91).one()
        assert Settings.query.filter_by(id=92).one()
        assert db.session.get(CatalogueOperation, "historical-operation")

    for folder, payload in marker_before.items():
        assert (folder / ".scanned").read_bytes() == payload
        assert not (folder / ".scanned.pending").exists()
    for path, payload in indexes_before.items():
        assert path.read_bytes() == payload
    assert (simple_folder / ".update").read_text(encoding="utf-8") == "fixture"


def test_reconstruction_preserves_internal_and_woo_ids_and_is_idempotent(
    reconstruction_app,
):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    with app.app_context():
        first = run_reconstruction()
        assert first.status == "succeeded"
        product = Product.query.filter_by(sku="REC-V-0007").one()
        variation = Variation.query.filter_by(sku="REC-V-0007-9").one()
        product.woo_id = 8101
        product.woo_synced_at = datetime(2026, 2, 3, 4, 5, 6)
        variation.woo_id = 8201
        variation.woo_synced_at = datetime(2026, 3, 4, 5, 6, 7)
        db.session.commit()
        identity = (product.id, variation.id)

        second = run_reconstruction()
        assert second.status == "succeeded"
        product = Product.query.filter_by(sku="REC-V-0007").one()
        variation = Variation.query.filter_by(sku="REC-V-0007-9").one()
        assert (product.id, variation.id) == identity
        assert product.woo_id == 8101
        assert product.woo_synced_at == datetime(2026, 2, 3, 4, 5, 6)
        assert variation.woo_id == 8201
        assert variation.woo_synced_at == datetime(2026, 3, 4, 5, 6, 7)
        assert Product.query.count() == 2
        assert Variation.query.count() == 2


def test_reconstruction_allocates_only_genuinely_new_identities(
    reconstruction_app,
):
    app, catalogue, _database = reconstruction_app
    _simple, variable_folder = _write_catalogue(catalogue)
    new_product = catalogue / "Simple Collection" / "New Simple"
    new_product.mkdir()
    (new_product / "product_info.json").write_text(
        json.dumps({"title": "New Simple"}), encoding="utf-8"
    )

    with app.app_context():
        result = run_reconstruction()
        assert result.status == "succeeded"
        assert Product.query.filter_by(sku="REC-S-0043").one()
        assert json.loads((new_product / ".scanned").read_text())["sku"] == "REC-S-0043"

    shared = catalogue / "Variable Collection" / "product_info.json"
    data = json.loads(shared.read_text(encoding="utf-8"))
    data["attributes"]["Size"].append("Medium")
    shared.write_text(json.dumps(data), encoding="utf-8")
    with app.app_context():
        result = run_reconstruction()
        assert result.status == "succeeded"
        assert {row.sku for row in Variation.query.all()} == {
            "REC-V-0007-3",
            "REC-V-0007-9",
            "REC-V-0007-10",
        }
        marker = json.loads((variable_folder / ".scanned").read_text())
        # Valid established marker payload is never rewritten by reconstruction.
        assert {row["sku"] for row in marker["variations"]} == {
            "REC-V-0007-3",
            "REC-V-0007-9",
        }
        index_path = variable_folder / "sku_index.json"
        index_after_new_variation = index_path.read_bytes()
        assert run_reconstruction().status == "succeeded"
        assert index_path.read_bytes() == index_after_new_variation
        assert {row.sku for row in Variation.query.all()} == {
            "REC-V-0007-3",
            "REC-V-0007-9",
            "REC-V-0007-10",
        }


def test_failed_pre_resolution_and_replacement_leave_projection_usable(
    reconstruction_app,
):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    with app.app_context():
        assert run_reconstruction().status == "succeeded"
        before = _projection_snapshot()

    shared = catalogue / "Variable Collection" / "product_info.json"
    original_shared = shared.read_bytes()
    shared.write_text("{invalid", encoding="utf-8")
    with app.app_context():
        failed = run_reconstruction()
        assert failed.status == "failed"
        assert failed.backup_path is None
        assert _projection_snapshot() == before
    shared.write_bytes(original_shared)

    def fail(stage, _sku):
        if stage == "parent":
            raise RuntimeError("replacement fixture failure token=private")

    with app.app_context():
        failed = run_reconstruction(failure_injector=fail)
        assert failed.status == "failed"
        assert failed.backup_path and failed.backup_path.is_file()
        assert _projection_snapshot() == before
        operation = db.session.get(CatalogueOperation, failed.operation_id)
        assert "private" not in operation.error
        assert "[REDACTED]" in operation.error
        item = CatalogueOperationItem.query.filter_by(
            operation_id=failed.operation_id, status="failed"
        ).one()
        assert item.database_state == "rolled_back"
        assert "private" not in item.error


def test_reconstruction_backup_is_valid_and_restorable(reconstruction_app, tmp_path):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    with app.app_context():
        result = run_reconstruction()
        backup = result.backup_path
        connection = sqlite3.connect(backup)
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()

    restored = tmp_path / "restored.db"
    restore_database(backup, restored)
    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 1
    finally:
        connection.close()


def test_post_replacement_failure_restores_verified_backup(reconstruction_app):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    with app.app_context():
        assert run_reconstruction().status == "succeeded"
        before = _projection_snapshot()

        def fail(stage, _sku):
            if stage == "after_replacement":
                raise RuntimeError("post replacement fixture failure")

        result = run_reconstruction(failure_injector=fail)
        assert result.status == "failed"
        assert result.backup_path and result.backup_path.is_file()
        assert _projection_snapshot() == before
        assert db.session.get(CatalogueOperation, result.operation_id).status == "failed"


def test_reconstruction_marks_absent_only_after_complete_success(reconstruction_app):
    app, catalogue, _database = reconstruction_app
    first_folder, _variable = _write_catalogue(catalogue)
    with app.app_context():
        assert run_reconstruction().status == "succeeded"
    renamed = first_folder.with_name("Temporarily Removed")
    first_folder.rename(renamed)
    # The renamed directory is still a valid product, so remove it outside scope.
    detached = catalogue.parent / "detached-product"
    renamed.rename(detached)
    with app.app_context():
        result = run_reconstruction()
        assert result.status == "succeeded"
        assert Product.query.filter_by(sku="REC-S-0042").one().catalogue_status == "missing"


def test_operation_lock_blocks_reconstruction(reconstruction_app):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    with app.app_context():
        lease = acquire_catalogue_operation("append")
        with pytest.raises(CatalogueOperationActive):
            run_reconstruction()
        finish_catalogue_operation(lease.id, status="succeeded")


def test_pending_marker_identity_is_preserved_and_reported_for_recovery(
    reconstruction_app,
):
    app, catalogue, _database = reconstruction_app
    _simple, variable = _write_catalogue(catalogue)
    scanned_path = variable / ".scanned"
    marker = json.loads(scanned_path.read_text(encoding="utf-8"))
    scanned_path.unlink()
    pending_path = variable / ".scanned.pending"
    pending_payload = {
        "version": 1,
        "operation_id": "interrupted-fixture",
        "state": "pending_database",
        "marker": marker,
    }
    pending_path.write_text(json.dumps(pending_payload), encoding="utf-8")

    with app.app_context():
        state = detect_setup_state()
        assert state.pending_count == 1
        result = run_reconstruction()
        assert result.status == "partial"
        assert result.recovery_required is True
        assert Product.query.filter_by(sku="REC-V-0007").one()
        assert {row.sku for row in Variation.query.all()} == {
            "REC-V-0007-3",
            "REC-V-0007-9",
        }
    assert json.loads(pending_path.read_text(encoding="utf-8")) == pending_payload


def test_full_scan_requires_explicit_confirmation_and_stays_separate(
    reconstruction_app, monkeypatch
):
    app, _catalogue, _database = reconstruction_app
    captured = {}

    def capture_start(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.routes.start_scan", capture_start)
    response = app.test_client().post("/initial-scan/start", json={"mode": "full"})
    assert response.status_code == 400
    assert captured == {}

    response = app.test_client().post(
        "/initial-scan/start",
        json={"mode": "full", "confirm_full_regeneration": True},
    )
    assert response.status_code == 200
    assert captured["scan_mode"] == "full"


def test_ambiguous_state_blocks_append_and_full_api_actions(
    reconstruction_app, monkeypatch
):
    app, catalogue, _database = reconstruction_app
    first, _variable = _write_catalogue(catalogue)
    (first / ".scanned").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        "app.routes.start_scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe scan must not start")
        ),
    )
    for payload in (
        {"mode": "append"},
        {"mode": "full", "confirm_full_regeneration": True},
    ):
        response = app.test_client().post("/initial-scan/start", json=payload)
        assert response.status_code == 409
        assert response.get_json()["error"] == "catalogue_state_ambiguous"
        assert str(catalogue.parent) not in response.get_data(as_text=True)


def test_reconstruction_route_is_authenticated_and_reports_safe_paths(
    reconstruction_app, monkeypatch
):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    app.config["LOGIN_DISABLED"] = False
    response = app.test_client().post("/catalogue/reconstruct")
    assert response.status_code in {302, 401}

    app.config["LOGIN_DISABLED"] = True
    response = app.test_client().post("/catalogue/reconstruct")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "succeeded"
    assert payload["backup"].startswith("backups/")
    assert payload["progress"]["operation"]["type"] == "reconstruction"
    assert payload["progress"]["operation"]["stage"] == "completed"
    assert payload["progress"]["counts"]["failures"] == 0
    assert payload["catalogue"]["products"] == payload["products"]
    assert payload["catalogue"]["variations"] >= 1
    assert str(catalogue.parent) not in json.dumps(payload)


def test_initial_setup_ui_explains_reconstruction_and_full_reset_separately(
    reconstruction_app,
):
    app, catalogue, _database = reconstruction_app
    _write_catalogue(catalogue)
    response = app.test_client().get("/initial-scan")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Reconstruction Required" in page
    assert "Rebuild catalogue index" in page
    assert "Existing catalogue identities" in page
    assert "Existing parent and variation identities will be preserved" in page
    assert "Intentional full regeneration" in page
    assert "may regenerate parent and variation SKU identities" in page
