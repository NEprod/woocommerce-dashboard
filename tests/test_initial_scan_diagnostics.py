"""Initial scan exposes safe preflight reasons without bypassing identity gates."""
import json
import re

import pytest

from app import db
from app.models import CatalogueOperation
from app.operations_workspace import scanner_readiness
from app.utils.reconstruction import detect_setup_state
from test_onboarding_deployment import configured
from test_taxonomy_workspace import registry_app, field


@pytest.mark.parametrize("contents,reason", [
    (None, "Collection metadata file is missing"),
    ("{}", "Missing required field: collection_type"),
    ("{invalid", "Expecting property name"),
])
def test_initial_page_names_collection_and_reason_without_starting(configured, monkeypatch, contents, reason):
    app, client, _, catalogue, _ = configured
    collection = catalogue / "Christmas Ornaments"
    collection.mkdir()
    if contents is not None:
        (collection / "product_info.json").write_text(contents)
    monkeypatch.setattr("app.routes.start_scan", lambda *a, **k: pytest.fail("Must not start a scan"))
    with app.app_context():
        assert scanner_readiness()["mounts_ready"]
        assert not detect_setup_state().safe_to_run
        assert CatalogueOperation.query.count() == 0
    page = client.get("/scanner")
    text = page.get_data(as_text=True)
    assert "Christmas Ornaments/product_info.json" in text and reason in text
    assert str(catalogue.parent) not in text
    assert "disabled" in re.search(r'<button[^>]*data-open-scan-confirm="append"[^>]*>', text).group()
    response = client.post("/scanner/start", json={"mode": "append", "confirm_operation": True},
                           headers={"X-CSRFToken": field(page, "csrf_token")})
    assert response.status_code == 409
    with app.app_context():
        assert CatalogueOperation.query.count() == 0


def test_restored_metadata_enables_review_without_auto_scan(configured, monkeypatch):
    app, client, _, catalogue, _ = configured
    collection = catalogue / "<script>Ornaments"
    collection.mkdir()
    monkeypatch.setattr("app.routes.start_scan", lambda *a, **k: pytest.fail("Must not auto-scan"))
    text = client.get("/scanner").get_data(as_text=True)
    assert "&lt;script&gt;Ornaments/product_info.json" in text
    assert "<script>Ornaments" not in text
    (collection / "product_info.json").write_text(json.dumps({"collection_type": "Simple", "sku_prefix": "FIC"}))
    page = client.get("/scanner")
    text = page.get_data(as_text=True)
    assert "disabled" not in re.search(r'<button[^>]*data-open-scan-confirm="append"[^>]*>', text).group()
    token = field(page, "csrf_token")
    assert client.post("/scanner/start", json={"mode": "append"}, headers={"X-CSRFToken": token}).status_code == 400
    with app.app_context():
        assert detect_setup_state().code == "new_catalogue"
        assert CatalogueOperation.query.count() == 0
