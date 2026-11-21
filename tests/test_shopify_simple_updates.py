"""Simple updates use fictional in-memory Shopify responses, never real writes."""
import copy
import json

import pytest
from PIL import Image

from app import db, shopify_drafts as drafts
from app.models import CatalogueOperation, ShopifyProductIdentity, ProductImage
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


@pytest.mark.parametrize("kind", ["missing", "id", "nonce", "variant", "inventory", "incomplete"])
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
    {"sale_price": "-1"}])
def test_invalid_sale_is_not_published(world, values):
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
    assert adapter.calls[len(previous):] == ["connection_read", "product_read", "dimension_definitions_read"]


def test_alt_scope_is_required_only_for_alt_changes(world):
    product, _, adapter = created(world)
    adapter.test_connection = lambda config: {"shop_id": "gid://shopify/Shop/42", "scopes": ["read_products", "write_products"]}
    assert drafts.preview(product, adapter)[0]["classification"] == "no_change"
    product.images[0].alt_text = "Changed"
    db.session.commit()
    plan, token = drafts.preview(product, adapter)
    assert token is None and any("write_files" in b for b in plan["blockers"])


class MediaUpdateAdapter(UpdateAdapter):
    def __init__(self):
        super().__init__()
        self.next_media = 900
        self.media_failure = None
        self.variant_media = []

    def inspect_variant(self, config, variant_id, after=None):
        self.calls.append('variant_media_read')
        return {'id': variant_id, 'media': {'edges': [
            {'node': {'id': media_id}} for media_id in self.variant_media],
            'pageInfo': {'hasNextPage': False}}}

    def update_simple_variant(self, config, product_id, variant):
        if 'mediaId' in variant:
            self.calls.append('variant_media_attach')
            self.variant_media = list(dict.fromkeys([*self.variant_media, variant['mediaId']]))
            return {'productVariantsBulkUpdate': {'productVariants': [{'id': variant['id']}], 'userErrors': []}}
        return super().update_simple_variant(config, product_id, variant)

    def detach_variant_media(self, config, product_id, variant_id, media_id):
        self.calls.append('variant_media_detach')
        self.variant_media.remove(media_id)
        return {'productVariantDetachMedia': {'product': {'id': product_id}, 'userErrors': []}}

    def stage_images(self, config, images):
        if self.remote is None:
            return super().stage_images(config, images)
        self.calls.append('media_stage')
        assert ShopifyProductIdentity.query.one().shopify_product_id == self.remote['id']
        return {'stagedUploadsCreate': {'stagedTargets': [{
            'url': 'https://storage.googleapis.com/upload',
            'resourceUrl': f'https://storage.googleapis.com/new/{self.next_media}',
            'parameters': []}], 'userErrors': []}}

    def upload_image(self, config, target, image, content):
        self.calls.append('binary_upload')
        assert content and image['sha256'] == __import__('hashlib').sha256(content).hexdigest()

    def add_product_media(self, config, product_id, media):
        self.calls.append('media_add')
        assert product_id == self.remote['id'] and media['mediaContentType'] == 'IMAGE'
        node = {'id': f'gid://shopify/MediaImage/{self.next_media}', 'alt': media['alt'],
            'status': 'READY', 'mediaContentType': 'IMAGE',
            'image': {'url': f'https://cdn.shopify.com/added-{self.next_media}.png'}}
        self.next_media += 1
        self.remote['media']['nodes'].append(node)
        if self.media_failure == 'lost_add':
            raise ShopifyError('Media add response lost')
        return {'productUpdate': {'product': {'id': product_id,
            'media': {'nodes': copy.deepcopy(self.remote['media']['nodes']),
                      'pageInfo': {'hasNextPage': False}}}, 'userErrors': []}}

    def remove_product_media(self, config, product_id, media_id):
        self.calls.append('media_remove')
        assert product_id == self.remote['id']
        self.remote['media']['nodes'] = [m for m in self.remote['media']['nodes'] if m['id'] != media_id]
        if self.media_failure == 'lost_remove':
            raise ShopifyError('Media removal response lost')
        return {'productDeleteMedia': {'deletedMediaIds': [media_id], 'mediaUserErrors': []}}


def media_created(world):
    product = world[0]
    adapter = MediaUpdateAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    adapter.calls.clear()
    return product, row, adapter


@pytest.mark.parametrize('kind', ['added', 'replaced', 'removed'])
def test_reviewed_media_changes_reuse_existing_identity(world, kind):
    product, row, adapter = media_created(world)
    original_id = row.shopify_product_id
    before = [m['id'] for m in adapter.remote['media']['nodes']]
    if kind == 'added':
        Image.new('RGB', (5, 5), 'green').save(world[1] / 'third.png')
        product.images.append(ProductImage(url='third.png', position=2, alt_text='Third'))
    elif kind == 'replaced':
        Image.new('RGB', (5, 5), 'blue').save(world[1] / 'original.png')
    else:
        removed = product.images[-1]
        product.images.remove(removed)
        db.session.delete(removed)
        (world[1] / 'second.jpg').unlink()
    db.session.commit()
    plan, token = drafts.preview(product, adapter)
    assert token and plan['classification'] == 'update'
    assert len(plan['media_actions']['add']) == int(kind != 'removed')
    assert len(plan['media_actions']['retire']) == int(kind != 'added')
    assert not {'media_stage', 'binary_upload', 'media_add', 'media_remove'} & set(adapter.calls)
    result = drafts.confirm(token, True, adapter)
    assert result.state == 'verified', drafts.evidence(result).get('error')
    assert result.shopify_product_id == original_id
    assert len(adapter.remote['media']['nodes']) == len(product.images)
    assert adapter.calls.count('media_add') == int(kind != 'removed')
    assert adapter.calls.count('media_remove') == int(kind != 'added')
    assert not drafts.evidence(result).get('media_sync', {}).get('uploaded')
    if kind == 'added': assert before == [m['id'] for m in adapter.remote['media']['nodes'][:2]]
    if kind == 'replaced': assert before[0] not in [m['id'] for m in adapter.remote['media']['nodes']]
    if kind == 'removed': assert before[-1] not in [m['id'] for m in adapter.remote['media']['nodes']]
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_uncertain_media_add_keeps_identity_and_blocks_duplicate(world):
    product, row, adapter = media_created(world)
    Image.new('RGB', (5, 5), 'blue').save(world[1] / 'original.png')
    _, token = drafts.preview(product, adapter)
    adapter.media_failure = 'lost_add'
    result = drafts.confirm(token, True, adapter)
    assert result.state == 'uncertain'
    assert adapter.calls.count('media_add') == 1
    with pytest.raises(ShopifyError, match='Recovery required'):
        drafts.preview(product, adapter)
    assert adapter.calls.count('media_add') == 1


def test_same_size_source_replacement_is_reviewed_and_verified(world):
    product, row, adapter = media_created(world)
    path = world[1] / 'original.png'
    before = path.read_bytes()
    old_id = adapter.remote['media']['nodes'][0]['id']
    Image.new('RGB', (5, 5), (1, 0, 0)).save(path)
    assert len(path.read_bytes()) == len(before) and path.read_bytes() != before
    plan, token = drafts.preview(product, adapter)
    assert token and plan['media_actions']['add'][0]['source'].endswith('/original.png')
    assert drafts.confirm(token, True, adapter).state == 'verified'
    assert old_id not in [node['id'] for node in adapter.remote['media']['nodes']]
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_uncertain_media_removal_never_replays_delete(world):
    product, row, adapter = media_created(world)
    removed = product.images[-1]
    product.images.remove(removed)
    db.session.delete(removed)
    (world[1] / 'second.jpg').unlink()
    db.session.commit()
    _, token = drafts.preview(product, adapter)
    adapter.media_failure = 'lost_remove'
    assert drafts.confirm(token, True, adapter).state == 'uncertain'
    assert adapter.calls.count('media_remove') == 1
    with pytest.raises(ShopifyError, match='Recovery required'):
        drafts.preview(product, adapter)
    assert adapter.calls.count('media_remove') == 1


@pytest.mark.parametrize('kind', ['replaced', 'removed'])
def test_simple_variant_image_link_is_verified_before_old_media_deletion(world, kind):
    product, row, adapter = media_created(world)
    old = adapter.remote['media']['nodes'][0]['id']
    adapter.variant_media = [old]
    if kind == 'replaced':
        Image.new('RGB', (5, 5), 'blue').save(world[1] / 'original.png')
    else:
        removed = product.images[0]
        product.images.remove(removed)
        db.session.delete(removed)
        (world[1] / 'original.png').unlink()
        db.session.commit()
    plan, token = drafts.preview(product, adapter)
    assert token and plan['simple_variant_media'] == [old]
    assert drafts.confirm(token, True, adapter).state == 'verified'
    assert old not in [node['id'] for node in adapter.remote['media']['nodes']]
    assert adapter.variant_media == ([adapter.remote['media']['nodes'][0]['id']] if kind == 'replaced' else [])
    assert adapter.calls.index('variant_media_detach') < adapter.calls.index('media_remove')
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_unreviewed_simple_variant_image_blocks_managed_removal(world):
    product, _, adapter = media_created(world)
    adapter.variant_media = ['gid://shopify/MediaImage/999']
    Image.new('RGB', (5, 5), 'blue').save(world[1] / 'original.png')
    with pytest.raises(ShopifyError, match='unreviewed media'):
        drafts.preview(product, adapter)
    assert not {'media_add', 'media_remove', 'variant_media_detach'} & set(adapter.calls)


def test_resumed_media_checkpoint_does_not_readd_removed_image(world):
    from app.shopify_media_update import prepare, retire
    product, row, adapter = media_created(world)
    removed = product.images[-1]
    product.images.remove(removed)
    db.session.delete(removed)
    (world[1] / 'second.jpg').unlink()
    db.session.commit()
    plan, token = drafts.preview(product, adapter)
    data = drafts.evidence(row)
    data['plan'] = plan
    old_id = plan['media_actions']['retire'][0]['id']
    data['media_sync'] = {'uploaded': {}, 'added': {}, 'removed': [old_id]}
    adapter.remote['media']['nodes'] = [node for node in adapter.remote['media']['nodes'] if node['id'] != old_id]
    before = adapter.calls[:]
    resolved = prepare(row, data, product, drafts.configuration(), adapter)
    retire(row, data, drafts.configuration(), adapter, resolved)
    assert adapter.calls[len(before):] == ['product_read']
    assert resolved == [node['id'] for node in adapter.remote['media']['nodes']]


def test_review_page_shows_image_change_and_authenticated_thumbnail(world, preview_app, monkeypatch):
    product, _, adapter = media_created(world)
    Image.new('RGB', (5, 5), 'blue').save(world[1] / 'original.png')
    original_preview = drafts.preview
    monkeypatch.setattr(drafts, 'preview',
        lambda selected, **kwargs: original_preview(selected, adapter=adapter, **kwargs))
    response = _client(preview_app).post(
        f'/products/{product.id}/shopify/review', data={'action': 'preview'})
    assert response.status_code == 200
    assert b'Replace with changed original' in response.data
    assert b'original.png' in response.data
    assert b'shopify-review-thumbnail' in response.data
    assert b'/catalogue/products/' not in response.data
    assert not {'media_add', 'media_remove', 'binary_upload'} & set(adapter.calls)


def test_unmanaged_remote_media_blocks_review_without_writes(world):
    product, _, adapter = media_created(world)
    adapter.remote['media']['nodes'].append(dict(adapter.remote['media']['nodes'][0], id='gid://shopify/MediaImage/999'))
    with pytest.raises(ShopifyError, match='differs from retained managed media'):
        drafts.preview(product, adapter)
    assert not {'media_stage', 'binary_upload', 'media_add', 'media_remove'} & set(adapter.calls)


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
    assert adapter.calls[len(previous):] == ["connection_read", "product_read"] + (["dimension_definitions_read"] if failure == "after" else [])
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
    assert response.status_code == 200 and b"No Change" in response.data and b"Confirm reviewed changes" not in response.data
    change(world, description="Changed")
    response = client.post("/shopify/drafts", data={"action": "preview", "product_id": product.id})
    assert response.status_code == 200 and b"Confirm reviewed changes" in response.data and TOKEN.encode() not in response.data
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
