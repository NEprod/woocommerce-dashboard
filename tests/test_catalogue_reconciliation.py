import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from app import create_app, db
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Collection,
    Product,
    ProductAsset,
    Settings,
    Variation,
)
from app.utils.ingest import ingest_rows_to_db
from app.utils.reconciliation import (
    authoritative_scope,
    reconcile_authoritative_products,
)
from app.utils.scan_runner import build_scan_scope
from app.utils.operation_control import finish_catalogue_operation
from app.utils.scanner import scan_collection
from config import Config


FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"


@pytest.fixture
def reconciliation_app(tmp_path):
    database = tmp_path / "reconciliation.db"
    catalogue = tmp_path / "catalogue"
    collection = catalogue / "Fictional Collection"
    collection.mkdir(parents=True)
    (collection / "product_info.json").write_text(
        json.dumps(
            {
                "collection_type": "Variable Collection",
                "sku_prefix": "FIC-R-",
                "title": "Shared title",
                "regular_price": "10.00",
            }
        ),
        encoding="utf-8",
    )

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, LOGIN_DISABLED=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue),
                    output_folder=str(tmp_path / "output"),
                    url_prefix="https://invalid.example/",
                )
            )
            db.session.commit()
        yield app, catalogue
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def _add_product_folder(catalogue, name, sku):
    folder = catalogue / "Fictional Collection" / name
    folder.mkdir(exist_ok=True)
    (folder / "product_info.json").write_text(
        json.dumps({"title": f"{name} override"}), encoding="utf-8"
    )
    (folder / ".scanned").write_text(json.dumps({"sku": sku}), encoding="utf-8")
    return folder


def _parent(sku, *, row_type="variable", title="Fixture product"):
    return {
        "Type": row_type,
        "SKU": sku,
        "Name": title,
        "Published": "1",
        "Regular price": "10.00",
        "Images": "",
        "Categories": "",
        "Tags": "",
        "Attribute 1 name": "Size" if row_type == "variable" else "",
        "Attribute 1 value(s)": "Small, Large" if row_type == "variable" else "",
    }


def _variation(parent_sku, sku, value):
    row = _parent(parent_sku)
    row.update(
        {
            "Type": "variation",
            "SKU": sku,
            "Parent": parent_sku,
            "Attribute 1 name": "Size",
            "Attribute 1 value(s)": value,
        }
    )
    return row


def _operation(operation_id, operation_type="append", scope=None):
    db.session.add(
        CatalogueOperation(
            id=operation_id,
            operation_type=operation_type,
            status="running",
            scope=json.dumps(scope or {}),
        )
    )
    db.session.commit()


def test_removed_variation_becomes_missing_and_reappearance_restores_identity(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    sku = "FIC-R-0001"
    _add_product_folder(catalogue, "First Product", sku)
    with app.app_context():
        ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small"), _variation(sku, f"{sku}-2", "Large")],
            log=lambda *args, **kwargs: None,
        )
        removed = Variation.query.filter_by(sku=f"{sku}-2").one()
        removed.woo_id = 7702
        removed.woo_synced_at = datetime(2026, 4, 5, 6, 7, 8)
        db.session.commit()
        identity = (removed.id, removed.sku, removed.woo_id, removed.woo_synced_at)

        summary = ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small")],
            log=lambda *args, **kwargs: None,
        )
        db.session.refresh(removed)
        assert removed.catalogue_status == "missing"
        assert removed.missing_at is not None
        assert summary["variations_missing"] == 1

        summary = ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small"), _variation(sku, f"{sku}-2", "Large")],
            log=lambda *args, **kwargs: None,
        )
        restored = Variation.query.filter_by(sku=f"{sku}-2").one()
        assert (restored.id, restored.sku, restored.woo_id, restored.woo_synced_at) == identity
        assert restored.catalogue_status == "active"
        assert restored.missing_at is None
        assert restored.restored_at is not None
        assert summary["variations_restored"] == 1


def test_variable_becoming_simple_marks_all_former_variations_missing(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    sku = "FIC-R-0001"
    _add_product_folder(catalogue, "First Product", sku)
    with app.app_context():
        ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small")],
            log=lambda *args, **kwargs: None,
        )
        ingest_rows_to_db(
            [_parent(sku, row_type="simple")], log=lambda *args, **kwargs: None
        )
        product = Product.query.filter_by(sku=sku).one()
        assert product.product_type == "simple"
        assert [(row.sku, row.catalogue_status) for row in product.variations] == [
            (f"{sku}-1", "missing")
        ]


def test_new_variation_does_not_disturb_existing_identity(reconciliation_app):
    app, catalogue = reconciliation_app
    sku = "FIC-R-0001"
    _add_product_folder(catalogue, "First Product", sku)
    with app.app_context():
        ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small")],
            log=lambda *args, **kwargs: None,
        )
        existing = Variation.query.filter_by(sku=f"{sku}-1").one()
        existing.woo_id = 7711
        db.session.commit()
        identity = (existing.id, existing.sku, existing.woo_id)

        ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small"), _variation(sku, f"{sku}-2", "Large")],
            log=lambda *args, **kwargs: None,
        )
        existing = Variation.query.filter_by(sku=f"{sku}-1").one()
        assert (existing.id, existing.sku, existing.woo_id) == identity
        assert Variation.query.filter_by(sku=f"{sku}-2").one().catalogue_status == "active"


def test_reconciliation_failure_rolls_back_missing_variation_change(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    sku = "FIC-R-0001"
    _add_product_folder(catalogue, "First Product", sku)
    with app.app_context():
        ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small"), _variation(sku, f"{sku}-2", "Large")],
            log=lambda *args, **kwargs: None,
        )

        def fail(stage, affected_sku):
            if stage == "variation_reconciliation":
                raise RuntimeError("fixture reconciliation failure token=private")

        summary = ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small")],
            log=lambda *args, **kwargs: None,
            failure_injector=fail,
        )
        assert summary["products_failed"] == 1
        assert {
            row.sku: row.catalogue_status for row in Variation.query.order_by(Variation.sku)
        } == {f"{sku}-1": "active", f"{sku}-2": "active"}


def test_authoritative_product_reconciliation_is_scope_limited_and_reversible(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    first_sku = "FIC-R-0001"
    second_sku = "FIC-R-0002"
    _add_product_folder(catalogue, "First Product", first_sku)
    _add_product_folder(catalogue, "Second Product", second_sku)
    with app.app_context():
        ingest_rows_to_db(
            [_parent(first_sku), _parent(second_sku)],
            log=lambda *args, **kwargs: None,
        )
        second = Product.query.filter_by(sku=second_sku).one()
        second.woo_id = 8802
        db.session.commit()
        identity = (second.id, second.sku, second.woo_id, second.created_at)
        _operation("full-scope", "full", {"scope_kind": "catalogue", "exhaustive": True})

        outcome = reconcile_authoritative_products(
            authoritative_scope("full", seen_source_relpaths={"Fictional Collection/First Product"}),
            operation_id="full-scope",
        )
        db.session.refresh(second)
        assert outcome == {"products_missing": 1}
        assert second.catalogue_status == "missing"
        assert (second.id, second.sku, second.woo_id, second.created_at) == identity

        # Portable source identity wins over an incoming replacement SKU.
        summary = ingest_rows_to_db(
            [_parent("FIC-R-9999", title="Restored")],
            log=lambda *args, **kwargs: None,
            source_folders={"FIC-R-9999": str(catalogue / "Fictional Collection" / "Second Product")},
        )
        restored = db.session.get(Product, identity[0])
        assert restored.sku == second_sku
        assert restored.woo_id == 8802
        assert restored.catalogue_status == "active"
        assert restored.missing_at is None
        assert summary["products_restored"] == 1


def test_non_authoritative_and_failed_scopes_never_mark_products_missing(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    sku = "FIC-R-0001"
    _add_product_folder(catalogue, "First Product", sku)
    with app.app_context():
        ingest_rows_to_db([_parent(sku)], log=lambda *args, **kwargs: None)
        for mode in ("append", "product_update"):
            scope = authoritative_scope(mode, seen_source_relpaths=set())
            assert scope is None
        assert reconcile_authoritative_products(None) == {"products_missing": 0}
        assert Product.query.filter_by(sku=sku).one().catalogue_status == "active"

    missing = catalogue / "not-mounted"
    plan = build_scan_scope(str(missing), "full")
    assert plan.complete is False
    assert plan.authoritative is False
    empty_mount = catalogue / "empty-mounted-catalogue"
    empty_mount.mkdir()
    plan = build_scan_scope(str(empty_mount), "full")
    assert plan.complete is False
    assert plan.authoritative is False


def test_failed_authoritative_reconciliation_rolls_back_every_missing_change(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    _add_product_folder(catalogue, "First Product", "FIC-R-0001")
    _add_product_folder(catalogue, "Second Product", "FIC-R-0002")
    with app.app_context():
        ingest_rows_to_db(
            [_parent("FIC-R-0001"), _parent("FIC-R-0002")],
            log=lambda *args, **kwargs: None,
        )

        def fail(stage, _sku):
            if stage == "product_reconciliation":
                raise RuntimeError("fixture authoritative failure")

        with pytest.raises(RuntimeError, match="authoritative failure"):
            reconcile_authoritative_products(
                authoritative_scope(
                    "full",
                    seen_source_relpaths={"Fictional Collection/First Product"},
                ),
                failure_injector=fail,
            )
        assert {
            row.sku: row.catalogue_status for row in Product.query.order_by(Product.sku)
        } == {"FIC-R-0001": "active", "FIC-R-0002": "active"}


def test_shared_collection_plan_refreshes_every_child_and_excludes_unrelated(
    reconciliation_app,
):
    _app, catalogue = reconciliation_app
    target = catalogue / "Fictional Collection"
    (target / "First Product").mkdir()
    (target / "Second Product").mkdir()
    unrelated = catalogue / "Unrelated Collection"
    unrelated.mkdir()
    (unrelated / "product_info.json").write_text(
        json.dumps({"collection_type": "Simple", "sku_prefix": "OTHER-"}),
        encoding="utf-8",
    )
    (unrelated / "Other Product").mkdir()

    plan = build_scan_scope(
        str(catalogue),
        "shared_collection",
        collection_relpath="Fictional Collection",
    )
    assert plan.complete is True
    assert plan.authoritative is True
    assert plan.collection_paths == (str(target),)
    assert plan.seen_source_relpaths == {
        "Fictional Collection/First Product",
        "Fictional Collection/Second Product",
    }


def _fixture_image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 3), "#663399").save(path)


@pytest.mark.parametrize(
    "collection_name", ["Simple Collection", "Variable Collection", "Single Variable"]
)
def test_shared_refresh_reuses_all_parent_and_variation_skus(
    tmp_path, quiet_log, collection_name
):
    collection = tmp_path / "catalogue" / collection_name
    shutil.copytree(FIXTURES / collection_name, collection)
    output = tmp_path / "output"
    output.mkdir()
    if collection_name == "Simple Collection":
        _fixture_image(collection / "Blue Token" / "token.png")
    elif collection_name == "Variable Collection":
        _fixture_image(collection / "Badge One" / "badge.png")
    else:
        _fixture_image(collection / "parent" / "parent.png")
        for theme in ("Moon", "Sun"):
            _fixture_image(collection / theme / "theme.png")
            for size in ("Mini", "Maxi"):
                _fixture_image(collection / theme / size / f"{theme}-{size}.png")

    initial = scan_collection(
        collection,
        "https://invalid.example/",
        output,
        log=quiet_log,
    )
    initial_skus = [row["SKU"] for row in initial]
    shared_path = collection / "product_info.json"
    shared = json.loads(shared_path.read_text(encoding="utf-8"))
    shared["short_description"] = "Changed shared fixture value"
    shared_path.write_text(json.dumps(shared), encoding="utf-8")

    refreshed = scan_collection(
        collection,
        "https://invalid.example/",
        output,
        force_update=True,
        update_csv=True,
        log=quiet_log,
    )
    assert [row["SKU"] for row in refreshed] == initial_skus
    assert all(
        row["Short description"] == "Changed shared fixture value"
        for row in refreshed
        if row["Type"] in ("variable", "simple")
    )
    if collection_name == "Simple Collection":
        assert refreshed[0]["Name"] == "Blue Token - Fictional Desk Token"
        assert set(refreshed[0]["Tags"].split(", ")) == {
            "baseline",
            "shared",
            "override",
        }


def test_shared_editor_starts_explicit_exhaustive_collection_refresh(
    reconciliation_app, monkeypatch
):
    app, catalogue = reconciliation_app
    shared_path = catalogue / "Fictional Collection" / "product_info.json"
    captured = {}
    with app.app_context():
        collection = Collection(
            name="Fictional Collection",
            root_path=str(shared_path.parent),
            sku_prefix="FIC-R-",
            shared_json_path=str(shared_path),
            collection_type="Variable Collection",
            source_relpath="Fictional Collection",
            shared_json_relpath="Fictional Collection/product_info.json",
        )
        product = Product(
            sku="FIC-R-0001",
            title="First Product",
            collection=collection,
            source_relpath="Fictional Collection/First Product",
        )
        db.session.add_all([collection, product])
        db.session.flush()
        db.session.add(
            ProductAsset(
                product_id=product.id,
                path=str(shared_path),
                source_relpath="Fictional Collection/product_info.json",
                kind="info",
                label="shared",
            )
        )
        db.session.commit()

    def capture_start(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.routes.start_scan", capture_start)
    response = app.test_client().post(
        "/edit_products/FIC-R-0001/save",
        json={"kind": "shared", "data": {"regular_price": "12.00"}},
    )
    assert response.status_code == 200
    assert captured["scan_mode"] == "shared_collection"
    assert captured["collection_relpath"] == "Fictional Collection"
    assert captured["scope"] == {
        "sku": "FIC-R-0001",
        "kind": "shared",
        "scope_kind": "collection",
        "collection_relpath": "Fictional Collection",
        "exhaustive": True,
    }
    assert not (shared_path.parent / ".update").exists()
    with app.app_context():
        finish_catalogue_operation(
            captured["operation_id"], status="succeeded"
        )


def test_operation_items_record_missing_and_restored_lifecycle_counts(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    sku = "FIC-R-0001"
    _add_product_folder(catalogue, "First Product", sku)
    with app.app_context():
        ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small"), _variation(sku, f"{sku}-2", "Large")],
            log=lambda *args, **kwargs: None,
        )
        Variation.query.filter_by(sku=f"{sku}-1").update(
            {"catalogue_status": "missing", "missing_at": datetime(2026, 1, 1)}
        )
        db.session.commit()
        _operation("history-operation")

        summary = ingest_rows_to_db(
            [_parent(sku), _variation(sku, f"{sku}-1", "Small")],
            operation_id="history-operation",
            log=lambda *args, **kwargs: None,
        )
        item = CatalogueOperationItem.query.filter_by(
            operation_id="history-operation", sku=sku
        ).one()
        assert summary["variations_missing"] == 1
        assert summary["variations_restored"] == 1
        assert item.variations_missing == 1
        assert item.variations_restored == 1
        assert item.product_restored is False


def test_collection_scope_missing_reconciliation_leaves_other_collection_untouched(
    reconciliation_app,
):
    app, catalogue = reconciliation_app
    _add_product_folder(catalogue, "First Product", "FIC-R-0001")
    _add_product_folder(catalogue, "Second Product", "FIC-R-0002")
    with app.app_context():
        ingest_rows_to_db(
            [_parent("FIC-R-0001"), _parent("FIC-R-0002")],
            log=lambda *args, **kwargs: None,
        )
        unrelated_collection = Collection(
            name="Unrelated",
            root_path=str(catalogue / "Unrelated"),
            sku_prefix="UNRELATED-",
            shared_json_path=str(catalogue / "Unrelated" / "product_info.json"),
            source_relpath="Unrelated",
        )
        unrelated = Product(
            sku="UNRELATED-0001",
            title="Unrelated",
            collection=unrelated_collection,
            source_relpath="Unrelated/Product",
        )
        db.session.add_all([unrelated_collection, unrelated])
        db.session.commit()

        outcome = reconcile_authoritative_products(
            authoritative_scope(
                "shared_collection_update",
                seen_source_relpaths={"Fictional Collection/First Product"},
                collection_source_relpath="Fictional Collection",
            )
        )
        assert outcome == {"products_missing": 1}
        assert Product.query.filter_by(sku="FIC-R-0002").one().catalogue_status == "missing"
        assert Product.query.filter_by(sku="UNRELATED-0001").one().catalogue_status == "active"
