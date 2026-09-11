"""Isolated registry-only route/storage tests; no real catalogue or Woo access."""
import csv
import html
import io
import json
import os
from pathlib import Path
import re
import socket
import xml.etree.ElementTree as ET

import pytest

from app import taxonomy_workspace as service
from app import taxonomy_registry as registry


def entry(key, **extra):
    return {"key": key, "name": key.title(), "slug": key, "state": "active", **extra}


@pytest.fixture
def registry_app(tmp_path, monkeypatch):
    import app as module
    from config import Config
    from app.models import User
    from app.utils.operation_control import reset_operation_control_for_tests
    instance = tmp_path / "instance"
    instance.mkdir()
    root = tmp_path / "taxonomy"
    root.mkdir()
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{instance / 'test.db'}")
    monkeypatch.setattr(Config, "TAXONOMY_ROOT", str(root))
    original = module.Flask
    monkeypatch.setattr(module, "Flask", lambda *args, **kwargs: original(*args, instance_path=str(instance), **kwargs))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("Registry cannot access network"))
    app = module.create_app()
    app.config.update(TESTING=True)
    reset_operation_control_for_tests()
    data = service.empty_registry()
    data["categories"] = [entry("cards", parent=None), entry("notelets", parent="cards")]
    data["attributes"] = [entry("finish", navigation=True, visible_default=False, terms=[entry("matte")])]
    (root / "registry.json").write_bytes(service.validate(data))
    with app.app_context():
        user = User(email="registry@example.test", username="registry", password="fictional")
        module.db.session.add(user)
        module.db.session.commit()
        user_id = user.id
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    yield app, client, root, data
    with app.app_context():
        module.db.session.remove()
    reset_operation_control_for_tests()


def field(response, name):
    text = response.get_data(as_text=True)
    match = re.search(r'name="' + name + r'"[^>]*value="([^"]*)"', text)
    if not match:
        match = re.search(r'<textarea[^>]*name="' + name + r'"[^>]*>(.*?)</textarea>', text, re.S)
    assert match, (name, response.status_code, text[-1000:])
    return html.unescape(match.group(1))


def proposal(client, data):
    page = client.get("/taxonomy/advanced")
    result = client.post("/taxonomy/advanced", data={"csrf_token": field(page, "csrf_token"),
        "base": field(page, "base"), "document": json.dumps(data)})
    assert result.status_code == 200
    return result


def confirm(client, page, **extra):
    values = {"csrf_token": field(page, "csrf_token"), "review": field(page, "review"),
              "document": field(page, "document"), "acknowledge": "yes", **extra}
    return client.post("/taxonomy/confirm", data=values, follow_redirects=True)


def seed_pair():
    rows = [{"name": "Paper", "parent": None, "slug": "paper", "category_path": "Paper", "level": 0, "sort_order": 1},
            {"name": "Notes", "parent": "Paper", "slug": "notes", "category_path": "Paper > Notes", "level": 1, "sort_order": 2}]
    seed = {"schema_version": 1, "registry_type": "tlc_taxonomy_seed", "categories": rows,
            "navigation_attributes": [{"name": "Finish", "navigation": True, "terms": ["Matte", "Gloss"]}]}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return json.dumps(seed).encode(), buffer.getvalue().encode()


def test_authentication_csrf_overview_and_templates(registry_app):
    app, client, root, _ = registry_app
    assert app.test_client().get("/taxonomy").status_code in (302, 401)
    assert client.post("/taxonomy/confirm", data={}).status_code == 400
    page = client.get("/taxonomy")
    assert page.status_code == 200
    for text in ["Registry readiness", "Ready", "Storefront Collections", "Reviewed Woo taxonomy sync", "Cards > Notelets"]:
        assert text in html.unescape(page.get_data(as_text=True))
    assert str(root) not in page.get_data(as_text=True)
    with app.app_context():
        for name in app.jinja_env.list_templates():
            if name.startswith("taxonomy/") or name == "includes/navbar.html":
                app.jinja_env.get_template(name)
    assert client.get("/taxonomy?kind=categories&q=notelet").status_code == 200
    assert client.get("/taxonomy/edit/terms?attribute=finish&key=matte").status_code == 200


@pytest.mark.parametrize("kind,extra", [("categories", {"parent": "cards"}), ("storefront_collections", {}),
                                       ("attributes", {"navigation": "yes"}), ("tags", {}), ("terms", {})])
def test_guided_create_edit_remove_reviewed(registry_app, kind, extra):
    _, client, root, _ = registry_app
    suffix = "&attribute=finish" if kind == "terms" else ""
    url = f"/taxonomy/edit/{kind}?{suffix}"
    page = client.get(url)
    values = {"base": field(page, "base"), "csrf_token": field(page, "csrf_token"),
              "key": "fictional", "name": "Fictional", "slug": "fictional", "state": "draft", "order": "3", **extra}
    original = (root / "registry.json").read_bytes()
    reviewed = client.post(url, data=values)
    assert reviewed.status_code == 200 and (root / "registry.json").read_bytes() == original
    assert confirm(client, reviewed).status_code == 200
    url = f"/taxonomy/edit/{kind}?key=fictional{suffix}"
    page = client.get(url)
    values.update(base=field(page, "base"), csrf_token=field(page, "csrf_token"), name="Renamed", key="attempted-key-change")
    reviewed = client.post(url, data=values)
    assert confirm(client, reviewed).status_code == 200
    document = json.loads((root / "registry.json").read_bytes())
    rows = document[kind] if kind != "terms" else document["attributes"][0]["terms"]
    assert next(r for r in rows if r["key"] == "fictional")["name"] == "Renamed"
    page = client.get(url)
    reviewed = client.post(url, data={"base": field(page, "base"), "csrf_token": field(page, "csrf_token"), "action": "remove"})
    assert confirm(client, reviewed).status_code == 200


def test_category_dependencies_cycle_and_preserved_draft(registry_app):
    _, client, root, _ = registry_app
    page = client.get("/taxonomy/edit/categories?key=cards")
    values = {"base": field(page, "base"), "csrf_token": field(page, "csrf_token"), "action": "remove"}
    assert client.post("/taxonomy/edit/categories?key=cards", data=values).status_code == 422
    values.update(action="save", name="Changed Draft", slug="cards", state="active", parent="notelets")
    failed = client.post("/taxonomy/edit/categories?key=cards", data=values)
    assert failed.status_code == 422 and b"Changed Draft" in failed.data
    assert json.loads((root / "registry.json").read_bytes())["categories"][0]["parent"] is None


def test_review_backup_history_and_no_product_side_effects(registry_app):
    app, client, root, data = registry_app
    from app.models import Product, Variation, CatalogueOperation
    original = (root / "registry.json").read_bytes()
    data["tags"] = [entry("fresh")]
    page = proposal(client, data)
    response = confirm(client, page)
    assert b"saved and verified" in response.data and b"No catalogue, scanner or WooCommerce" in response.data
    backups = list(root.glob(".registry-backup-*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not list(root.glob(".registry-stage-*"))
    with app.app_context():
        assert Product.query.count() == Variation.query.count() == 0
        operation = CatalogueOperation.query.filter_by(operation_type="taxonomy_registry_update").one()
        assert operation.status == "succeeded"
        assert "fresh" not in operation.scope  # Bounded counts, no full document.
        assert client.get(f"/operations/{operation.id}").status_code == 200
    assert confirm(client, page).status_code == 409  # Replay cannot overwrite.


@pytest.mark.parametrize("failure", ["stale", "tamper", "ack", "root", "user"])
def test_signed_confirmation_safety(registry_app, failure):
    app, client, root, data = registry_app
    page = proposal(client, data)
    kwargs = {}
    if failure == "stale":
        (root / "registry.json").write_text(json.dumps(data) + " ")
    elif failure == "tamper":
        kwargs["document"] = field(page, "document") + " "
    elif failure == "ack":
        kwargs["acknowledge"] = ""
    elif failure == "root":
        replacement = root.parent / "other-taxonomy"
        replacement.mkdir()
        (replacement / "registry.json").write_bytes(service.validate(data))
        app.config["TAXONOMY_ROOT"] = str(replacement)
    else:
        from app import db
        from app.models import User
        with app.app_context():
            user = User(username="other", email="other@example.test", password="fictional")
            db.session.add(user)
            db.session.commit()
            identity = str(user.id)
        with client.session_transaction() as session:
            session["_user_id"] = identity
    assert confirm(client, page, **kwargs).status_code == 409


@pytest.mark.parametrize("document", ['{', '{"schema_version":1,"schema_version":1}', '{"schema_version":9}'])
def test_invalid_advanced_preserves_input(registry_app, document):
    _, client, root, _ = registry_app
    original = (root / "registry.json").read_bytes()
    page = client.get("/taxonomy/advanced")
    response = client.post("/taxonomy/advanced", data={"csrf_token": field(page,"csrf_token"), "base": field(page,"base"), "document": document})
    assert response.status_code == 422
    assert field(response, "document") == document
    assert (root / "registry.json").read_bytes() == original


def test_readonly_stale_edit_and_unsafe_symlink(registry_app):
    app, client, root, data = registry_app
    page = proposal(client, data)
    source = root / "registry.json"
    source.chmod(0o444)
    assert b"Read Only" in client.get("/taxonomy").data
    assert confirm(client, page).status_code == 409
    source.chmod(0o600)
    real = root / "saved.json"
    source.rename(real)
    source.symlink_to(real)
    assert confirm(client, page).status_code == 409
    assert real.read_bytes() == service.validate(data)
    source.unlink()
    real.rename(source)
    edit = client.get("/taxonomy/advanced")
    source.write_bytes(service.validate({**data, "tags": [entry("changed")]}))
    assert client.post("/taxonomy/advanced", data={"csrf_token": field(edit,"csrf_token"), "base": field(edit,"base"), "document": json.dumps(data)}).status_code == 409


def test_bootstrap_review_exact_counts_stable_keys_and_no_overwrite(registry_app):
    _, client, root, _ = registry_app
    assert client.get("/taxonomy/import").status_code == 409
    (root / "registry.json").unlink()
    seed, categories = seed_pair()
    assert service.bootstrap(seed, categories) == service.bootstrap(seed, categories)
    document = service.bootstrap(seed, categories)
    assert service.counts(document) == {"categories":2, "storefront_collections":0, "attributes":1, "tags":0, "terms":2}
    assert document["categories"][1]["parent"] == "cat-paper"
    page = client.get("/taxonomy/import")
    review = client.post("/taxonomy/import", data={"base":field(page,"base"), "csrf_token":field(page,"csrf_token"),
        "seed":(io.BytesIO(seed), "fictional.json"), "categories_csv":(io.BytesIO(categories),"fictional.csv")})
    assert review.status_code == 200 and not (root / "registry.json").exists()
    assert confirm(client, review).status_code == 200
    assert json.loads((root / "registry.json").read_bytes()) == document
    assert confirm(client, review).status_code == 409


def test_bootstrap_rejects_mismatch_collision_and_bad_structure():
    seed, categories = seed_pair()
    with pytest.raises(service.RegistryEditError):
        service.bootstrap(seed, categories.replace(b"Notes", b"Wrong"))
    data = json.loads(seed)
    data["navigation_attributes"][0]["terms"] += ["MATTE"]
    with pytest.raises(service.RegistryEditError):
        service.bootstrap(json.dumps(data).encode(), categories)
    for invalid in [b"{}", b"[]", b"{", b"x" * (registry.MAX_BYTES+1)]:
        with pytest.raises(service.RegistryEditError):
            service.bootstrap(invalid, categories)


@pytest.mark.parametrize("failure", ["backup", "replace", "postverify"])
def test_failed_save_preserves_old_source(registry_app, monkeypatch, failure):
    app, _, root, data = registry_app
    original = (root / "registry.json").read_bytes()
    data["tags"] = [entry("new")]
    with app.app_context():
        _, revision, _ = service.current_source()
        if failure == "backup":
            real = service._write_new
            def bad(fd, name, raw, mode=0o600):
                if "backup" in name:
                    raise service.RegistryEditError("Backup failed")
                return real(fd, name, raw, mode)
            monkeypatch.setattr(service,"_write_new",bad)
        elif failure == "replace":
            monkeypatch.setattr(service.os,"replace",lambda *a,**k: (_ for _ in ()).throw(OSError("fixture")))
        else:
            real_replace, real_sync = os.replace, os.fsync
            replaced = [False]
            def replace(*args, **kwargs):
                real_replace(*args, **kwargs)
                replaced[0] = True
            def sync(fd):
                if replaced[0]:
                    replaced[0] = False
                    raise OSError("fixture durability failure")
                real_sync(fd)
            monkeypatch.setattr(service.os,"replace",replace)
            monkeypatch.setattr(service.os,"fsync",sync)
        with pytest.raises((service.RegistryEditError, OSError)):
            service.save_reviewed(data, revision)
    assert (root / "registry.json").read_bytes() == original
    assert not list(root.glob(".registry-stage-*"))


def test_fictional_pagination_and_responsive_semantics(registry_app):
    _, client, root, data = registry_app
    data["tags"] = [entry(f"tag-{i}") for i in range(30)]
    (root / "registry.json").write_bytes(service.validate(data))
    page = client.get("/taxonomy?kind=tags&page=2")
    assert page.status_code == 200 and b"page 2" in page.data
    assert b'aria-label="Definition pages"' in page.data
    css = Path("app/static/assets/css/taxonomy.css").read_text()
    assert "1199px" in css and "767px" in css and "minmax(0, 1fr)" in css
    assert "var(--color-code-text)" in css
    assert b'aria-current="page"' in page.data and b'Taxonomy' in page.data


def test_optional_taxonomy_deployment_contract():
    root = ET.parse("unraid/my-woocommerce-dashboard.xml").getroot()
    configs = {c.attrib["Target"]:c for c in root.findall("Config")}
    assert configs["/taxonomy"].attrib["Required"] == "false"
    assert configs["/taxonomy"].attrib["Default"] == ""
    assert configs["TAXONOMY_ROOT"].text == "/taxonomy"
    overlay = Path("compose.taxonomy.yaml").read_text()
    assert "create_host_path: false" in overlay and "target: /taxonomy" in overlay
    assert "taxonomy" not in Path("compose.yaml").read_text()  # Existing deployment remains optional.


def test_lock_conflict_and_backup_retention(registry_app):
    import fcntl
    app, _, root, data = registry_app
    with app.app_context():
        _, revision, _ = service.current_source()
        data["tags"] = [entry("changed")]
        lock_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError):
                service.save_reviewed(data, revision)
        finally:
            os.close(lock_fd)
        for index in range(12):
            (root / f'.registry-backup-{index:032x}.json').write_bytes(service.validate(data))
        unrelated = root / 'manual-backup.json'
        unrelated.write_bytes(b'private reference')
        service.save_reviewed(data, revision)
        assert len(list(root.glob('.registry-backup-*.json'))) == 10
        assert unrelated.read_bytes() == b'private reference'


def test_missing_mount_never_created_and_bootstrap_rejects_destination_change(registry_app):
    app, client, root, data = registry_app
    (root / 'registry.json').unlink()
    page = client.get('/taxonomy/import')
    seed, categories = seed_pair()
    review = client.post('/taxonomy/import', data={'base':field(page,'base'), 'csrf_token':field(page,'csrf_token'),
        'seed':(io.BytesIO(seed),'seed.json'), 'categories_csv':(io.BytesIO(categories),'categories.csv')})
    (root / 'registry.json').write_bytes(service.validate(data))
    assert confirm(client, review).status_code == 409
    missing = root.parent / 'not-mounted'
    app.config['TAXONOMY_ROOT'] = str(missing)
    assert client.get('/taxonomy/import').status_code == 409
    assert not missing.exists()


def test_stale_review_retains_source_and_symlink_ancestor_rejects(registry_app):
    app, client, root, data = registry_app
    page = proposal(client, data)
    alias = root.parent / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    app.config['TAXONOMY_ROOT'] = str(alias)
    response = confirm(client, page)
    assert response.status_code == 409 and b'Unsaved proposed source' in response.data
    assert str(root).encode() not in response.data


def test_postwrite_mismatch_keeps_verified_backup_and_reports_failure(registry_app, monkeypatch):
    app, client, root, data = registry_app
    original = (root / 'registry.json').read_bytes()
    data['tags'] = [entry('new')]
    review = proposal(client, data)
    real_replace = os.replace
    def corrupt(*args, **kwargs):
        real_replace(*args, **kwargs)
        (root / 'registry.json').write_bytes(b'{')
    monkeypatch.setattr(service.os, 'replace', corrupt)
    response = confirm(client, review)
    assert response.status_code == 409 and b'Post-write verification failed' in response.data
    assert any(p.read_bytes() == original for p in root.glob('.registry-backup-*.json'))
    from app.models import CatalogueOperation
    with app.app_context():
        assert CatalogueOperation.query.filter_by(operation_type='taxonomy_registry_update').one().status == 'failed'


def test_request_bounds_precede_csrf_parsing(registry_app):
    _, client, _, data = registry_app
    page = client.get('/taxonomy/advanced')
    values = {'base':field(page,'base'), 'csrf_token':field(page,'csrf_token'),
              'document':' ' * 600000 + json.dumps(data)}
    assert client.post('/taxonomy/advanced', data=values).status_code == 200
    values['document'] = 'x' * (4 * 1024 * 1024)
    assert client.post('/taxonomy/advanced', data=values).status_code == 413


def test_supplied_tlc_bootstrap_readonly():
    source_root = os.environ.get("TLC_REFERENCE_ROOT")
    if not source_root:
        pytest.skip("Optional external read-only TLC references not configured")
    root = Path(source_root)
    paths = [root/"tlc-taxonomy-categories-attributes-full.json", root/"TLC-WooCommerce-Categories.csv"]
    originals = [p.read_bytes() for p in paths]
    document = service.bootstrap(*originals)
    assert service.counts(document) == {"categories":56,"storefront_collections":0,"attributes":7,"terms":110,"tags":0}
    assert [a["name"] for a in document["attributes"]] == ["Occasion","Recipient","Age / Milestone","Personalisation","Material","Production Method","Style / Theme"]
    assert [len(a["terms"]) for a in document["attributes"]] == [26,32,20,3,10,7,12]
    assert [p.read_bytes() for p in paths] == originals
    assert document == service.bootstrap(*originals)
    # The deployment-owned production artifact is the same vocabulary, with the
    # reviewed definitions activated, not a change to importer defaults.
    for kind in registry.KINDS:
        for row in document[kind]:
            row['state'] = 'active'
            for term in row.get('terms', []):
                term['state'] = 'active'
    assert json.loads(TLC_ARTIFACT.read_bytes()) == document


TLC_ARTIFACT = Path(__file__).resolve().parents[1] / 'deployment/examples/tlc/registry.json'
TLC_DIGEST = 'a7a249ca6cfcf87845c2cec3629b98b1b5b29f44d6e7d7c08f96b4c5abbc084f'


def test_tlc_artifact_production_loader_and_independent_counts(tmp_path):
    original = TLC_ARTIFACT.read_bytes()
    root = tmp_path / 'mounted-taxonomy'
    root.mkdir()
    (root / 'registry.json').write_bytes(original)
    loaded = registry.load_registry(root)
    assert loaded.status == 'ready' and loaded.issues == ()
    assert loaded.snapshot.schema_version == 1 and loaded.snapshot.digest == TLC_DIGEST
    data = json.loads(original)
    assert len(data['categories']) == 56 and len(data['attributes']) == 7
    assert sum(len(a['terms']) for a in data['attributes']) == 110
    assert len(data['storefront_collections']) == 2 and data['tags'] == []
    assert all(r['state'] == 'active' for kind in registry.KINDS for r in data[kind])
    assert all(t['state'] == 'active' for a in data['attributes'] for t in a['terms'])
    (root / 'registry.json').write_text(json.dumps(data, sort_keys=True))
    assert registry.load_registry(root).snapshot.digest == TLC_DIGEST
    assert TLC_ARTIFACT.read_bytes() == original


def test_tlc_artifact_workspace_roundtrip(registry_app):
    _, client, root, _ = registry_app
    original = TLC_ARTIFACT.read_bytes()
    (root / 'registry.json').write_bytes(original)
    overview = html.unescape(client.get('/taxonomy').get_data(as_text=True))
    for label, count in [('Categories',56), ('Attributes',7), ('Attribute terms',110), ('Storefront Collections',2), ('Tags',0)]:
        assert f'<h2>{label}</h2><p>{count}</p>' in overview
    category = html.unescape(client.get('/taxonomy?q=Birthday Cards').get_data(as_text=True))
    assert 'Cards > Birthday Cards' in category
    attributes = html.unescape(client.get('/taxonomy?kind=attributes&q=Recipient').get_data(as_text=True))
    assert '32 registered terms' in attributes and 'For Daughter' in attributes
    assert client.get('/taxonomy/edit/attributes?key=attr-recipient').status_code == 200
    assert client.get('/taxonomy/edit/terms?attribute=attr-recipient&key=term-for-daughter').status_code == 200
    assert client.get('/taxonomy/advanced').status_code == 200
    data = json.loads(original)
    stale = proposal(client, {**data, 'tags':[entry('temporary-test-only')]})
    url = '/taxonomy/edit/categories?key=cat-birthday-cards'
    edit = client.get(url)
    target = next(r for r in data['categories'] if r['key'] == 'cat-birthday-cards')
    reviewed = client.post(url, data={**target, 'aliases':'Cards > Birthday Greetings',
        'csrf_token':field(edit,'csrf_token'), 'base':field(edit,'base')})
    assert reviewed.status_code == 200
    assert (root / 'registry.json').read_bytes() == original
    assert confirm(client, reviewed).status_code == 200
    result = registry.load_registry(root)
    assert result.status == 'ready' and result.snapshot.digest != TLC_DIGEST
    expected = json.loads(original)
    next(r for r in expected['categories'] if r['key'] == target['key'])['aliases'] = ['Cards > Birthday Greetings']
    assert json.loads((root / 'registry.json').read_bytes()) == expected
    assert confirm(client, stale).status_code == 409
    assert json.loads((root / 'registry.json').read_bytes()) == expected
    assert TLC_ARTIFACT.read_bytes() == original


@pytest.mark.parametrize('installed', [True, False])
def test_bring_your_own_registry_without_tlc_or_fallback(registry_app, monkeypatch, installed):
    app, client, root, _ = registry_app
    from app import create_app
    def forbidden(*args, **kwargs):
        pytest.fail('TLC bootstrap must never run automatically')
    monkeypatch.setattr(service, 'bootstrap', forbidden)
    own = service.empty_registry()
    own['categories'] = [entry('nebula-modules', parent=None)]
    own['attributes'] = [entry('signal-band', navigation=True, visible_default=False,
                                terms=[entry('quartz-tone')])]
    source = root / 'registry.json'
    if installed:
        source.write_bytes(service.validate(own))
    else:
        source.unlink()
    before = source.read_bytes() if installed else None
    restarted = create_app()  # Actual startup under the isolated fixture config.
    with restarted.app_context():
        result = registry.load_configured_registry()
        assert result.status == ('ready' if installed else 'registry_missing')
    page = client.get('/taxonomy').get_data(as_text=True)
    assert 'Bring your own registry' in page
    if installed:
        assert 'Nebula-Modules' in page
        attribute_page = client.get('/taxonomy?kind=attributes').get_data(as_text=True)
        assert 'Signal-Band' in attribute_page and 'Quartz-Tone' in attribute_page
        assert 'For Daughter' not in attribute_page and 'Birthday Cards' not in page
        assert source.read_bytes() == before
    else:
        assert 'Registry Missing' in page
        assert not source.exists() and list(root.iterdir()) == []


def test_deployment_artifact_not_an_application_default():
    project = TLC_ARTIFACT.parents[3]
    assert '/deployment/examples/**' in (project / '.dockerignore').read_text()
    dockerfile = (project / 'Dockerfile').read_text()
    entrypoint = (project / 'docker/entrypoint.sh').read_text()
    assert 'deployment/examples' not in dockerfile + entrypoint
    assert 'registry.json' not in dockerfile + entrypoint
    assert not (project / 'app/resources/taxonomy/registry.json').exists()
    configs = {row.attrib['Target']: row for row in ET.parse(project / 'unraid/my-woocommerce-dashboard.xml').getroot().findall('Config')}
    assert configs['/taxonomy'].attrib['Default'] == ''
    assert configs['/taxonomy'].attrib['Required'] == 'false'
    assert configs['TAXONOMY_ROOT'].text == '/taxonomy'
