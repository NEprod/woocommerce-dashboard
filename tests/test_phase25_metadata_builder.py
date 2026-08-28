import hashlib
import json
import time
from datetime import datetime
from html.parser import HTMLParser

import pytest
from PIL import Image

from app import create_app, db
from app.intake_metadata_builder import (
    COLLECTION_TYPES,
    METADATA_FILENAME,
    SUPPORTED_FIELDS,
    eligible_metadata_results,
    metadata_preview,
)
from app.models import CatalogueOperation, User
from config import Config


def _image(path, colour=(40, 100, 140)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 8), colour).save(path)


def _scope(relative, state="metadata_required", operation_type="intake_image_rename"):
    summary = {
        "source_relpath": relative,
        "prepared_relpath": relative,
        "workflow_status": state,
        "source_images": 3,
        "copied_images": 3,
        "failed_images": 0,
    }
    return operation_type, json.dumps(
        {"source_relpath": relative, "workflow_status": state, "operation_summary": summary},
        separators=(",", ":"),
    )


@pytest.fixture
def metadata_app(tmp_path):
    from app.intake_grouping import reset_intake_operation_control_for_tests

    instance = tmp_path / "instance"
    intake = tmp_path / "intake"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    for folder in (instance, intake, catalogue, output):
        folder.mkdir()
    simple = intake / "Prepared" / "Simple Cards"
    _image(simple / "Parent" / "cards_01.png")
    variable = intake / "Prepared" / "Variable Cards"
    _image(variable / "Hero A" / "cards_hero_a_01.jpg")
    _image(variable / "Hero B" / "cards_hero_b_01.png")
    single = intake / "Prepared" / "Single Variable Cards"
    _image(single / "Parent" / "cards_parent_01.png")
    _image(single / "Hero A" / "A5" / "cards_hero_a_a5_01.png")
    _image(single / "Hero B" / "A4" / "cards_hero_b_a4_01.jpg")
    loose = intake / "Loose"
    _image(loose / "loose.png")

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance / 'site.db'}"
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        INTAKE_ROOT=str(intake),
        INTAKE_TEST_MOUNTED=True,
        INTAKE_MUTATION_LOCK_PATH=str(instance / "intake.lock"),
        DISCORD_ENABLED=False,
    )
    with app.app_context():
        db.session.add(User(email="metadata@example.test", username="metadata-admin", password="unused"))
        for index, name in enumerate(("Simple Cards", "Variable Cards", "Single Variable Cards"), start=1):
            operation_type, scope = _scope(f"Prepared/{name}")
            db.session.add(
                CatalogueOperation(
                    id=str(index) * 32,
                    operation_type=operation_type,
                    status="succeeded",
                    scope=scope,
                    started_at=datetime(2026, 1, index),
                    finished_at=datetime(2026, 1, index, 0, 1),
                )
            )
        db.session.commit()
    reset_intake_operation_control_for_tests()
    try:
        yield app, intake, catalogue, output
    finally:
        with app.app_context():
            db.session.remove()
        reset_intake_operation_control_for_tests()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


@pytest.fixture
def metadata_client(metadata_app):
    app, *_ = metadata_app
    client = app.test_client()
    with app.app_context():
        user_id = User.query.one().id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _document(collection_type="Simple"):
    return {
        "collection_type": collection_type,
        "title": "Shared Cards",
        "sku_prefix": "CARD-",
        "live": False,
        "categories": ["Cards", "Handmade"],
        "tags": ["birthday", "crochet"],
    }


def _wait(app, operation_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id)
            if row and row.status in {"succeeded", "partial", "failed", "interrupted"}:
                return row
        time.sleep(0.02)
    raise AssertionError("metadata operation did not finish")


def test_eligibility_uses_durable_workflow_state(metadata_app):
    app, intake, *_ = metadata_app
    with app.app_context():
        results = eligible_metadata_results(intake)
        assert [item["name"] for item in results] == ["Simple Cards", "Single Variable Cards", "Variable Cards"]
        for path in ("Loose", "Prepared", "../Prepared/Simple Cards", ".catalogue-intake-staging/x"):
            with pytest.raises(ValueError):
                metadata_preview(intake, path, _document())


def test_supported_inventory_and_collection_types_match_contract():
    assert COLLECTION_TYPES == ("Simple", "Variable Collection", "Single Variable")
    assert SUPPORTED_FIELDS == (
        "collection_type", "title", "sku_prefix", "price", "sale_price",
        "sale_start_date", "sale_end_date", "weight", "dimensions", "categories",
        "tags", "live", "short_description", "description", "attributes",
        "image_attributes", "variation_modifiers", "shipping_class", "grouped_ids",
        "grouped_products", "upsell_ids", "cross_sell_ids", "upsells", "crosssells",
        "meta_title", "meta_description",
    )


@pytest.mark.parametrize(
    ("path", "document"),
    [
        ("Prepared/Simple Cards", _document("Simple")),
        ("Prepared/Variable Cards", _document("Variable Collection")),
        (
            "Prepared/Single Variable Cards",
            {
                **_document("Single Variable"),
                "attributes": {"Style": ["Hero A", "Hero B"], "Size": ["A5", "A4"]},
                "image_attributes": ["Style", "Size"],
                "variation_modifiers": {"Size=A5": {"price": "12.00"}},
            },
        ),
    ],
)
def test_guided_documents_validate_and_preview_exact_json(metadata_app, path, document):
    app, intake, *_ = metadata_app
    with app.app_context():
        preview = metadata_preview(intake, path, document)
    assert preview["ready"]
    assert json.loads(preview["json_text"]) == document
    assert preview["json_text"].endswith("\n")
    assert preview["document"]["live"] is False
    assert not any(value.startswith("/") for value in preview["document"].values() if isinstance(value, str))


def test_semantic_validation_reports_blocking_errors_and_warnings(metadata_app):
    app, intake, *_ = metadata_app
    invalid = {
        **_document("Unknown"),
        "sku_prefix": "bad/path",
        "price": "10",
        "sale_price": "12",
        "sale_start_date": "2026-03-02",
        "sale_end_date": "2026-03-01",
        "attributes": {"Style": ["Hero A", "hero a"]},
        "image_attributes": ["Unknown", "unknown"],
        "variation_modifiers": {"Size=A5": {"price": "1"}},
    }
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", invalid)
    codes = {item["code"] for item in preview["findings"]}
    assert {"invalid_collection_type", "invalid_sku_prefix", "invalid_sale_dates", "duplicate_attribute_value", "duplicate_image_attribute", "unknown_image_attribute", "invalid_modifier_reference"} <= codes
    assert "sale_above_price" in codes
    assert not preview["ready"]


def test_single_variable_parent_case_hierarchy_and_count(metadata_app):
    app, intake, *_ = metadata_app
    document = {
        **_document("Single Variable"),
        "attributes": {"Style": ["Hero A", "Hero B"], "Size": ["A5", "A4"]},
        "image_attributes": ["Style", "Size"],
    }
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Single Variable Cards", document)
    assert preview["analysis"]["parent_folders"] == ["Parent"]
    assert "Parent" not in preview["analysis"]["product_folders"]
    assert preview["analysis"]["expected_variations"] == 4
    assert preview["analysis"]["visible_variations"] == 2
    assert preview["analysis"]["image_health"] == {"exact": 2, "fallback": 2, "missing": 0}
    assert any(item["code"] == "image_fallback_parent" for item in preview["warnings"])


def test_variable_title_preview_uses_existing_product_json_without_editing_it(metadata_app, metadata_client):
    app, intake, *_ = metadata_app
    product_json = intake / "Prepared" / "Variable Cards" / "Hero A" / METADATA_FILENAME
    authored_override = {"title": "Product-specific Hero"}
    product_json.write_text(json.dumps(authored_override, ensure_ascii=False), encoding="utf-8")
    before = product_json.read_bytes()
    document = _document("Variable Collection")
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Variable Cards", document)
    titles = {item["folder"]: item["title"] for item in preview["analysis"]["title_previews"]}
    assert titles["Hero A"] == "Product-specific Hero - Shared Cards"
    assert titles["Hero B"] == "Hero B - Shared Cards"
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    assert _wait(app, operation_id).status in {"succeeded", "partial"}
    assert product_json.read_bytes() == before


def test_duplicate_parent_variants_are_blocked(metadata_app):
    app, intake, *_ = metadata_app
    document = {**_document("Single Variable"), "attributes": {"Style": ["Hero A"]}, "image_attributes": ["Style"]}
    from app.intake_metadata_builder import _folder_analysis
    snapshot = {
        "folder": intake / "Prepared" / "Single Variable Cards",
        "folders": ["Parent", "parent", "Hero A"],
        "images": [
            {"path": "Parent/one.png"},
            {"path": "parent/two.png"},
            {"path": "Hero A/variation.png"},
        ],
    }
    analysis = _folder_analysis(snapshot, document)
    assert any(item["code"] == "duplicate_parent" for item in analysis["findings"])


def test_digest_is_deterministic_and_changes_for_document_folder_image_and_state(metadata_app):
    app, intake, *_ = metadata_app
    with app.app_context():
        first = metadata_preview(intake, "Prepared/Simple Cards", _document())
        second = metadata_preview(intake, "Prepared/Simple Cards", _document())
        changed = metadata_preview(intake, "Prepared/Simple Cards", {**_document(), "title": "Changed"})
    assert first["digest"] == second["digest"]
    assert first["digest"] != changed["digest"]
    _image(intake / "Prepared" / "Simple Cards" / "Parent" / "cards_02.png", (1, 2, 3))
    with app.app_context():
        image_changed = metadata_preview(intake, "Prepared/Simple Cards", _document())
    assert image_changed["digest"] != first["digest"]


def test_existing_valid_and_malformed_json_handling(metadata_app):
    app, intake, *_ = metadata_app
    path = intake / "Prepared" / "Simple Cards" / METADATA_FILENAME
    existing = {**_document(), "future_supported_field": {"keep": True}}
    path.write_text(json.dumps(existing), encoding="utf-8")
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards")
    assert preview["document"]["future_supported_field"] == {"keep": True}
    assert any(item["code"] == "unknown_field" for item in preview["warnings"])
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()
    with app.app_context():
        malformed = metadata_preview(intake, "Prepared/Simple Cards")
    assert not malformed["ready"]
    assert path.read_bytes() == before


def test_routes_require_authentication_csrf_and_render_without_unsupported_actions(metadata_app, metadata_client):
    app, *_ = metadata_app
    anonymous = app.test_client()
    assert anonymous.get("/image-preparation/metadata").status_code in {302, 401}
    assert anonymous.post("/image-preparation/metadata/preview").status_code in {302, 401}
    app.config["WTF_CSRF_ENABLED"] = True
    assert metadata_client.post("/image-preparation/metadata/preview", data={}).status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
    page = metadata_client.get("/image-preparation/metadata")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Create Product Metadata" in body
    assert "Copy to Catalogue" not in body
    assert "Append Scan" not in body


def test_preview_route_and_safe_same_name_save(metadata_app, metadata_client):
    app, intake, catalogue, output = metadata_app
    source_before = (intake / "Prepared" / "Simple Cards" / "Parent" / "cards_01.png").read_bytes()
    catalogue_before = list(catalogue.rglob("*"))
    output_before = list(output.rglob("*"))
    document = _document()
    preview_response = metadata_client.post(
        "/image-preparation/metadata/preview",
        data={"path": "Prepared/Simple Cards", "document": json.dumps(document)},
    )
    assert preview_response.status_code == 200
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", document)
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={
            "path": preview["source"],
            "document": preview["json_text"],
            "digest": preview["digest"],
            "acknowledge": "yes",
        },
    )
    assert response.status_code == 302
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    row = _wait(app, operation_id)
    assert row.status in {"succeeded", "partial"}
    result = intake / "Prepared" / "Simple Cards"
    assert json.loads((result / METADATA_FILENAME).read_text(encoding="utf-8")) == document
    assert (result / "Parent" / "cards_01.png").read_bytes() == source_before
    assert not (intake / "Prepared" / "Simple Cards (2)").exists()
    assert not list(result.glob("product_info.json.*"))
    assert list(catalogue.rglob("*")) == catalogue_before
    assert list(output.rglob("*")) == output_before
    with app.app_context():
        scope = json.loads(db.session.get(CatalogueOperation, operation_id).scope)
    summary = scope["operation_summary"]
    assert summary["workflow_status"] == "validation_required"
    assert summary["rollback_state"] == "removed_after_verification"
    assert summary["metadata_action"] == "create"
    assert "description" not in json.dumps(summary)


def test_duplicate_submission_is_blocked_and_result_can_be_reopened(metadata_app, metadata_client):
    app, intake, *_ = metadata_app
    document = _document()
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", document)
    data = {"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"}
    first = metadata_client.post("/image-preparation/metadata/confirm", data=data)
    operation_id = first.headers["Location"].rstrip("/").split("/")[-1]
    _wait(app, operation_id)
    stale = metadata_client.post("/image-preparation/metadata/confirm", data=data, follow_redirects=True)
    assert b"changed after preview" in stale.data
    with app.app_context():
        results = eligible_metadata_results(intake)
    selected = next(item for item in results if item["name"] == "Simple Cards")
    assert selected["action"] == "Edit Product Metadata"
    assert selected["workflow_status"] == "validation_required"


def test_read_only_intake_allows_preview_but_not_save(metadata_app, metadata_client):
    app, intake, *_ = metadata_app
    document = _document()
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", document)
    app.config["INTAKE_TEST_MOUNTED"] = True
    import app.intake_metadata_builder as builder
    original = builder.intake_readiness
    builder.intake_readiness = lambda: {"readable": True, "writable": False}
    try:
        response = metadata_client.post(
            "/image-preparation/metadata/confirm",
            data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
            follow_redirects=True,
        )
    finally:
        builder.intake_readiness = original
    assert b"mounted read/write" in response.data
    assert not (intake / "Prepared" / "Simple Cards" / METADATA_FILENAME).exists()


def test_operation_metadata_does_not_store_full_json_or_absolute_paths(metadata_app, metadata_client):
    app, intake, *_ = metadata_app
    document = {**_document(), "description": "CONFIDENTIAL-DESCRIPTION-DO-NOT-PERSIST"}
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", document)
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    _wait(app, operation_id)
    with app.app_context():
        row = db.session.get(CatalogueOperation, operation_id)
        retained = (row.scope or "") + (row.error or "")
    assert "CONFIDENTIAL-DESCRIPTION" not in retained
    assert str(intake) not in retained
    assert len(retained) < 6000


def test_existing_metadata_is_updated_without_visible_backup_clutter(metadata_app, metadata_client):
    app, intake, *_ = metadata_app
    target = intake / "Prepared" / "Simple Cards" / METADATA_FILENAME
    target.write_text(json.dumps(_document()), encoding="utf-8")
    changed = {**_document(), "title": "Corrected shared title", "live": True}
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", changed)
    assert preview["action"] == "update"
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    row = _wait(app, operation_id)
    assert row.status in {"succeeded", "partial"}
    assert json.loads(target.read_text(encoding="utf-8")) == changed
    assert target.read_bytes().endswith(b"\n")
    assert target.stat().st_mode & 0o777 == 0o600
    assert [path.name for path in target.parent.glob("product_info.json*")] == [METADATA_FILENAME]


def test_staging_failure_leaves_visible_result_unchanged(metadata_app, metadata_client, monkeypatch):
    app, intake, *_ = metadata_app
    image = intake / "Prepared" / "Simple Cards" / "Parent" / "cards_01.png"
    original = image.read_bytes()
    import app.intake_metadata_builder as builder
    monkeypatch.setattr(builder, "_write_metadata", lambda *_args: (_ for _ in ()).throw(builder.MetadataProposalRejected("injected write failure")))
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", _document())
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    row = _wait(app, operation_id)
    assert row.status == "failed"
    assert image.read_bytes() == original
    assert not (image.parents[1] / METADATA_FILENAME).exists()
    assert not (intake / "Prepared" / "Simple Cards (2)").exists()


def test_post_promotion_failure_restores_original(metadata_app, metadata_client, monkeypatch):
    app, intake, *_ = metadata_app
    target = intake / "Prepared" / "Simple Cards" / METADATA_FILENAME
    original_document = _document()
    target.write_text(json.dumps(original_document), encoding="utf-8")
    import app.intake_metadata_builder as builder
    original_verify = builder._verify_result
    calls = {"count": 0}

    def injected(folder, preview):
        calls["count"] += 1
        if calls["count"] == 2:
            raise builder.MetadataProposalRejected("injected promoted verification failure")
        return original_verify(folder, preview)

    monkeypatch.setattr(builder, "_verify_result", injected)
    changed = {**original_document, "title": "Must roll back"}
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", changed)
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    row = _wait(app, operation_id)
    assert row.status == "failed"
    assert json.loads(target.read_text(encoding="utf-8")) == original_document


def test_operation_detail_exposes_metadata_summary_and_next_step(metadata_app, metadata_client):
    app, intake, *_ = metadata_app
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", _document())
    response = metadata_client.post(
        "/image-preparation/metadata/confirm",
        data={"path": preview["source"], "document": preview["json_text"], "digest": preview["digest"], "acknowledge": "yes"},
    )
    operation_id = response.headers["Location"].rstrip("/").split("/")[-1]
    _wait(app, operation_id)
    page = metadata_client.get(f"/operations/{operation_id}")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Catalogue Intake — Save Metadata" in body
    assert "Metadata complete — validation required" in body
    assert "Validate prepared collection" in body
    assert "Edit Product Metadata" in body
    assert preview["json_text"] not in body


def test_preview_does_not_send_discord_or_write_files(metadata_app, monkeypatch):
    app, intake, *_ = metadata_app
    import app.utils.discord as discord
    calls = []
    monkeypatch.setattr(discord, "send_discord_message", lambda *args, **kwargs: calls.append((args, kwargs)))
    with app.app_context():
        preview = metadata_preview(intake, "Prepared/Simple Cards", _document())
    assert preview["ready"]
    assert calls == []
    assert not (intake / "Prepared" / "Simple Cards" / METADATA_FILENAME).exists()


class _SemanticParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.h1 = 0
        self.main = 0
        self.labels = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "h1":
            self.h1 += 1
        elif tag == "main":
            self.main += 1
        elif tag == "label":
            self.labels += 1


def test_rendered_editor_semantics_responsive_hooks_and_supported_boundaries(metadata_app, metadata_client):
    document = {
        **_document("Single Variable"),
        "attributes": {"Style": ["Hero A", "Hero B"], "Size": ["A5", "A4"]},
        "image_attributes": ["Style", "Size"],
        "variation_modifiers": {"Size=A5": {"price": "12.00"}},
    }
    response = metadata_client.post(
        "/image-preparation/metadata/preview",
        data={"path": "Prepared/Single Variable Cards", "document": json.dumps(document)},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    parser = _SemanticParser()
    parser.feed(body)
    assert parser.h1 == 1
    assert parser.main == 1
    assert len(parser.ids) == len(set(parser.ids))
    assert parser.labels >= 20
    for required in (
        "Identity", "Product Copy", "Pricing and Sale", "Shipping and Physical Data",
        "Taxonomy", "Attributes", "Variation Modifiers", "JSON Preview",
        "Advanced JSON", "Save Product Metadata", "Preview only",
    ):
        assert required in body
    for unsupported in ("Copy to Catalogue", "Append Scan", "Woo sync", "Ready for Catalogue"):
        assert unsupported not in body
    css = (metadata_app[0].root_path + "/static/assets/css/custom.css")
    stylesheet = open(css, encoding="utf-8").read()
    assert "@media (max-width: 720px)" in stylesheet
    assert ".prepared-metadata-shell .structured-editor-row" in stylesheet
    assert ".prepared-json-preview pre" in stylesheet and "overflow: auto" in stylesheet
    assert "metadata-mobile-savebar" in body


def test_advanced_editor_preserves_draft_and_reports_errors_without_uncaught_submission():
    script = open("app/static/assets/js/prepared-metadata.js", encoding="utf-8").read()
    assert "Advanced JSON remains authoritative" in script
    assert "event.preventDefault()" in script
    assert "beforeunload" in script
    assert "structuredClone(boot.document" in script
    assert "JSON.parse(editor.value)" in script
    assert "dirty = false" in script
