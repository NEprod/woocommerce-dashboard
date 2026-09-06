"""Configuration/onboarding routes and read-only curated-source regression."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from app import db, onboarding
from app.models import Settings, CatalogueOperation
from test_taxonomy_workspace import registry_app, field


@pytest.fixture
def configured(registry_app):
    app, client, root, data = registry_app
    catalogue, output = root.parent / "catalogue", root.parent / "output"
    catalogue.mkdir(); output.mkdir()
    with app.app_context():
        db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix="https://images.example.test/"))
        db.session.commit()
        onboarding.begin()
    app.config.update(PRODUCT_FOLDER=str(catalogue), OUTPUT_FOLDER=str(output), URL_PREFIX="https://images.example.test/", INTAKE_TEST_MOUNTED=False)
    return app, client, root, catalogue, output


def test_deployment_values_override_without_persisting_or_editing_fallback(configured):
    app, client, root, catalogue, output = configured
    app.config["URL_PREFIX"] = "https://cdn.example.test/image-"
    with app.app_context():
        settings = Settings.query.one()
        assert settings.url_prefix == app.config["URL_PREFIX"]
        assert db.session.execute(text("select url_prefix from settings")).scalar() == "https://images.example.test/"
        with pytest.raises(ValueError):
            settings.url_prefix = "https://other.example.test/"
        assert onboarding.readiness()["ready"]
    page = client.get("/initial-settings")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for key in onboarding.FIELDS:
        assert f'name="{key}"' not in body
    assert str(root.parent) not in body
    token = field(page, "csrf_token")
    assert client.post("/initial-settings", data={"csrf_token": token, "url_prefix": "https://other.example.test/"}).status_code == 409
    assert client.post("/initial-settings", data={"csrf_token": token}).headers["Location"].endswith("/scanner")
    app.config["URL_PREFIX"] = ""
    with app.app_context():
        assert Settings.query.one().url_prefix == ""
        assert not onboarding.readiness()["ready"]
    app.config["URL_PREFIX"] = None
    with app.app_context():
        assert Settings.query.one().url_prefix == "https://images.example.test/"


def test_local_fallback_still_editable_and_validated(configured):
    app, client, root, catalogue, output = configured
    app.config.update(PRODUCT_FOLDER=None, OUTPUT_FOLDER=None, URL_PREFIX=None)
    page = client.get("/initial-settings")
    data = {"csrf_token": field(page, "csrf_token"), "product_folder": str(catalogue), "output_folder": str(output), "url_prefix": "https://images.example.test/new/"}
    assert client.post("/initial-settings", data=data).status_code == 302
    data["url_prefix"] = "https://user:password@example.test/"
    invalid = client.post("/initial-settings", data=data)
    assert invalid.status_code == 400
    assert b"user:password" not in invalid.data
    assert onboarding.directory_state("/invalid\x00directory") == "Invalid"


@pytest.mark.parametrize("case", ["missing", "invalid", "read_only"])
def test_taxonomy_readiness_gates_onboarding_without_creating_registry(configured, case):
    app, client, root, *_ = configured
    path = root / "registry.json"
    if case == "missing": path.unlink()
    elif case == "invalid": path.write_text("{invalid")
    else: path.chmod(0o444)
    page = client.get("/initial-settings")
    assert page.status_code == 200
    response = client.get("/scanner")
    assert response.status_code == (200 if case == "read_only" else 302)
    if case == "missing": assert not path.exists()
    if case == "invalid": assert path.read_text() == "{invalid"
    path.chmod(0o644) if path.exists() else None


@pytest.mark.parametrize("state", ["missing", "read_only", "inaccessible"])
def test_directory_readiness(configured, state):
    app, client, root, catalogue, output = configured
    if state == "missing": output.rmdir()
    else: output.chmod(0o555 if state == "read_only" else 0)
    with app.app_context():
        assert not onboarding.readiness()["configured"]
    if output.exists(): output.chmod(0o755)


def test_start_uses_existing_runner_and_success_completes_only_after_revalidation(configured, monkeypatch):
    app, client, root, catalogue, output = configured
    from app import routes
    calls = []
    def start(application, run_id, *, scan_mode, scope):
        calls.append((scan_mode, scope))
        row = CatalogueOperation(id="onboarding-operation", operation_type="append", status="running", scope=json.dumps(scope), recovery_state="none")
        db.session.add(row); db.session.commit()
        return row.id
    monkeypatch.setattr(routes, "start_scan", start)
    page = client.get("/scanner")
    assert b"Required initial catalogue scan" in page.data
    assert b"auth-shell" not in page.data
    assert not calls
    token = field(page, "csrf_token")
    assert client.get("/").headers["Location"].endswith("/initial-settings")
    assert client.post("/scanner/start", json={"mode": "append", "confirm_operation": True}).status_code == 400
    response = client.post("/scanner/start", json={"mode": "append", "confirm_operation": True}, headers={"X-CSRFToken": token})
    assert response.status_code == 202
    assert calls[0][0] == "append" and calls[0][1]["initial_setup"]
    data = {"csrf_token": token, "operation_id": "onboarding-operation"}
    assert client.post("/setup/complete", data=data).status_code == 409
    for status in ("failed", "interrupted", "partial"):
        with app.app_context():
            row = db.session.get(CatalogueOperation, "onboarding-operation")
            row.status = status; row.products_failed = 1; db.session.commit()
        assert client.post("/setup/complete", data=data).status_code == 409
    with app.app_context():
        row = db.session.get(CatalogueOperation, "onboarding-operation")
        row.status = "succeeded"; row.products_failed = 0; db.session.commit()
    page = client.get("/operations/onboarding-operation")
    assert b"Complete setup and continue to Dashboard" in page.data
    app.config["URL_PREFIX"] = "https://changed.example.test/"
    assert client.post("/setup/complete", data=data).status_code == 409
    app.config["URL_PREFIX"] = "https://images.example.test/"
    assert client.post("/setup/complete", data={"operation_id": data["operation_id"]}).status_code == 400
    assert client.post("/setup/complete", data=data).status_code == 302
    with app.app_context(): assert not onboarding.pending()
    assert client.get("/").status_code == 200
    assert len(calls) == 1


def test_initial_modes_and_authentication_remain_guarded(configured, monkeypatch):
    app, client, root, catalogue, output = configured
    from app import routes
    from app.utils.reconstruction import SetupState
    monkeypatch.setattr(routes, "start_scan", lambda *a, **k: pytest.fail("Must not scan"))
    token = field(client.get("/scanner"), "csrf_token")
    for mode in ("update", "full"):
        response = client.post("/scanner/start", json={"mode": mode, "confirm_operation": True}, headers={"X-CSRFToken": token})
        assert response.status_code in {400, 409}
    monkeypatch.setattr(routes, "detect_setup_state", lambda: SetupState("reconstruction_required", 1, 1, 1, 0, 0, True, "reconstruction", True, message="Existing identities require reconstruction."))
    page = client.get("/initial-scan", follow_redirects=True)
    assert b"Run identity-preserving reconstruction" in page.data
    assert client.post("/scanner/start", json={"mode": "append", "confirm_operation": True}, headers={"X-CSRFToken": token}).status_code == 409
    assert app.test_client().get("/initial-settings").status_code in {302, 401}
    assert client.post("/catalogue/reconstruct").status_code == 400


def test_real_initial_runner_persists_scope_and_allows_completion(configured):
    import time
    app, client, root, *_ = configured
    token = field(client.get("/scanner"), "csrf_token")
    assert client.post("/catalogue/reconstruct", data={"csrf_token": token, "confirm_reconstruction": "yes"}).status_code == 409
    response = client.post("/scanner/start", json={"mode": "append", "confirm_operation": True}, headers={"X-CSRFToken": token})
    assert response.status_code == 202
    operation_id = response.json["operation_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with app.app_context():
            operation = db.session.get(CatalogueOperation, operation_id)
            done = operation.status != "running"
        if done: break
        time.sleep(.02)
    with app.app_context():
        operation = db.session.get(CatalogueOperation, operation_id)
        assert onboarding.can_complete(operation), (operation.status, operation.error)
    assert client.post("/setup/complete", data={"csrf_token": token, "operation_id": operation_id}).status_code == 302


def test_existing_identity_reconstruction_completes_setup_without_regeneration(configured):
    from test_reconstruction import _write_catalogue
    app, client, root, catalogue, output = configured
    _write_catalogue(catalogue)
    page = client.get("/scanner")
    assert b"Run identity-preserving reconstruction" in page.data
    token = field(page, "csrf_token")
    response = client.post("/catalogue/reconstruct", data={"csrf_token": token, "confirm_reconstruction": "yes"})
    assert response.status_code == 302
    operation_id = response.headers["Location"].rsplit("/", 1)[1]
    with app.app_context():
        operation = db.session.get(CatalogueOperation, operation_id)
        assert operation.operation_type == "reconstruction"
        assert onboarding.can_complete(operation)
    assert client.post("/setup/complete", data={"csrf_token": token, "operation_id": operation_id}).status_code == 302


def test_legacy_installation_not_forced_back_through_setup(configured):
    app, client, root, *_ = configured
    (Path(app.instance_path) / "onboarding.json").unlink()
    (root / "registry.json").unlink()
    assert client.get("/").status_code == 200
    assert client.get("/scanner").status_code == 200


def test_generic_deployment_contract():
    root = Path(__file__).resolve().parents[1]
    entries = ET.parse(root / "unraid/my-woocommerce-dashboard.xml").getroot().findall("Config")
    targets = [e.attrib["Target"] for e in entries]
    for target in ("/intake", "/catalogue", "/output", "/taxonomy", "PRODUCT_FOLDER", "OUTPUT_FOLDER", "URL_PREFIX", "TAXONOMY_ROOT"):
        assert targets.count(target) == 1
    for e in entries:
        if e.attrib["Target"] in {"/intake", "/catalogue", "/output", "/taxonomy"}:
            assert e.attrib["Default"] == ""
    compose = (root / "compose.yaml").read_text()
    assert "PRODUCT_FOLDER: /catalogue" in compose and "OUTPUT_FOLDER: /output" in compose
    assert "URL_PREFIX:" in compose
    assert "target: /intake" in (root / "compose.intake.yaml").read_text()
    assert "target: /taxonomy" in (root / "compose.taxonomy.yaml").read_text()


def test_startup_projects_environment_without_new_columns(configured, monkeypatch):
    import app as module
    from config import Config
    from sqlalchemy import inspect
    app, client, root, catalogue, output = configured
    with app.app_context():
        Settings.query.delete(); db.session.commit()
    for key, value in {"PRODUCT_FOLDER": str(catalogue), "OUTPUT_FOLDER": str(output), "URL_PREFIX": "https://images.example.test/prefix-"}.items():
        monkeypatch.setattr(Config, key, value)
    restarted = module.create_app()
    with restarted.app_context():
        settings = Settings.query.one()
        assert settings.product_folder == str(catalogue)
        assert settings._product_folder is None
        assert {c["name"] for c in inspect(db.engine).get_columns("settings")} == {"id", "product_folder", "output_folder", "url_prefix"}
        from app.utils.image_tools import get_image_csv_urls
        assert get_image_csv_urls(["fictional.png"], settings.url_prefix) == ["https://images.example.test/prefix-fictional.webp"]
        assert onboarding.pending()


@pytest.mark.parametrize("unsafe", ["corrupt", "symlink"])
def test_invalid_setup_state_fails_closed(configured, unsafe):
    app, client, root, *_ = configured
    path = Path(app.instance_path) / "onboarding.json"
    if unsafe == "corrupt":
        path.write_text("{invalid")
    else:
        path.unlink(); path.symlink_to(root / "registry.json")
    with app.app_context():
        assert onboarding.pending()
        assert not onboarding.readiness()["ready"]
    assert client.get("/scanner").status_code == 302
    assert b"Application setup state is unreadable or invalid" in client.get("/initial-settings").data


def test_current_scanner_on_temporary_curated_products(tmp_path):
    from app.utils.scanner import scan_collection
    source = Path(__file__).resolve().parents[1] / "deployment/examples/tlc/products"
    assert source.is_dir(), "Curated product references must be supplied, not fabricated"
    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()}
    before = hashes()
    copied = tmp_path / "catalogue"
    shutil.copytree(source, copied)
    output = tmp_path / "output"; output.mkdir()
    results = {}
    failures = {}
    for collection in sorted(copied.iterdir()):
        if not collection.is_dir(): continue
        messages = []
        rows = scan_collection(str(collection), "https://images.example.test/", str(output), log=lambda message, **kw: messages.append(message))
        if not rows:
            failures[collection.name] = [str(m) for m in messages if "Failed" in str(m)]
        results[collection.name] = {kind: sum(r["Type"] == kind for r in rows) for kind in ("simple", "variable", "variation")}
    print("Curated scanner rows:", json.dumps(results, sort_keys=True))
    assert results == {
        "16-Bit Pixel Art Cards": {"simple": 3, "variable": 0, "variation": 0},
        "3D Christmas Ornament Set": {"simple": 0, "variable": 1, "variation": 14},
        "Adorable Dog Ornaments": {"simple": 0, "variable": 4, "variation": 16},
        "Lego Brick Cards": {"simple": 2, "variable": 0, "variation": 0},
        "Naughty But Nice Ornament Set": {"simple": 4, "variable": 0, "variation": 0},
        "Personalised Hero Print": {"simple": 0, "variable": 1, "variation": 9},
    }
    assert hashes() == before
    assert not failures, failures
