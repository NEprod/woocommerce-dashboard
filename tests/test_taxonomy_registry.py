"""Fictional read-only registry tests; no catalogue, scanner or Woo access."""

from copy import deepcopy
import json
import os
from pathlib import Path
import runpy

import pytest

from app import taxonomy_registry as registry


def entry(key, name, **extra):
    return {"key": key, "name": name, "slug": key, "state": "active", **extra}


@pytest.fixture
def document():
    return {
        "schema_version": 1,
        "categories": [entry("stationery", "Stationery", parent=None),
                       entry("notebooks", "Notebooks", parent="stationery")],
        "storefront_collections": [entry("moon-range", "Moon Range")],
        "attributes": [entry("finish", "Finish", navigation=True, visible_default=True,
                             terms=[entry("matte", "Matte"), entry("gloss", "Gloss")])],
        "tags": [entry("seasonal", "Seasonal", aliases=["Seasonal Selection"])],
    }


@pytest.fixture
def authored(tmp_path, document):
    root = tmp_path / "taxonomy"
    root.mkdir()
    source = root / "registry.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    return root, source


def save(source, data):
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_valid_snapshot_read_is_nonmutating_and_immutable(authored):
    root, source = authored
    original, metadata = source.read_bytes(), source.stat()
    result = registry.load_registry(root)
    assert result.status == "ready" and result.available
    assert result.snapshot.data["categories"][1]["parent"] == "stationery"
    with pytest.raises(TypeError):
        result.snapshot.data["schema_version"] = 2
    with pytest.raises(TypeError):
        result.snapshot.data["attributes"][0]["terms"][0]["name"] = "Changed"
    with pytest.raises(AttributeError):
        result.snapshot.data["tags"].append({})
    with pytest.raises(AttributeError):
        result.snapshot.digest = "changed"
    assert source.read_bytes() == original
    assert source.stat().st_mtime_ns == metadata.st_mtime_ns
    assert list(root.iterdir()) == [source]


def test_digest_ignores_serialization_but_preserves_content_and_order(authored, document):
    root, source = authored
    first = registry.load_registry(root).snapshot
    source.write_text(json.dumps(document, indent=4, sort_keys=True), encoding="utf-8")
    assert registry.load_registry(root).snapshot.digest == first.digest
    document["attributes"][0]["terms"].reverse()
    save(source, document)
    assert registry.load_registry(root).snapshot.digest != first.digest
    assert first.data["attributes"][0]["terms"][0]["key"] == "matte"
    source.write_text("{", encoding="utf-8")
    assert registry.load_registry(root).snapshot is None  # Never stale-cache fallback.


@pytest.mark.parametrize("root", [None, "", "  "])
def test_unconfigured(root):
    assert registry.load_registry(root).status == "not_configured"


def test_missing_root_and_registry_are_not_created(tmp_path):
    root = tmp_path / "absent"
    assert registry.load_registry(root).status == "root_missing"
    assert not root.exists()
    root.mkdir()
    assert registry.load_registry(root).status == "registry_missing"
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("raw,code", [
    (b"{", "malformed_json"), (b"\xff", "malformed_json"),
    (b'{"schema_version":1,"schema_version":1}', "duplicate_json_key"),
    (b'{"nested":{"name":"A","name":"B"}}', "duplicate_json_key"),
    (b'{"schema_version":NaN}', "malformed_json"),
    (b'{"schema_version":2}', "unsupported_version"),
    (b'{"schema_version":true}', "unsupported_version"),
    (b'[]', "schema"),
])
def test_invalid_json_and_versions(authored, raw, code):
    root, source = authored
    source.write_bytes(raw)
    result = registry.load_registry(root)
    assert result.status == "invalid" and not result.available
    assert result.issues[0].code == code
    assert str(root) not in repr(result)


@pytest.mark.parametrize("mutation,code", [
    (lambda d: d["tags"].append(entry("finish", "Other")), "duplicate_key"),
    (lambda d: d["categories"][1].update(parent="unknown"), "invalid_parent"),
    (lambda d: d["categories"][1].update(parent="finish"), "invalid_parent"),
    (lambda d: d["categories"][0].update(parent="notebooks"), "category_cycle"),
    (lambda d: d["tags"].append(entry("other", "  SEASONAL  ")), "normalization_collision"),
    (lambda d: d["tags"].append(entry("other", "Seasonal Selection")), "normalization_collision"),
    (lambda d: d["tags"].append(entry("other", "Other", slug="seasonal")), "duplicate_slug"),
    (lambda d: d["attributes"][0]["terms"].append(entry("matte", "Other")), "duplicate_key"),
    (lambda d: d["attributes"][0]["terms"].append(entry("other", "MATTE")), "normalization_collision"),
    (lambda d: d["attributes"][0].update(terms="invalid"), "schema"),
    (lambda d: d["attributes"][0].update(woo_id=100), "schema"),
    (lambda d: d["tags"][0].update(name="  "), "empty_label"),
    (lambda d: d["tags"][0].update(slug="Bad Slug"), "schema"),
    (lambda d: d["tags"][0].update(slug="seasonal\n"), "schema"),
    (lambda d: d["tags"][0].update(name="Seasonal\n"), "schema"),
    (lambda d: d["categories"][0].update(name="A > B"), "category_separator"),
])
def test_invalid_definitions(authored, document, mutation, code):
    root, source = authored
    mutation(document)
    save(source, document)
    result = registry.load_registry(root)
    assert result.status == "invalid" and result.issues[0].code == code


def test_unicode_collision_and_scoped_category_names(authored, document):
    root, source = authored
    document["tags"] = [entry("first", "Café"), entry("second", "Cafe\u0301")]
    save(source, document)
    assert registry.load_registry(root).issues[0].code == "normalization_collision"
    document["tags"] = []
    document["categories"].extend([entry("craft", "Craft", parent=None),
                                    entry("craft-notebooks", "Notebooks", parent="craft")])
    other = deepcopy(document["attributes"][0])
    other.update(key="texture", name="Texture", slug="texture")
    document["attributes"].append(other)  # Same term keys valid in different attributes.
    save(source, document)
    assert registry.load_registry(root).available


@pytest.mark.parametrize("location", ["root", "ancestor", "file"])
def test_symlinks_rejected_even_for_readable_targets(authored, tmp_path, location):
    root, source = authored
    alias = tmp_path / "alias"
    if location == "file":
        real = root / "real.json"
        source.rename(real)
        source.symlink_to(real)
        target = root
    elif location == "root":
        alias.symlink_to(root, target_is_directory=True)
        target = alias
    else:
        alias.symlink_to(tmp_path, target_is_directory=True)
        target = alias / "taxonomy"
    result = registry.load_registry(target)
    assert not result.available and result.issues[0].code == "unsafe_path"


def test_traversal_and_overlap_rejected(authored, tmp_path):
    root, _ = authored
    for unsafe in ["relative", "/", str(root / ".." / "taxonomy")]:
        assert registry.load_registry(unsafe).status == "invalid_root"
    for excluded in [root, tmp_path, root / "nested"]:
        assert registry.load_registry(root, excluded_roots=[excluded]).issues[0].code == "root_overlap"


def test_read_only_registry_still_available(authored):
    root, source = authored
    source.chmod(0o444)
    assert registry.load_registry(root).status == "read_only"
    assert registry.load_registry(root).available


def test_permission_failure_does_not_expose_host_path(authored, monkeypatch):
    root, _ = authored
    original = os.open
    def deny(path, *args, **kwargs):
        if path == "registry.json":
            raise PermissionError(13, f"private host detail {root}")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(registry.os, "open", deny)
    result = registry.load_registry(root)
    assert result.status == "inaccessible" and str(root) not in repr(result)


def test_size_structure_definition_and_hierarchy_bounds(authored, document, monkeypatch):
    root, source = authored
    monkeypatch.setattr(registry, "MAX_BYTES", 50)
    assert registry.load_registry(root).issues[0].code == "size_limit"
    monkeypatch.setattr(registry, "MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(registry, "MAX_DEFINITIONS", 1)
    assert registry.load_registry(root).issues[0].code == "definition_limit"
    monkeypatch.setattr(registry, "MAX_DEFINITIONS", 2000)
    document["categories"] = [entry(f"cat-{i}", str(i), parent=f"cat-{i-1}" if i else None) for i in range(33)]
    save(source, document)
    assert registry.load_registry(root).issues[0].code == "hierarchy_limit"
    source.write_text('[' * 40 + '0' + ']' * 40)
    assert registry.load_registry(root).issues[0].code == "structure_limit"


def test_fifo_is_rejected_without_blocking(authored):
    root, source = authored
    source.unlink()
    os.mkfifo(source)
    assert registry.load_registry(root).issues[0].code == "not_regular_file"


def test_config_environment_default_override_and_disabled(monkeypatch):
    monkeypatch.delenv("TAXONOMY_ROOT", raising=False)
    assert runpy.run_path("config.py")["Config"].TAXONOMY_ROOT == "/taxonomy"
    for value in ["/fictional-taxonomy", ""]:
        monkeypatch.setenv("TAXONOMY_ROOT", value)
        assert runpy.run_path("config.py")["Config"].TAXONOMY_ROOT == value


def test_application_startup_does_not_require_or_load_registry(tmp_path, monkeypatch):
    import app as app_module
    from config import Config
    instance = tmp_path / "isolated-instance"
    instance.mkdir()
    flask_class = app_module.Flask
    monkeypatch.setattr(app_module, "Flask", lambda *a, **kw: flask_class(*a, instance_path=str(instance), **kw))
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{instance / 'site.db'}")
    monkeypatch.setattr(Config, "TAXONOMY_ROOT", str(tmp_path / "missing-taxonomy"))
    def unexpected(*args, **kwargs):
        pytest.fail("Startup must not load taxonomy")
    with monkeypatch.context() as guard:
        guard.setattr(registry, "load_registry", unexpected)
        app = app_module.create_app()
        assert app.test_client().get("/setup").status_code == 200
    with app.app_context():
        assert registry.load_configured_registry().status == "root_missing"
        app.config["TAXONOMY_ROOT"] = str(instance)
        assert registry.load_configured_registry().issues[0].code == "root_overlap"
        from app.models import Settings
        app_module.db.session.add(Settings(product_folder=str(tmp_path / "catalogue")))
        app_module.db.session.commit()
        app.config["TAXONOMY_ROOT"] = str(tmp_path / "catalogue" / "taxonomy")
        assert registry.load_configured_registry().issues[0].code == "root_overlap"
        app_module.db.session.remove()
    assert not (tmp_path / "missing-taxonomy").exists()
