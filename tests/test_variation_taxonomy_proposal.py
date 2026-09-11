import html
import json
import re

import pytest

from app import db
from app.models import Product
from app.taxonomy_workspace import validate, RegistryEditError
from app.variation_taxonomy_proposal import proposal
from test_m56_product_taxonomy import world
from test_phase3_woo_publish_preview import preview_app, _client


def setup(app, monkeypatch, tmp_path, mode="new"):
    specs = [("Build Type", ["Flat 3 Layer", "3d 3 Layer"]),
             ("Design Style", ["Farm", "Fireplace", "Horse", "Street", "Town", "Train", "Trees"])]
    pid, woo, source, root, document, _ = world(app, monkeypatch, tmp_path, variable=True, driver_spec=specs)
    data = json.loads((root / "registry.json").read_text())
    if mode == "new": data["attributes"] = data["attributes"][:1]
    if mode == "partial": data["attributes"][1]["terms"] = data["attributes"][1]["terms"][:1]
    (root / "registry.json").write_bytes(validate(data))
    return pid, woo, source, root, document


@pytest.mark.parametrize("mode, additions", [("new", 11), ("partial", 1), ("complete", 0)])
def test_proposal_review_and_save(preview_app, monkeypatch, tmp_path, mode, additions):
    with preview_app.app_context():
        pid, woo, source, root, _ = setup(preview_app, monkeypatch, tmp_path, mode)
        product = db.session.get(Product, pid)
        skus = [v.sku for v in product.variations]
        before = source.read_bytes(), (root / "registry.json").read_bytes()
        result = proposal(product)
        assert result["additions"] == additions and result["children"] == 14
        assert [g["name"] for g in result["groups"]] == ["Build Type", "Design Style"]
        assert [t["name"] for t in result["groups"][0]["terms"]] == ["Flat 3 Layer", "3d 3 Layer"]
        assert [t["name"] for t in result["groups"][1]["terms"]] == ["Farm", "Fireplace", "Horse", "Street", "Town", "Train", "Trees"]
        assert (source.read_bytes(), (root / "registry.json").read_bytes()) == before
        client = _client(preview_app)
        url = f"/taxonomy/variation-proposal/{pid}"
        page = client.get(url)
        assert page.status_code == 200
        if additions:
            token = html.unescape(re.search(r'name="review" value="([^"]+)"', page.text)[1])
            assert client.post(url, data={"review": token}).status_code == 409
            saved = client.post(url, data={"review": token, "acknowledge": "yes"})
            assert saved.status_code == 302 and saved.location.endswith("/taxonomy/sync/attributes")
            assert proposal(product)["additions"] == 0
            assert list(root.glob(".registry-backup-*.json"))
        else:
            assert "Approve registry additions" not in page.text
        assert source.read_bytes() == before[0]
        assert [v.sku for v in product.variations] == skus
        assert not woo.methods and not woo.writes


@pytest.mark.parametrize("change", ["registry", "metadata", "child", "sku"])
def test_stale_proposal_refused(preview_app, monkeypatch, tmp_path, change):
    with preview_app.app_context():
        pid, woo, source, root, document = setup(preview_app, monkeypatch, tmp_path)
        client = _client(preview_app); url = f"/taxonomy/variation-proposal/{pid}"
        token = html.unescape(re.search(r'name="review" value="([^"]+)"', client.get(url).text)[1])
        if change == "registry":
            (root / "registry.json").write_bytes((root / "registry.json").read_bytes() + b"\n")
        elif change == "metadata":
            document["description"] = "Changed"; source.write_text(json.dumps(document))
        else:
            child = db.session.get(Product, pid).variations[0]
            if change == "sku": child.sku = "CHANGED"
            else: child.attributes[0].value = "Wrong"
            db.session.commit()
        before = (root / "registry.json").read_bytes()
        assert client.post(url, data={"review": token, "acknowledge": "yes"}).status_code == 409
        assert (root / "registry.json").read_bytes() == before
        assert not woo.methods


@pytest.mark.parametrize("change", ["legacy", "simple", "missing_child", "conflict"])
def test_unsafe_proposal_refused(preview_app, monkeypatch, tmp_path, change):
    with preview_app.app_context():
        pid, _, source, root, document = setup(preview_app, monkeypatch, tmp_path)
        product = db.session.get(Product, pid)
        if change == "legacy":
            document.pop("variation_attributes"); source.write_text(json.dumps(document))
        elif change == "simple": product.product_type = "simple"
        elif change == "missing_child": product.variations.pop()
        else:
            data = json.loads((root / "registry.json").read_text())
            data["attributes"][0]["slug"] = "build-type"
            (root / "registry.json").write_bytes(validate(data))
        with pytest.raises(RegistryEditError): proposal(product)


def test_auth_csrf_and_detail(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        pid, _, _, root, _ = setup(preview_app, monkeypatch, tmp_path)
        url = f"/taxonomy/variation-proposal/{pid}"
        before = (root / "registry.json").read_bytes()
        assert preview_app.test_client().get(url).status_code == 401
        from flask import g
        g.pop("_login_user", None)
        client = _client(preview_app)
        assert "Review &amp; add variation taxonomy" in client.get(f"/products/{pid}").text
        preview_app.config["WTF_CSRF_ENABLED"] = True
        assert client.post(url, data={"acknowledge": "yes"}).status_code == 400
        assert (root / "registry.json").read_bytes() == before
