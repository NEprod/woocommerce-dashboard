import json
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Product,
    Settings,
    Variation,
)
from app.utils.file_markers import (
    PENDING_FILE,
    load_pending_scanned,
    write_scanned,
)
from app.utils.ingest import ingest_rows_to_db
from app.utils.marker_recovery import (
    finalize_ingested_markers,
    mark_pending_database_recovery,
    recover_committed_markers,
)
from app.utils import scanner as scanner_module
from app.utils.scanner import scan_collection
from app.utils.scan_runner import get_progress, start_scan
from app.utils.sku_manager import save_sku_index
from config import Config


FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 3), "#336699").save(path)


def _simple_collection(tmp_path, product_name="Blue Token"):
    collection = tmp_path / "catalogue" / "Simple Collection"
    shutil.copytree(FIXTURES / "Simple Collection", collection)
    product = collection / product_name
    if product_name != "Blue Token":
        shutil.copytree(collection / "Blue Token", product)
    _image(product / f"{product_name.lower().replace(' ', '-')}.png")
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return collection, product, output


def _variable_collection(tmp_path):
    collection = tmp_path / "catalogue" / "Variable Collection"
    shutil.copytree(FIXTURES / "Variable Collection", collection)
    product = collection / "Badge One"
    _image(product / "badge.png")
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return collection, product, output


def _stage_collection(collection, output, operation_id, quiet_log, **kwargs):
    return scan_collection(
        collection,
        "https://invalid.example/assets/",
        output,
        log=quiet_log,
        defer_markers=True,
        operation_id=operation_id,
        **kwargs,
    )


@pytest.fixture
def recovery_app(tmp_path):
    database = tmp_path / "recovery.db"
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        yield app
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _configure(app, catalogue_root, output):
    with app.app_context():
        db.session.add(
            Settings(
                product_folder=str(catalogue_root),
                output_folder=str(output),
                url_prefix="https://invalid.example/assets/",
            )
        )
        db.session.commit()


def _operation(operation_id):
    db.session.add(
        CatalogueOperation(
            id=operation_id,
            operation_type="append",
            status="running",
            scope="{}",
        )
    )
    db.session.commit()


def test_scanned_replacement_is_atomic_and_preserves_valid_marker_on_failure(
    tmp_path, quiet_log, monkeypatch
):
    folder = tmp_path / "product"
    folder.mkdir()
    marker = folder / ".scanned"
    marker.write_text('{"sku":"FIC-OLD"}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("fixture replace failure")

    monkeypatch.setattr("app.utils.atomic_files.os.replace", fail_replace)
    with pytest.raises(OSError, match="fixture replace failure"):
        write_scanned(folder, {"sku": "FIC-NEW"}, log=quiet_log)

    assert marker.read_text(encoding="utf-8") == '{"sku":"FIC-OLD"}'
    assert not list(folder.glob(".*.tmp"))


def test_sku_index_replacement_is_atomic_and_preserves_valid_index_on_failure(
    tmp_path, quiet_log, monkeypatch
):
    index = tmp_path / "sku_index.json"
    index.write_text('{"counter":7}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("fixture index replace failure")

    monkeypatch.setattr("app.utils.atomic_files.os.replace", fail_replace)
    with pytest.raises(OSError, match="fixture index replace failure"):
        save_sku_index({"counter": 8}, tmp_path, log=quiet_log)

    assert index.read_text(encoding="utf-8") == '{"counter":7}'
    assert not list(tmp_path.glob(".*.tmp"))


def test_pending_marker_preserves_scanned_payload_and_delays_update_removal(
    tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    (product / ".update").write_text("fixture", encoding="utf-8")

    rows = _stage_collection(collection, output, "pending-payload", quiet_log)

    assert [row["SKU"] for row in rows] == ["FIC-S-0001"]
    assert not (product / ".scanned").exists()
    assert (product / ".update").read_text(encoding="utf-8") == "fixture"
    pending = load_pending_scanned(product, log=quiet_log)
    assert set(pending) == {"version", "operation_id", "state", "marker"}
    assert pending["version"] == 1
    assert pending["operation_id"] == "pending-payload"
    assert pending["state"] == "pending_database"
    assert set(pending["marker"]) == {
        "sku",
        "title",
        "images_used",
        "scan_date",
    }
    assert pending["marker"]["sku"] == "FIC-S-0001"
    assert pending["marker"]["title"] == "Blue Token - Fictional Desk Token"


def test_recoverable_pipeline_orders_pending_before_database_and_marker_after_commit(
    recovery_app, tmp_path, quiet_log, monkeypatch
):
    collection, product, output = _simple_collection(tmp_path)
    catalogue_root = collection.parent
    (product / ".update").write_text("fixture", encoding="utf-8")
    events = []
    original_generate = scanner_module.generate_sku
    original_process = scanner_module.process_images
    original_write = scanner_module.write_scanned

    def generate(*args, **kwargs):
        result = original_generate(*args, **kwargs)
        events.append("sku_index")
        return result

    def process(*args, **kwargs):
        events.append("images")
        return original_process(*args, **kwargs)

    def write(*args, **kwargs):
        result = original_write(*args, **kwargs)
        events.append("pending_marker")
        return result

    monkeypatch.setattr(scanner_module, "generate_sku", generate)
    monkeypatch.setattr(scanner_module, "process_images", process)
    monkeypatch.setattr(scanner_module, "write_scanned", write)

    rows = _stage_collection(collection, output, "ordered", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    with recovery_app.app_context():
        _operation("ordered")
        events.append("database_ingestion")
        ingest_rows_to_db(rows, log=quiet_log, operation_id="ordered")

        def record_finalization(stage, sku):
            events.append(stage)

        finalize_ingested_markers(
            catalogue_root,
            "ordered",
            log=quiet_log,
            failure_injector=record_finalization,
        )

    assert events == [
        "sku_index",
        "images",
        "pending_marker",
        "database_ingestion",
        "marker_replace",
        "update_remove",
        "pending_clear",
    ]


def test_pending_retry_reuses_parent_sku_without_advancing_collection_counter(
    tmp_path, quiet_log
):
    collection, _, output = _simple_collection(tmp_path)
    first = _stage_collection(collection, output, "interrupted-one", quiet_log)
    first_index = json.loads(
        (collection / "sku_index.json").read_text(encoding="utf-8")
    )

    retried = _stage_collection(collection, output, "interrupted-two", quiet_log)
    second_index = json.loads(
        (collection / "sku_index.json").read_text(encoding="utf-8")
    )

    assert [row["SKU"] for row in first] == ["FIC-S-0001"]
    assert [row["SKU"] for row in retried] == ["FIC-S-0001"]
    assert first_index == second_index == {"counter": 1}


def test_pending_retry_reuses_every_matching_variation_sku(
    tmp_path, quiet_log
):
    collection, product, output = _variable_collection(tmp_path)
    first = _stage_collection(collection, output, "variation-one", quiet_log)
    first_pending = load_pending_scanned(product, log=quiet_log)
    first_index = json.loads(
        (product / "sku_index.json").read_text(encoding="utf-8")
    )

    retried = _stage_collection(collection, output, "variation-two", quiet_log)
    second_pending = load_pending_scanned(product, log=quiet_log)
    second_index = json.loads(
        (product / "sku_index.json").read_text(encoding="utf-8")
    )

    assert [row["SKU"] for row in first] == [row["SKU"] for row in retried]
    assert first_pending["marker"]["variations"] == second_pending["marker"][
        "variations"
    ]
    assert first_index == second_index


def test_existing_valid_marker_survives_failed_update_staging(
    recovery_app, tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    initial = scan_collection(
        collection, "https://invalid.example/assets/", output, log=quiet_log
    )
    original_marker = (product / ".scanned").read_bytes()
    (product / ".update").write_text("fixture", encoding="utf-8")

    updated = _stage_collection(
        collection,
        output,
        "failed-update",
        quiet_log,
        update_csv=True,
    )
    _configure(recovery_app, collection.parent, output)
    with recovery_app.app_context():
        _operation("failed-update")

        def fail_update(stage, sku):
            if stage == "parent":
                raise RuntimeError("fixture update database failure")

        summary = ingest_rows_to_db(
            updated,
            log=quiet_log,
            operation_id="failed-update",
            failure_injector=fail_update,
        )
        outcome = finalize_ingested_markers(
            collection.parent, "failed-update", log=quiet_log
        )
        assert summary["products_failed"] == 1
        assert outcome["database_recovery_required"] == 1

    assert [row["SKU"] for row in updated] == [row["SKU"] for row in initial]
    assert (product / ".scanned").read_bytes() == original_marker
    assert (product / ".update").exists()
    assert (product / PENDING_FILE).exists()


def test_failure_before_database_records_recovery_and_retains_pending_identity(
    recovery_app, tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    catalogue_root = collection.parent
    rows = _stage_collection(collection, output, "before-db", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    with recovery_app.app_context():
        _operation("before-db")
        outcome = mark_pending_database_recovery(
            catalogue_root,
            "before-db",
            RuntimeError("fixture failure token=do-not-store"),
            log=quiet_log,
        )

        operation = db.session.get(CatalogueOperation, "before-db")
        item = CatalogueOperationItem.query.filter_by(
            operation_id="before-db"
        ).one()
        assert outcome["database_recovery_required"] == 1
        assert operation.recovery_state == "database_recovery_required"
        assert item.sku == rows[0]["SKU"]
        assert item.source_path == "Simple Collection/Blue Token"
        assert item.database_state == "not_started"
        assert item.marker_state == "database_recovery_required"
        assert "do-not-store" not in item.error
        assert "[REDACTED]" in item.error
    assert (product / PENDING_FILE).exists()
    assert (product / ".update").exists()
    assert not (product / ".scanned").exists()


def test_database_transaction_failure_retains_pending_and_marks_recovery(
    recovery_app, tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    catalogue_root = collection.parent
    rows = _stage_collection(collection, output, "db-failure", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    with recovery_app.app_context():
        _operation("db-failure")

        def fail_parent(stage, sku):
            if stage == "product_images":
                raise RuntimeError("fixture DB failure")

        summary = ingest_rows_to_db(
            rows,
            log=quiet_log,
            operation_id="db-failure",
            failure_injector=fail_parent,
        )
        outcome = finalize_ingested_markers(
            catalogue_root, "db-failure", log=quiet_log
        )

        item = CatalogueOperationItem.query.filter_by(
            operation_id="db-failure"
        ).one()
        assert summary["products_failed"] == 1
        assert outcome["database_recovery_required"] == 1
        assert item.database_state == "rolled_back"
        assert item.marker_state == "database_recovery_required"
        assert Product.query.count() == 0
    assert (product / PENDING_FILE).exists()
    assert (product / ".update").exists()
    assert not (product / ".scanned").exists()

    retried_rows = _stage_collection(
        collection, output, "db-retry", quiet_log
    )
    assert [row["SKU"] for row in retried_rows] == [rows[0]["SKU"]]
    with recovery_app.app_context():
        _operation("db-retry")
        retry_summary = ingest_rows_to_db(
            retried_rows, log=quiet_log, operation_id="db-retry"
        )
        retry_outcome = finalize_ingested_markers(
            catalogue_root, "db-retry", log=quiet_log
        )
        assert retry_summary["products_created"] == 1
        assert retry_outcome["finalized"] == 1
        assert Product.query.filter_by(sku=rows[0]["SKU"]).count() == 1
    assert (product / ".scanned").exists()
    assert not (product / PENDING_FILE).exists()


def test_database_success_then_marker_failure_records_marker_recovery(
    recovery_app, tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    catalogue_root = collection.parent
    rows = _stage_collection(collection, output, "marker-failure", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    with recovery_app.app_context():
        _operation("marker-failure")
        summary = ingest_rows_to_db(
            rows, log=quiet_log, operation_id="marker-failure"
        )

        def fail_marker(stage, sku):
            if stage == "marker_replace":
                raise OSError("fixture marker failure token=private")

        outcome = finalize_ingested_markers(
            catalogue_root,
            "marker-failure",
            log=quiet_log,
            failure_injector=fail_marker,
        )

        item = CatalogueOperationItem.query.filter_by(
            operation_id="marker-failure"
        ).one()
        assert summary["products_created"] == 1
        assert outcome["marker_recovery_required"] == 1
        assert item.database_state == "committed"
        assert item.marker_state == "marker_recovery_required"
        assert item.status == "recovery_required"
        assert "private" not in item.error
        assert "[REDACTED]" in item.error
        product_id = Product.query.filter_by(sku="FIC-S-0001").one().id
    assert (product / PENDING_FILE).exists()
    assert not (product / ".scanned").exists()

    with recovery_app.app_context():
        recovered = recover_committed_markers(catalogue_root, log=quiet_log)
        assert recovered["recovered"] == 1
        assert Product.query.filter_by(sku="FIC-S-0001").one().id == product_id
    assert (product / ".scanned").exists()
    assert not (product / PENDING_FILE).exists()


def test_update_removal_failure_keeps_pending_for_marker_recovery(
    recovery_app, tmp_path, quiet_log
):
    collection, product, output = _simple_collection(tmp_path)
    catalogue_root = collection.parent
    (product / ".update").write_text("fixture", encoding="utf-8")
    rows = _stage_collection(collection, output, "update-failure", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    with recovery_app.app_context():
        _operation("update-failure")
        ingest_rows_to_db(rows, log=quiet_log, operation_id="update-failure")

        def fail_update(stage, sku):
            if stage == "update_remove":
                raise OSError("fixture update removal failure")

        outcome = finalize_ingested_markers(
            catalogue_root,
            "update-failure",
            log=quiet_log,
            failure_injector=fail_update,
        )

        item = CatalogueOperationItem.query.filter_by(
            operation_id="update-failure"
        ).one()
        assert outcome["marker_recovery_required"] == 1
        assert item.marker_state == "marker_recovery_required"
    assert (product / ".scanned").exists()
    assert (product / ".update").exists()
    assert (product / PENDING_FILE).exists()


def test_interrupted_committed_pending_is_finalized_without_duplicate_database_rows(
    recovery_app, tmp_path, quiet_log
):
    collection, product, output = _variable_collection(tmp_path)
    catalogue_root = collection.parent
    rows = _stage_collection(collection, output, "interrupted", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    with recovery_app.app_context():
        _operation("interrupted")
        ingest_rows_to_db(rows, log=quiet_log, operation_id="interrupted")
        product_id = Product.query.filter_by(sku=rows[0]["SKU"]).one().id
        variation_ids = {
            variation.sku: variation.id for variation in Variation.query.all()
        }

        outcome = recover_committed_markers(catalogue_root, log=quiet_log)

        assert outcome["recovered"] == 1
        assert Product.query.one().id == product_id
        assert {
            variation.sku: variation.id for variation in Variation.query.all()
        } == variation_ids
        item = CatalogueOperationItem.query.filter_by(
            operation_id="interrupted"
        ).one()
        assert item.marker_state == "finalized"
    assert (product / ".scanned").exists()
    assert not (product / PENDING_FILE).exists()


def test_failed_parent_does_not_change_unrelated_successful_product_markers(
    recovery_app, tmp_path, quiet_log
):
    collection, first, output = _simple_collection(tmp_path)
    second = collection / "Green Token"
    shutil.copytree(first, second)
    _image(second / "green-token.png")
    catalogue_root = collection.parent
    rows = _stage_collection(collection, output, "two-products", quiet_log)
    _configure(recovery_app, catalogue_root, output)
    parent_skus = [row["SKU"] for row in rows if row["Type"] == "simple"]
    assert len(parent_skus) == 2
    with recovery_app.app_context():
        _operation("two-products")

        def fail_second(stage, sku):
            if stage == "product_attributes" and sku == parent_skus[1]:
                raise RuntimeError("fixture second failure")

        summary = ingest_rows_to_db(
            rows,
            log=quiet_log,
            operation_id="two-products",
            failure_injector=fail_second,
        )
        outcome = finalize_ingested_markers(
            catalogue_root, "two-products", log=quiet_log
        )

        assert summary["products_created"] == 1
        assert summary["products_failed"] == 1
        assert outcome["finalized"] == 1
        assert outcome["database_recovery_required"] == 1
    assert (first / ".scanned").exists()
    assert not (first / PENDING_FILE).exists()
    assert (second / PENDING_FILE).exists()
    assert not (second / ".scanned").exists()


def test_scan_runner_completes_database_then_marker_finalization_end_to_end(
    recovery_app, tmp_path
):
    collection, product, output = _simple_collection(tmp_path)
    _configure(recovery_app, collection.parent, output)
    run_id = "end-to-end-marker-finalization"

    operation_id = start_scan(recovery_app, run_id, scan_mode="append")
    deadline = time.monotonic() + 10
    while get_progress(run_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert get_progress(run_id)["status"] == "done"
    with recovery_app.app_context():
        operation = db.session.get(CatalogueOperation, operation_id)
        item = CatalogueOperationItem.query.filter_by(
            operation_id=operation_id
        ).one()
        assert operation.status == "succeeded"
        assert operation.marker_state == "finalized"
        assert operation.recovery_state == "none"
        assert operation.products_succeeded == 1
        assert item.database_state == "committed"
        assert item.marker_state == "finalized"
        assert Product.query.filter_by(sku="FIC-S-0001").one()
    assert (product / ".scanned").exists()
    assert not (product / PENDING_FILE).exists()
    assert not (product / ".update").exists()
