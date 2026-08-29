import importlib
import json
from pathlib import Path

import pytest

from app import create_app, db
from app.models import Collection, Product, Settings
from app.utils import discord
from app.utils.ingest import ingest_rows_to_db
from app.utils.json_utils import merge_product_json
from app.utils.scanner import scan_collection
from config import Config


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _simple_collection(root: Path, folder: str, prefix: str, shared_title=None):
    collection = root / folder
    payload = {
        "collection_type": "Simple",
        "sku_prefix": prefix,
        "price": "10.00",
    }
    if shared_title is not None:
        payload["title"] = shared_title
    _write_json(collection / "product_info.json", payload)
    return collection


@pytest.mark.parametrize(
    ("override_title", "shared_title", "expected"),
    [
        ("Ascending", "Choose Your Design & Direction", "Ascending - Choose Your Design & Direction"),
        (None, "Choose Your Design & Direction", "Holiday Express Train - Choose Your Design & Direction"),
        (None, None, "Holiday Express Train"),
        ("   ", "Choose Your Design & Direction", "Holiday Express Train - Choose Your Design & Direction"),
        ("Ascending", "  ", "Ascending"),
        (" ", "\t", "Holiday Express Train"),
        ("L’Ascension", "Café & Colour", "L’Ascension - Café & Colour"),
    ],
)
def test_product_title_fallback_contract(override_title, shared_title, expected):
    shared = {"sku_prefix": "FIC-T-"}
    override = {}
    if shared_title is not None:
        shared["title"] = shared_title
    if override_title is not None:
        override["title"] = override_title

    resolved = merge_product_json(
        shared,
        override,
        path="/catalogue/Collection Display Name/Holiday Express Train",
    )
    assert resolved["title"] == expected
    assert not resolved["title"].startswith("-")
    assert not resolved["title"].endswith("-")


def test_append_assignment_is_deterministic_and_uses_source_provenance(tmp_path, quiet_log):
    database = tmp_path / "catalogue.db"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    output.mkdir()
    collections = [
        _simple_collection(catalogue, "Alpha Collection", "M9-A-", "Alpha shared"),
        _simple_collection(catalogue, "Bravo Collection", "M9-B-", "Bravo shared"),
        _simple_collection(catalogue, "Charlie Collection", "M9-C-", "Charlie shared"),
    ]
    for index, collection in enumerate(collections):
        product = collection / "Existing Product"
        product.mkdir()
        if index % 2:
            _write_json(product / "product_info.json", {"title": f"Existing {index}"})

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue),
                    output_folder=str(output),
                    url_prefix="https://invalid.example/images/",
                )
            )
            db.session.commit()

            initial_rows = []
            for collection in reversed(collections):
                initial_rows.extend(
                    scan_collection(collection, "https://invalid.example/images/", output, log=quiet_log)
                )
            ingest_rows_to_db(initial_rows, log=quiet_log)

            new_product = collections[1] / "New Product"
            new_product.mkdir()
            _write_json(new_product / "product_info.json", {"title": "New specific title"})

            for _ in range(3):
                append_rows = []
                for collection in collections:
                    append_rows.extend(
                        scan_collection(collection, "https://invalid.example/images/", output, log=quiet_log)
                    )
                if append_rows:
                    ingest_rows_to_db(append_rows, log=quiet_log)

                projected = Product.query.filter_by(source_relpath="Bravo Collection/New Product").one()
                bravo = Collection.query.filter_by(source_relpath="Bravo Collection").one()
                assert projected.collection_id == bravo.id
                assert projected.title == "New specific title - Bravo shared"
                assert Product.query.filter_by(source_relpath="Bravo Collection/New Product").count() == 1
                assert Collection.query.count() == 3
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_update_refreshes_resolved_title_without_changing_identity(tmp_path, quiet_log):
    database = tmp_path / "title-update.db"
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    output.mkdir()
    collection = _simple_collection(
        catalogue, "Collection Folder Identity", "M9-U-", "Shared Product Title"
    )
    product_folder = collection / "Product Folder Identity"
    product_folder.mkdir()
    override_path = product_folder / "product_info.json"
    _write_json(override_path, {"title": "Original Product Title"})

    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        with app.app_context():
            db.session.add(
                Settings(
                    product_folder=str(catalogue), output_folder=str(output),
                    url_prefix="https://invalid.example/images/",
                )
            )
            db.session.commit()
            first_rows = scan_collection(collection, "https://invalid.example/images/", output, log=quiet_log)
            ingest_rows_to_db(first_rows, log=quiet_log)
            projected = Product.query.one()
            product_id, sku = projected.id, projected.sku
            assert projected.title == "Original Product Title - Shared Product Title"

            _write_json(override_path, {"title": "Updated Product Title"})
            (product_folder / ".update").write_text("update", encoding="utf-8")
            updated_rows = scan_collection(
                collection, "https://invalid.example/images/", output,
                update_csv=True, log=quiet_log,
            )
            ingest_rows_to_db(updated_rows, log=quiet_log)
            projected = Product.query.one()
            assert projected.id == product_id
            assert projected.sku == sku
            assert projected.title == "Updated Product Title - Shared Product Title"
            assert Collection.query.one().name == "Collection Folder Identity"
    finally:
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_scanner_emits_all_title_fallback_cases_without_bad_separators(tmp_path, quiet_log):
    catalogue = tmp_path / "catalogue"
    output = tmp_path / "output"
    output.mkdir()
    titled = _simple_collection(catalogue, "Display Collection", "M9-T-", "Shared title")
    for folder, payload in (
        ("Specific", {"title": "Product title"}),
        ("Shared Only", None),
        ("Blank Override", {"title": "   "}),
    ):
        (titled / folder).mkdir()
        if payload is not None:
            _write_json(titled / folder / "product_info.json", payload)
    untitled = _simple_collection(catalogue, "Untitled Display Collection", "M9-N-")
    (untitled / "Folder Fallback").mkdir()

    rows = scan_collection(titled, "https://invalid.example/images/", output, log=quiet_log)
    rows += scan_collection(untitled, "https://invalid.example/images/", output, log=quiet_log)
    assert {row["Name"] for row in rows} == {
        "Product title - Shared title",
        "Shared Only - Shared title",
        "Blank Override - Shared title",
        "Folder Fallback",
    }
    assert all(not row["Name"].startswith("-") and not row["Name"].endswith("-") for row in rows)


def test_shared_buttons_centre_content_without_changing_navigation_alignment():
    css = Path("app/static/assets/css/custom.css").read_text(encoding="utf-8")
    assert ".btn { display: inline-flex;" in css
    assert "align-items: center; justify-content: center;" in css
    assert ".sidebar-link { display: flex;" in css
    assert ".sidebar-link { display: flex; min-height: 44px; align-items: center;" in css
    assert "@media (max-width: 767.98px)" in css
    assert ".btn, .btn-sm { min-height: 48px; }" in css


def test_user_facing_categories_and_uk_english_copy_are_canonical():
    templates = Path("app/templates")
    rendered_source = "\n".join(
        path.read_text(encoding="utf-8") for path in templates.rglob("*.html")
    )
    assert "Categorys" not in rendered_source
    assert "Add Categorie" not in rendered_source
    assert "('categories','Categories','Category')" in rendered_source
    assert "scanner behaviour" in rendered_source
    assert "scanner behavior" not in rendered_source
    assert "Known unchanged behaviour" in rendered_source


class _Response:
    def __init__(self, status_code=204):
        self.status_code = status_code
        self.headers = {}


def test_discord_events_route_to_configured_channels_with_bounded_safe_payloads(monkeypatch):
    monkeypatch.setenv("DISCORD_ENABLED", "true")
    for channel, variable in discord.WEBHOOK_ENV.items():
        monkeypatch.setenv(variable, f"https://discord.com/api/webhooks/test/{channel}")
    module = importlib.reload(discord)
    calls = []
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: calls.append((url, kwargs["json"])) or _Response())

    assert module.notify_scan_started("append", 3)[0]
    assert module.notify_scan_completed("append", {"warnings": 0, "products_created": 1}, "00:01")[0]
    assert module.notify_scan_completed("append", {"warnings": 1, "warning_summary": [{"category": "missing source images", "count": 1}]}, "00:02")[0]
    assert module.notify_scan_failed("append", "Authorization: Bearer secret at /Users/person/catalogue")[0]
    assert module.notify_editor_saved("collection metadata", "M9-1", collection="Bravo Collection", affected=2)[0]
    assert module.notify_override_created("M9-1", product="New specific title - Bravo shared", collection="Bravo Collection")[0]
    assert module.notify_ingest_product(sku="M9-1", name="New specific title - Bravo shared", product_type="simple", parent_images_count=1, variation_images_count=0, total_images_count=1, output_images_copied=1, has_shared=True, has_override=True, folder_path="Bravo Collection/New Product")[0]

    routes = [url.rsplit("/", 1)[-1] for url, _ in calls]
    assert routes == ["scans_info", "scans_info", "scans_errors", "scans_errors", "edits", "overrides", "ingest"]
    serialised = json.dumps([payload for _, payload in calls], ensure_ascii=False)
    assert "New specific title - Bravo shared" in serialised
    assert "Parent images" in serialised and "Output images copied" in serialised
    assert "secret" not in serialised
    assert "/Users/person" not in serialised
    assert all(len(embed.get("fields", [])) <= module.MAX_EMBED_FIELDS for _, payload in calls for embed in payload.get("embeds", []))

    monkeypatch.setenv("DISCORD_ENABLED", "false")
    importlib.reload(discord)
