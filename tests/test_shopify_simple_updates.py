"""Simple updates use fictional in-memory Shopify responses, never real writes."""
import copy
import json

import pytest
from PIL import Image

from app import db, shopify_drafts as drafts
from app.models import CatalogueOperation, ShopifyProductIdentity
from app.shopify_connection import ShopifyError, ShopifyAdapter, ShopifyConfiguration, SIMPLE_QUERY, UPDATE_QUERY, FILES_QUERY, ORDER_QUERY, COUNTRIES_QUERY
from app.shopify_simple_fields import weight_grams
from test_shopify_drafts import FakeDraftAdapter, world
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_foundation import isolated_transport, Session, Response, TOKEN


class UpdateAdapter(FakeDraftAdapter):
    def __init__(self):
        super().__init__()
        self.update_failure = None
        self.inputs = []
        self.deferred_order = False

    def test_connection(self, config):
        result = super().test_connection(config)
        result["scopes"].append("write_files")
        return result

    def update_simple_product(self, config, product):
        self.calls.append("product_update")
        row = ShopifyProductIdentity.query.one()
        assert row.state == "updating" and drafts.evidence(row)["update_attempted"]
        assert db.session.get(CatalogueOperation, row.operation_id).status == "running"
        assert product["id"] == row.shopify_product_id
        self.inputs.append(copy.deepcopy(product))
        if self.update_failure == "before":
            raise ShopifyError("Update timed out; reconcile before another write.")
        for key, value in product.items():
            if key == "metafields":
                for item in value:
                    self.remote[item["key"].removeprefix("product_")] = dict(item, id="gid://shopify/Metafield/500")
            elif key == "seo":
                self.remote["seo"].update(value)
            else:
                self.remote[key] = value
        if self.update_failure == "after":
            raise ShopifyError("Update result was lost; reconcile before another write.")
        return {"productUpdate": {"product": {"id": self.remote["id"]}, "userErrors": []}}

    def update_simple_variant(self, config, product_id, variant):
        self.inputs.append(copy.deepcopy(variant))
        if self.update_failure == "partial":
            raise ShopifyError("Variant write uncertain.")
        return super().update_simple_variant(config, product_id, variant)

    def update_media_alt(self, config, files):
        self.calls.append("alt_update")
        self.inputs.append(copy.deepcopy(files))
        for file in files:
            next(m for m in self.remote["media"]["nodes"] if m["id"] == file["id"])["alt"] = file["alt"]
        return {"fileUpdate": {"userErrors": [], "files": files}}

    def reorder_media(self, config, product_id, ids):
        self.calls.append("order_update")
        if not self.deferred_order:
            nodes = {m["id"]: m for m in self.remote["media"]["nodes"]}
            self.remote["media"]["nodes"] = [nodes[i] for i in ids]
        return {"productReorderMedia": {"job": {"id": "gid://shopify/Job/6dfa599a-a426-4030-8f10-6564abc465f9"}, "mediaUserErrors": []}}


def created(world):
    product, _, _ = world
    adapter = UpdateAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified"
    adapter.calls.clear()
    adapter.inputs.clear()
    return product, row, adapter


def change(world, **values):
    path = world[2]
    path.write_text(json.dumps({**json.loads(path.read_text()), **values}))


def test_complete_update_retains_id_media_and_no_change(world):
    product, row, adapter = created(world)
    remote_id, media_ids = row.shopify_product_id, [m["id"] for m in adapter.remote["media"]["nodes"]]
    source_bytes = world[1].joinpath("original.png").read_bytes()
    change(world, description="<p>Full description</p>", price="4.50", sale_price="3.50", weight="50",
        requires_shipping=True, country_of_origin="GB", vendor="Tender Loving Creations",
        tags=["Card", "Birthday"], meta_title="Birthday Card", meta_description="Handmade card",
        dimensions={"length": "155", "width": "155", "height": "3"})
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "update" and plan["remote_id"] == remote_id
    assert not any("write" in c or "update" in c for c in adapter.calls)
    result = drafts.confirm(token, True, adapter)
    assert result.state == "verified" and result.shopify_product_id == remote_id
    assert [m["id"] for m in adapter.remote["media"]["nodes"]] == media_ids
    assert "stage_write" not in adapter.calls and "product_create" not in adapter.calls
    assert source_bytes == world[1].joinpath("original.png").read_bytes()
    variant = adapter.remote["simpleVariants"]["nodes"][0]
    assert variant["price"] == "3.50" and variant["compareAtPrice"] == "4.50"
    assert variant["inventoryItem"]["measurement"]["weight"] == {"value": 50.0, "unit": "GRAMS"}
    assert variant["inventoryItem"]["countryCodeOfOrigin"] == "GB"
    assert variant["inventoryItem"]["tracked"] is False
    assert all("tracked" not in item.get("inventoryItem", {}) for item in adapter.inputs if isinstance(item, dict))
    assert json.loads(adapter.remote["length"]["value"]) == {"unit": "millimeters", "value": 155.0}
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "no_change" and token is None
    assert not any(f["changed"] for f in plan["fields"])
    assert db.session.get(CatalogueOperation, result.operation_id).status == "succeeded"
    assert product.woo_id is None and TOKEN not in result.media_evidence_json


def test_legacy_verified_identity_can_be_reviewed_without_rewriting_store_mapping(world):
    product, row, adapter = created(world)
    data = drafts.evidence(row)
    for key in ("managed", "warnings", "unsupported", "fields"):
        data["plan"].pop(key, None)
    data.pop("variant_id", None)
    data.pop("inventory_item_id", None)
    drafts.save(row, data)
    before = row.store_key, row.store_domain, row.shopify_product_id
    change(world, weight="50", requires_shipping=True)
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    assert row.state == "verified" and before == (row.store_key, row.store_domain, row.shopify_product_id)


def test_complete_simple_creation_supports_shipping_seo_dimensions_and_sale(world):
    product = world[0]
    change(world, price="4.00", sale_price="3.50", weight="50", requires_shipping=True,
        country_of_origin="GB", vendor="TLC", tags=["Card"], meta_title="Card",
        meta_description="Description", dimensions={"length": "155"})
    adapter = UpdateAdapter()
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "create"
    row = drafts.confirm(token, True, adapter)
    assert row.state == "verified"
    assert adapter.remote["vendor"] == "TLC" and adapter.remote["seo"]["title"] == "Card"
    assert adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["countryCodeOfOrigin"] == "GB"
    assert drafts.preview(product, adapter)[0]["classification"] == "no_change"


def test_confirm_expired_or_replayed_update_is_rejected(world, monkeypatch):
    product, row, adapter = created(world)
    change(world, description="Changed")
    _, token = drafts.preview(product, adapter)
    with pytest.raises(ShopifyError, match="expired"): drafts.confirm(token + "invalid", True, adapter)
    drafts.confirm(token, True, adapter)
    writes = adapter.calls.count("product_update")
    with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
    assert adapter.calls.count("product_update") == writes and row.state == "verified"


def test_confirmation_only_mutates_reviewed_changed_fields(world):
    product, _, adapter = created(world)
    change(world, description="New description")
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    assert adapter.inputs == [{"id": "gid://shopify/Product/100", "descriptionHtml": "New description"}]
    assert "variant_write" not in adapter.calls and "alt_update" not in adapter.calls


def test_source_change_after_reservation_before_write_restores_verified_evidence(world, monkeypatch):
    product, row, adapter = created(world)
    original_operation, original_digest = row.operation_id, row.reviewed_digest
    change(world, description="Reviewed")
    _, token = drafts.preview(product, adapter)
    original_save = drafts.save
    def source_change(record, data, state=None):
        original_save(record, data, state)
        if state == "pending":
            change(world, description="Changed after reservation")
    monkeypatch.setattr(drafts, "save", source_change)
    with pytest.raises(ShopifyError, match="changed during execution"):
        drafts.confirm(token, True, adapter)
    assert row.state == "verified" and drafts.evidence(row)["stage"] == "verified"
    assert row.operation_id == original_operation and row.reviewed_digest == original_digest
    assert "product_update" not in adapter.calls
    failed = db.session.get(CatalogueOperation, drafts.evidence(row)["last_failed_review_operation_id"])
    assert failed.status == "failed"


def test_failed_verification_and_busy_reconciliation_never_clear_recovery(world):
    product, row, adapter = created(world)
    change(world, description="Updated")
    _, token = drafts.preview(product, adapter)
    original = adapter.read_simple
    count = [0]
    def failed_after_write(*args):
        count[0] += 1
        if count[0] == 2:
            raise ShopifyError("Readback timed out")
        return original(*args)
    adapter.read_simple = failed_after_write
    drafts.confirm(token, True, adapter)
    assert row.state == "uncertain"
    from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation, CatalogueOperationActive
    lease = acquire_catalogue_operation("product_update")
    try:
        with pytest.raises(CatalogueOperationActive): drafts.reconcile(row, adapter=adapter)
        assert row.state == "uncertain"
    finally:
        finish_catalogue_operation(lease.id, status="succeeded")
    adapter.read_simple = original
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "verified"


@pytest.mark.parametrize("change_kind", ["local", "remote", "credentials", "image"])
def test_stale_update_confirmation_never_writes(world, monkeypatch, change_kind):
    product, row, adapter = created(world)
    change(world, description="Changed")
    _, token = drafts.preview(product, adapter)
    if change_kind == "local": change(world, description="Changed again")
    elif change_kind == "remote": adapter.remote["vendor"] = "External edit"
    elif change_kind == "credentials": monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "different_fictional_token")
    else: Image.new("RGB", (5, 5), "blue").save(world[1] / "original.png")
    with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
    assert "product_update" not in adapter.calls and "variant_write" not in adapter.calls
    assert row.state == "verified"


@pytest.mark.parametrize("kind", ["missing", "id", "nonce", "variant", "inventory", "active", "incomplete"])
def test_untrusted_remote_never_becomes_create_or_update(world, kind):
    product, _, adapter = created(world)
    if kind == "missing": adapter.remote = None
    elif kind == "id": adapter.remote["id"] = "gid://shopify/Product/999"
    elif kind == "nonce": adapter.remote["metafield"]["value"] = "different"
    elif kind == "variant": adapter.remote["simpleVariants"]["nodes"][0]["id"] = "gid://shopify/ProductVariant/999"
    elif kind == "inventory": adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["id"] = "gid://shopify/InventoryItem/999"
    elif kind == "active": adapter.remote["status"] = "ACTIVE"
    else: adapter.remote["simpleVariants"]["pageInfo"]["hasNextPage"] = True
    with pytest.raises(ShopifyError): drafts.preview(product, adapter)
    assert "product_create" not in adapter.calls and "product_update" not in adapter.calls


def test_wrong_store_and_changed_source_identity_remain_protected(world):
    product, row, adapter = created(world)
    adapter.test_connection = lambda config: {"shop_id": "gid://shopify/Shop/99", "scopes": ["read_products", "write_products"]}
    with pytest.raises(ShopifyError): drafts.reconcile(row, adapter=adapter)
    with pytest.raises(ShopifyError, match="another Shopify store"): drafts.preview(product, adapter)
    # A different endpoint must not automatically copy an already mapped product.
    adapter = UpdateAdapter()
    adapter.remote = {"id": "gid://shopify/Product/100"}
    row.source_identity = "other/source"
    db.session.commit()
    with pytest.raises(ShopifyError, match="source/SKU"): drafts.preview(product, adapter)


@pytest.mark.parametrize("value", ["UK", "ZZ", "XX"])
def test_country_must_be_supported_by_shopify(world, value):
    product, _, adapter = created(world)
    change(world, country_of_origin=value)
    with pytest.raises(ShopifyError, match="CountryCode"): drafts.preview(product, adapter)


@pytest.mark.parametrize("values", [{"sale_price": "3.50"}, {"sale_price": "4.00"},
    {"sale_price": "-1"}, {"sale_price": "2.00", "sale_start_date": "2026-10-01"}])
def test_invalid_or_scheduled_sale_is_not_published(world, values):
    product, _, adapter = created(world)
    change(world, **values)
    with pytest.raises(ShopifyError): drafts.preview(product, adapter)
    assert "product_update" not in adapter.calls


def test_sale_removal_restores_regular_price_and_clears_compare_at(world):
    product, row, adapter = created(world)
    change(world, sale_price="2.50")
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    change(world, sale_price="")
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    variant = adapter.remote["simpleVariants"]["nodes"][0]
    assert variant["price"] == "3.50" and variant["compareAtPrice"] is None
    assert row.state == "verified"


def test_missing_optional_fields_do_not_clear_remote_values(world):
    product, _, adapter = created(world)
    adapter.remote.update(vendor="Remote vendor", seo={"title": "Remote SEO", "description": "Remote description"})
    inventory = adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]
    inventory.update(countryCodeOfOrigin="CN", requiresShipping=False, tracked=True)
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "update" and token
    assert [f["field"] for f in plan["fields"] if f["changed"]] == ["tracked"]
    assert "countryCodeOfOrigin" not in plan["managed"]["variant"]["inventoryItem"]
    drafts.confirm(token, True, adapter)
    assert inventory["countryCodeOfOrigin"]=="CN" and inventory["requiresShipping"] is False
    assert adapter.remote["vendor"]=="Remote vendor" and adapter.remote["seo"]["title"]=="Remote SEO"


def test_dimensions_type_conflict_blocks_before_write(world):
    product, _, adapter = created(world)
    change(world, dimensions={"length": "155"})
    adapter.remote["length"] = {"type": "single_line_text_field", "value": "155"}
    with pytest.raises(ShopifyError, match="incompatible type"): drafts.preview(product, adapter)


@pytest.mark.parametrize("kind", ["tags", "weight", "dimension", "image"])
def test_incomplete_managed_read_evidence_never_claims_no_change(world, kind):
    product, _, adapter = created(world)
    if kind == "tags":
        change(world, tags=["Card"])
        adapter.remote["tags"] = None
    elif kind == "weight":
        change(world, weight="50")
        adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["measurement"]["weight"] = {"value": "NaN", "unit": "GRAMS"}
    elif kind == "dimension":
        change(world, dimensions={"length": "155"})
        adapter.remote.pop("length")
    else:
        adapter.remote["media"]["nodes"][0]["image"] = "incomplete"
    with pytest.raises(ShopifyError): drafts.preview(product, adapter)
    assert "product_update" not in adapter.calls


def test_weight_and_dimensions_compare_across_equivalent_units(world):
    product, row, adapter = created(world)
    change(world, weight="50", dimensions={"length": "155"})
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    adapter.remote["simpleVariants"]["nodes"][0]["inventoryItem"]["measurement"]["weight"] = {"value": 0.05, "unit": "KILOGRAMS"}
    adapter.remote["length"]["value"] = json.dumps({"value": 15.5, "unit": "cm"})
    plan, token = drafts.preview(product, adapter)
    assert plan["classification"] == "no_change" and token is None
    assert weight_grams({"value": 1, "unit": "POUNDS"}) == weight_grams({"value": 16, "unit": "OUNCES"})


def test_media_order_alt_and_processing_use_existing_ids(world, preview_app):
    product, row, adapter = created(world)
    ids = [m["id"] for m in adapter.remote["media"]["nodes"]]
    product.images[0].position, product.images[1].position = 1, 0
    product.images[0].alt_text = "New alt"
    db.session.commit()
    plan, token = drafts.preview(product, adapter)
    assert plan["media_actions"]["ids"] == ids[::-1]
    adapter.deferred_order = True
    drafts.confirm(token, True, adapter)
    assert row.state == "processing"
    assert drafts.evidence(row)["stage"] == "media_reordering"
    page = _client(preview_app).get(f"/shopify/drafts/identity/{row.id}")
    assert page.status_code == 200
    first_row = page.data.split(b"<tbody>")[1].split(b"</tr>")[0]
    assert b"second.jpg" in first_row and ids[1].encode() in first_row
    assert "binary_upload" not in adapter.calls and "stage_write" not in adapter.calls
    adapter.remote["media"]["nodes"].reverse()
    previous = adapter.calls[:]
    drafts.reconcile(row, adapter=adapter)
    assert row.state == "verified"
    assert adapter.calls[len(previous):] == ["connection_read", "product_read"]


def test_alt_scope_is_required_only_for_alt_changes(world):
    product, _, adapter = created(world)
    adapter.test_connection = lambda config: {"shop_id": "gid://shopify/Shop/42", "scopes": ["read_products", "write_products"]}
    assert drafts.preview(product, adapter)[0]["classification"] == "no_change"
    product.images[0].alt_text = "Changed"
    db.session.commit()
    plan, token = drafts.preview(product, adapter)
    assert token is None and any("write_files" in b for b in plan["blockers"])


@pytest.mark.parametrize("kind", ["bytes", "removed", "extra_remote"])
def test_media_replacement_removal_is_explicitly_unsupported(world, kind):
    product, _, adapter = created(world)
    if kind == "bytes": Image.new("RGB", (5, 5), "blue").save(world[1] / "original.png")
    elif kind == "removed": (world[1] / "second.jpg").unlink()
    else: adapter.remote["media"]["nodes"].append(dict(adapter.remote["media"]["nodes"][0], id="gid://shopify/MediaImage/999"))
    with pytest.raises(ShopifyError, match="Unsupported|catalogue source"): drafts.preview(product, adapter)
    assert "binary_upload" not in adapter.calls and "product_update" not in adapter.calls


@pytest.mark.parametrize("failure", ["before", "after", "partial"])
def test_uncertain_updates_block_retry_and_reconcile_without_writes(world, failure):
    product, row, adapter = created(world)
    change(world, description="Updated", **({"price": "4.00"} if failure == "partial" else {}))
    _, token = drafts.preview(product, adapter)
    adapter.update_failure = failure
    drafts.confirm(token, True, adapter)
    assert row.state == "uncertain" and row.verification_state == "unverified"
    op_id = row.operation_id
    db.session.expire_all()
    row = db.session.get(ShopifyProductIdentity, row.id)
    with pytest.raises(ShopifyError, match="Recovery required"): drafts.preview(product, adapter)
    with pytest.raises(ShopifyError): drafts.confirm(token, True, adapter)
    previous = adapter.calls[:]
    drafts.reconcile(row, adapter=adapter)
    assert adapter.calls[len(previous):] == ["connection_read", "product_read"]
    assert row.state == ("verified" if failure == "after" else "uncertain")
    if failure != "after":
        # Explicit operator correction in Shopify Admin, not a retry by the app.
        adapter.remote["descriptionHtml"] = "Updated"
        if failure == "partial": adapter.remote["simpleVariants"]["nodes"][0]["price"] = "4.00"
        drafts.reconcile(row, adapter=adapter)
        assert row.state == "verified"
    completed=db.session.get(CatalogueOperation, op_id)
    assert completed.status == "succeeded"
    # Complete independent reconciliation supersedes the original outcome without
    # deleting the uncertain attempt, original error or its notification event.
    retained=json.loads(completed.scope)
    assert retained['resolved_previous_outcome']['status']=='partial'
    assert 'recovery_required' in retained['shopify_discord_events']
    assert "product_create" not in adapter.calls


def test_handle_is_reviewed_unique_and_retains_attempt_correlation(world):
    product, row, adapter = created(world)
    nonce = drafts.evidence(row)["plan"]["nonce"]
    adapter.find_draft = lambda config, handle: {"nodes": [], "pageInfo": {"hasNextPage": False}}
    change(world, handle="birthday-card")
    plan, token = drafts.preview(product, adapter)
    assert any(f["field"] == "handle" and f["changed"] for f in plan["fields"])
    drafts.confirm(token, True, adapter)
    assert row.state == "verified" and adapter.remote["handle"] == "birthday-card"
    assert adapter.remote["metafield"]["value"] == nonce
    assert adapter.inputs[-1]["redirectNewHandle"] is False


def test_update_confirmation_auth_csrf_and_no_change_ui(world, monkeypatch, preview_app):
    product, _, adapter = created(world)
    monkeypatch.setattr(drafts, "ShopifyAdapter", lambda: adapter)
    client = _client(preview_app)
    response = client.post("/shopify/drafts", data={"action": "preview", "product_id": product.id})
    assert response.status_code == 200 and b"No Change" in response.data and b"Confirm reviewed update" not in response.data
    change(world, description="Changed")
    response = client.post("/shopify/drafts", data={"action": "preview", "product_id": product.id})
    assert response.status_code == 200 and b"Confirm reviewed update" in response.data and TOKEN.encode() not in response.data
    _, token = drafts.preview(product, adapter)
    with pytest.raises(ShopifyError, match="acknowledgement"): drafts.confirm(token, False, adapter)
    preview_app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/shopify/drafts", data={"action": "confirm", "review": token, "acknowledge": "yes"}).status_code == 400


def test_new_fixed_transports_keep_read_write_allowlists_and_bounded_timeouts(monkeypatch):
    config = ShopifyConfiguration("fictional.myshopify.com", TOKEN)
    adapter = ShopifyAdapter()
    for query in (SIMPLE_QUERY, COUNTRIES_QUERY, UPDATE_QUERY, FILES_QUERY, ORDER_QUERY):
        session = Session(Response({"data": {"result": {}}}))
        monkeypatch.setattr("app.shopify_connection.requests.Session", lambda: session)
        if query in (SIMPLE_QUERY, COUNTRIES_QUERY): adapter._read(config, query, {})
        else: adapter._write(config, query, {})
        assert session.calls[0][1]["timeout"] == (5, 15)
        assert session.calls[0][1]["allow_redirects"] is False
    with pytest.raises(ShopifyError): adapter._read(config, UPDATE_QUERY)
    with pytest.raises(ShopifyError): adapter._write(config, SIMPLE_QUERY, {})
