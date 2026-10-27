"""Bounded variable stages within the existing signed Shopify publishing workflow.

Catalogue projections own SKUs/prices; identity evidence owns remote GIDs. No scan,
allocation, channel publication, deletion or automatic uncertain-write retry.
"""
import copy
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from time import monotonic
from itsdangerous import BadSignature

from app import db, shopify_drafts as drafts, shopify_native_dimensions as native
from app.models import Product, ShopifyProductIdentity
from app.catalogue_images import variation_image_diagnostics
from app.commerce import catalogue_snapshot
from app.taxonomy_assignments import product_document, variation_drivers
from app.shopify_connection import ShopifyAdapter, ShopifyError, ShopifyNativeWriteRejected, _gid
from app import shopify_simple_fields as fields
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

MAX_VARIANTS = 32


def gallery_diagnostics(product, parents):
    result, seen = [], set()
    for row in [*parents, *[image for v in sorted(product.variations, key=lambda v: (v.menu_order or 0, v.id))
            if v.catalogue_status == "active" for image in variation_image_diagnostics(v, include_parent_fallback=False)]]:
        if not row["source_exists"] or not row["source_reference"]:
            raise ShopifyError("Every variable gallery/variant image must resolve to its original authorised source.")
        if row["source_reference"] not in seen:
            row = dict(row, position=len(result))
            result.append(row)
            seen.add(row["source_reference"])
    return result


def source_parts(product):
    """No byte hashing; eager loaded by workspace callers, no SKU allocation."""
    authored=product_document(product)
    # Existing inheritance merges these as sets. Canonicalise only unordered
    # assignments, while recording option order separately (never sort drivers).
    for key in ("tags","categories"):
        if isinstance(authored.get(key),list): authored[key]=sorted(authored[key])
    return {"authored": authored, "driver_order": variation_drivers(authored), "variations": [
        {"identity": v.source_identity, "sku": v.sku, "status": v.catalogue_status,
         "price": v.regular_price, "sale": v.sale_price, "dates": [v.sale_start, v.sale_end],
         "weight": v.weight, "dimensions": [v.length, v.width, v.height], "visible": v.visible,
         "attributes": sorted((a.name, a.value) for a in v.attributes),
         "images": [(i.url, i.position, i.alt_text) for i in v.images]}
        for v in sorted(product.variations, key=lambda v: (v.source_identity or "", v.sku or ""))]}


def packed_policy(dimensions, packaging=None):
    policy = {"enabled": native.flag("SHOPIFY_NATIVE_DIMENSIONS_ENABLED"),
        "use_product_dimensions": False, 'source_policy':2, "desired": None, "reason": ""}
    if packaging and native.has_packed(packaging):
        policy.update(native.explicit_packed(packaging))
        if not policy['enabled']:
            policy['reason'] = 'Packed Dimensions — Awaiting Shopify API capability. Enable the opt-in 2027-01 adapter to check store support; no measurement is published while disabled.'
        return policy
    policy.update(unconfigured=True,reason='Packed shipping dimensions unconfigured; finished dimensions never substitute. Existing remote measurements are retained pending review.')
    return policy


def projection(product):
    authored = product_document(product)
    names = variation_drivers(authored)
    if not 1 <= len(names) <= 3:
        raise ShopifyError("Shopify supports one to three SKU options. Resolve unsupported driver counts without changing catalogue identities.")
    children = sorted([v for v in product.variations if v.catalogue_status == "active"], key=lambda v: (v.menu_order or 0, v.id))
    if not 1 <= len(children) <= MAX_VARIANTS:
        raise ShopifyError("This reviewed operation supports 1–32 active variants; larger projections need a separately bounded milestone.")
    from app.utils.scanner import build_variations
    from app.utils.json_utils import apply_variation_modifiers
    from app.utils.csv_writer import build_variation_row
    count = 1
    for name in names:
        values = authored.get("attributes", {}).get(name)
        if not isinstance(values, list) or not values or any(not isinstance(v,str) or not v.strip() or len(v)>255 for v in values):
            raise ShopifyError("Variant options require explicit, bounded names and values.")
        count *= len(values)
    if count > MAX_VARIANTS:
        raise ShopifyError("Authored option combinations exceed the 32-variant review bound.")
    expected = {tuple((n, a[n]) for n in names) for a in build_variations(authored)}
    actual, identities, skus, result = set(), set(), set(), []
    for v in children:
        attrs = {a.name: a.value for a in v.attributes}
        selection = tuple((name, attrs.get(name)) for name in names)
        if (not v.source_identity or not v.sku or v.source_identity in identities or v.sku in skus
                or selection in actual or selection not in expected or v.visible is False):
            raise ShopifyError("Variation identities/SKUs/options are incomplete, ambiguous, hidden or stale. Validate and perform the established targeted rescan; no identities are allocated here.")
        # Reuse scanner calculations only to detect stale ingestion. Publish its
        # persisted effective numbers, never a newly invented alternate price.
        calculated = build_variation_row(dict(authored, sku=product.sku),
            {"attributes": dict(selection), "modifiers": apply_variation_modifiers(authored, dict(selection), log=lambda *a,**k: None)}, [], 1, override_sku=v.sku)
        pairs = [(v.regular_price, calculated.get("Regular price")), (v.sale_price, calculated.get("Sale price")),
            (v.weight, calculated.get("Weight (g)")), (v.length, calculated.get("Length (mm)")),
            (v.width, calculated.get("Width (mm)")), (v.height, calculated.get("Height (mm)"))]
        def decimal(value):
            return None if value in (None, "") else Decimal(str(value))
        if any(decimal(left) != decimal(right) for left,right in pairs):
            raise ShopifyError("Resolved variant prices/measurements no longer match current inherited metadata/modifiers. Complete the targeted rescan before Review.")
        dimensions = {"length": v.length, "width": v.width, "height": v.height}
        from app.catalogue_fields import shipping_details
        shipping = shipping_details(authored, product)
        from app.packaging_contract import effective as packing_effective
        packaging = packing_effective(authored,dict(selection))
        from app.packaging_contract import finished_mm
        shipping.update(weight_g=packaging.get('weight_g',v.weight), dimensions_mm=finished_mm(authored,dict(selection)) or dimensions)
        from app.sale_pricing import variant_sale
        # Scanner identity/regular/measurement checks above stay authoritative.
        # Sale modifiers previously omitted by legacy CSV are read explicitly
        # through the existing modifier selector, not a new combination engine.
        sale = variant_sale(authored, dict(selection), v.sale_price)
        variant_authored = dict(authored, sale_price=sale, packaging=packaging)
        managed, warnings = fields.contract(SimpleNamespace(data={"authored": variant_authored,
            "sale_price": v.sale_price, "shipping": shipping}), {"title": product.title, "description": "",
            "price": fields.number(v.regular_price, "effective variant price", money=True), "sku": v.sku})
        payload = managed["variant"]
        if managed["product"].get("metafields"):
            payload["metafields"] = managed["product"]["metafields"]
        hs = authored.get("harmonized_system_code")
        if hs is not None:
            if not isinstance(hs,str) or not hs.isdigit() or not 6 <= len(hs) <= 13:
                raise ShopifyError("Explicit HS code must contain 6–13 digits.")
            payload["inventoryItem"]["harmonizedSystemCode"] = hs
        images = variation_image_diagnostics(v, include_parent_fallback=False)
        source = images[0]["source_reference"] if images else None
        if images and any(not i["source_exists"] for i in images):
            raise ShopifyError("Variant image source is unavailable; no association is guessed.")
        result.append({"identity": v.source_identity, "local_id": v.id, "sku": v.sku,
            "options": [{"name": n, "value": value} for n,value in selection],
            "managed": payload, "media_source": source, "native": packed_policy(dimensions,packaging),
            "dimensions_mm": {k: str(value) if value is not None else None for k,value in dimensions.items()}})
        actual.add(selection); identities.add(v.source_identity); skus.add(v.sku)
    if actual != expected:
        raise ShopifyError("Current authored combinations and active catalogue variants differ. Complete the targeted rescan; no duplicate variants are invented.")
    return result


def local_plan(product):
    if product.product_type != "variable" or product.collection_type not in {"Variable Collection", "Single Variable"}:
        raise ShopifyError("Only established Variable Collection/Single Variable parents are supported.")
    variants = projection(product)
    plan = drafts.common_plan(product, variable=True)
    plan.update(kind="variable", variants=variants, options=[])
    # A varying size belongs to its real variant, never a generic parent size.
    sizes = [v['managed'].get('metafields', []) for v in variants]
    if any(size != sizes[0] for size in sizes[1:]):
        plan['managed']['product'].pop('metafields', None)
        plan['warnings'].append('Finished sizes differ by variant. Variant metafields are authoritative; any historical parent size is retained, not silently cleared. Bind theme display to the selected variant.')
    prices=[Decimal(v["managed"]["price"]) for v in variants]
    plan["price_summary"]={"minimum":format(min(prices),".2f"),"maximum":format(max(prices),".2f")}
    for option in variants[0]["options"]:
        name = option["name"]
        values = list(dict.fromkeys(next(o["value"] for o in v["options"] if o["name"] == name) for v in variants))
        plan["options"].append({"name": name, "position": len(plan["options"])+1, "values": [{"name": value} for value in values]})
    plan["warnings"].append("Parent SKU is a local grouping identity; existing child SKUs belong to Shopify variants. Tracking/selling policy is explicitly reviewed; quantities and locations remain unmanaged. Remote-only variants are retained.")
    from app.packaging_contract import effective as packing_effective
    authored=product_document(product)
    for variant in variants:
        selection={o['name']:o['value'] for o in variant['options']}
        packing=packing_effective(authored,selection)
        if 'selected_package_id' in packing:
            assignment='selected locally' if packing['selected_package_id'] else 'explicitly unassigned locally'
            plan['warnings'].append(f"{variant['sku']}: optional solo package {assignment}. Linked IDs can be submitted for reviewed acceptance, not independent readback. Clearing/unlinked choices need manual Admin action; other fields remain publishable.")
    if any(isinstance(v,dict) and "sale_price" in v for v in product_document(product).get("variation_modifiers",{}).values()):
        plan["warnings"].append("Per-variant sale amounts use existing exact/most-specific modifiers. The legacy scanner sale column is unchanged; Review shows the effective Shopify prices.")
    plan["unsupported"] = ["Storefront navigation publishing is deferred", "Customer-facing customisation", "Inventory quantities/locations",
        "Destructive option restructuring / variant deletion", "Existing gallery replacement/addition/removal", "Shopify package presets / labels"]
    plan["digest"] = drafts.digest({"base": plan["digest"], "variants": variants, "options": plan["options"]})
    return plan


def remote_identity(row, data, remote, *, native_only=False):
    from app.shopify_connection import ShopifyIdentityConflict, ShopifyVariantMissing
    if (not isinstance(remote,dict) or remote.get("id") != row.shopify_product_id or remote.get("status") not in {"DRAFT","ACTIVE","ARCHIVED"}
            or (remote.get("metafield") or {}).get("value") != data["plan"]["nonce"]):
        raise ShopifyError("Retained variable product/store/correlation/Draft identity is untrusted; no new Create or update allowed.")
    for connection, bound in (("variants", MAX_VARIANTS),) if native_only else (("variants", MAX_VARIANTS), ("media",drafts.MAX_IMAGES)):
        value = remote.get(connection)
        if not isinstance(value,dict) or value.get("pageInfo",{}).get("hasNextPage") is not False or not isinstance(value.get("nodes"),list) or len(value["nodes"])>bound:
            raise ShopifyError("Variable remote evidence is incomplete or exceeds the review bound; no equality or destructive action assumed.")
    ids, inventory_ids, skus, options = set(), set(), set(), set()
    nodes = remote["variants"]["nodes"]
    for v in nodes:
        item = v.get("inventoryItem")
        if not isinstance(item,dict) or item.get("sku") != v.get("sku"):
            raise ShopifyError("Remote variant/inventory SKU relationship is incomplete.")
        vid, iid = _gid(v.get("id"),"ProductVariant"), _gid(item.get("id"),"InventoryItem")
        selected = v.get("selectedOptions")
        if not isinstance(selected,list) or any(not isinstance(o,dict) or not isinstance(o.get("name"),str) or not isinstance(o.get("value"),str) for o in selected):
            raise ShopifyError("Remote selected-option evidence is incomplete.")
        combination = tuple((o["name"],o["value"]) for o in selected)
        if vid in ids or iid in inventory_ids or combination in options or v.get("sku") and v["sku"] in skus:
            raise ShopifyError("Ambiguous remote variant/inventory/SKU identities; no update allowed.")
        ids.add(vid); inventory_ids.add(iid); options.add(combination); skus.add(v.get("sku"))
    by_id = {v["id"]:v for v in nodes}
    for identity, mapping in data.get("variant_maps",{}).items():
        current = by_id.get(mapping["id"])
        if not current:
            raise ShopifyVariantMissing("Retained variant missing from the complete Shopify read: "+mapping["sku"]+". No automatic recreation; inspect Identity & History.")
        if (current["inventoryItem"]["id"] != mapping["inventory_id"] or current["sku"] != mapping["sku"]
                or current["selectedOptions"] != mapping["options"]):
            raise ShopifyIdentityConflict("A retained variant identity/SKU/options changed. Reconcile, never recreate it automatically.")
    if not native_only:
        options=remote.get("options")
        if not isinstance(options,list) or not 1<=len(options)<=3:
            raise ShopifyError("Remote option identity evidence is incomplete.")
        for option in options:
            _gid(option.get("id"),"ProductOption")
            values=option.get("optionValues")
            if not isinstance(values,list) or len(values)>MAX_VARIANTS:
                raise ShopifyError("Remote option values exceed the bounded identity contract.")
            for value in values:
                _gid(value.get("id"),"ProductOptionValue")
        by_option={o["id"]:o for o in options}
        for kept in data.get("option_maps",[]):
            current=by_option.get(kept["id"])
            if not current or current["name"]!=kept["name"] or current["position"]!=kept["position"]:
                raise ShopifyError("Retained option identity changed; destructive restructuring is blocked.")
            known={v["id"]:v["name"] for v in current["optionValues"]}
            if any(known.get(v["id"])!=v["name"] for v in kept["values"]):
                raise ShopifyError("Retained option-value identity changed; no silent rename/removal.")
    return nodes


def parent_rows(plan, remote):
    first = remote["variants"]["nodes"][0]
    fake = {"product": plan["managed"]["product"], "variant": {"inventoryItem":{"sku": first["sku"]},
        "price": first["price"], "compareAtPrice": first["compareAtPrice"]}}
    rows = fields.field_rows(fake, dict(remote,simpleVariants={"nodes":[first],"pageInfo":{"hasNextPage":False}}))
    return [r for r in rows if r["field"] not in {"price","compareAtPrice","variant.sku","sku"}]


def variant_rows(wanted, remote):
    synthetic = {"title":"", "descriptionHtml":"", "simpleVariants":{"nodes":[remote],"pageInfo":{"hasNextPage":False}}}
    for k in ("length","width","height"):
        synthetic[k] = remote.get(k)
    meta = wanted["managed"].get("metafields", [])
    managed = {"product":{"metafields":meta}, "variant": wanted["managed"]}
    rows = fields.field_rows(managed, synthetic)
    if "harmonizedSystemCode" in wanted["managed"]["inventoryItem"] and "harmonizedSystemCode" not in remote["inventoryItem"]:
        raise ShopifyError("Remote HS code evidence is incomplete.")
    association = remote.get("media")
    if not isinstance(association,dict) or association.get("pageInfo",{}).get("hasNextPage") is not False or len(association.get("nodes",[]))>1:
        raise ShopifyError("Variant media association is incomplete/ambiguous; no replacement guessed.")
    if wanted.get("media_id"):
        before = [m["id"] for m in association["nodes"]]
        rows.append({"field":"mediaId", "before":before, "after":wanted["media_id"], "changed":before != [wanted["media_id"]]})
    return rows


def compare(plan, row, data, remote, *, initial_variant=None):
    nodes = remote_identity(row,data,remote)
    names = [o["name"] for o in plan["options"]]
    actual_options = remote.get("options")
    if not isinstance(actual_options,list) or [o.get("name") for o in sorted(actual_options,key=lambda o:o["position"])] != names:
        plan["blockers"].append("Option names/order differ; destructive option restructuring is unsupported.")
    rows = parent_rows(plan,remote)
    sources = {m["source"]:m.get("id") for m in data.get("media",[])}
    # Existing media review enforces exact owned gallery and hashes, without writes.
    actions, media_rows = drafts.media_review(plan,data,remote)
    plan["media_actions"] = actions
    rows += media_rows
    by_id = {v["id"]:v for v in nodes}
    used = set()
    for v in plan["variants"]:
        v["media_id"] = sources.get(v["media_source"])
        if v["media_source"] and not v["media_id"]:
            plan["blockers"].append("Variant image lacks a retained media mapping: " + v["sku"])
        mapping = data.get("variant_maps",{}).get(v["identity"])
        if not mapping and initial_variant and v['identity']==plan['variants'][0]['identity']:
            mapping={'id':initial_variant['id'],'inventory_id':initial_variant['inventoryItem']['id'],
                'sku':v['sku'],'options':v['options']}
        if mapping:
            if mapping["sku"] != v["sku"] or mapping["options"] != v["options"]:
                plan["blockers"].append("Stable variant SKU/options cannot be silently rewritten: " + v["sku"])
            v.update(remote_id=mapping["id"], inventory_id=mapping["inventory_id"], action="Update")
            used.add(mapping["id"])
            current_media=[m["id"] for m in by_id[mapping["id"]].get("media",{}).get("nodes",[])]
            if v.get("media_id") and current_media and current_media != [v["media_id"]]:
                plan["blockers"].append("Existing variant media replacement requires a separately reviewed association policy: "+v["sku"])
            previous=next((old for old in data["plan"]["variants"] if old["identity"]==v["identity"]),None)
            if previous and previous.get("media_source") and not v["media_source"]:
                plan["blockers"].append("Managed variant media removal is unsupported; retained association preserved: "+v["sku"])
            for field in variant_rows(v,by_id[mapping["id"]]):
                rows.append({**field,"field":"variants."+v["sku"]+"."+field["field"],"variant_identity":v["identity"],"leaf":field["field"]})
        else:
            candidates = [n for n in nodes if n["sku"] == v["sku"] or n["selectedOptions"] == v["options"]]
            if candidates:
                plan["blockers"].append("Unmapped remote variant candidate requires identity review: " + v["sku"])
            v["action"] = "Create"
            rows.append({"field":"variants."+v["sku"]+".create","before":None,"after":v["managed"]["price"],"changed":True})
    extras = [v["sku"] or v["id"] for v in nodes if v["id"] not in used]
    if len(nodes)+sum(v["action"]=="Create" for v in plan["variants"])>MAX_VARIANTS:
        plan["blockers"].append("Managed additions plus retained remote-only variants exceed the 32-variant readback bound; no variants are removed to make space.")
    if extras:
        plan["warnings"].append("Remote-only/unmapped variants are preserved: " + ", ".join(extras))
    plan["fields"] = rows
    return nodes


def native_review(plan, row, data, config, shop, adapter):
    plans = {v["identity"]: dict(v["native"], sku=v["sku"], before=None, inventory_id=v.get("inventory_id"), classification="blocked") for v in plan["variants"]}
    plan["native"] = {"enabled":native.flag("SHOPIFY_NATIVE_DIMENSIONS_ENABLED"), "variants":plans,
        "version":"2027-01", "checked_at":None, "classification":"disabled", "reason":""}
    if not plan["native"]["enabled"]:
        plan['native']['reason'] = 'Packed Dimensions — Awaiting Shopify API capability. Opt-in 2027-01 is disabled; no native measurements are published.'
        plan['native']['pending_capability'] = any(v.get('measurement_source') for v in plans.values())
        return
    try:
        allowed = native.capability(adapter,config,shop)
        remote = adapter.read_variable(config,row.shopify_product_id,plan["managed"]["namespace"],native=True) if row else None
        nodes = {v["id"]:v for v in remote_identity(row,data,remote,native_only=True)} if row else {}
        for v in plan["variants"]:
            value = plans[v["identity"]]
            proof = data.get("variant_native",{}).get(v["identity"],{}) if data else {}
            if proof.get("state") in {"pending","uncertain"}:
                plan["blockers"].append("Native outcome requires read-only reconciliation: " + v["sku"])
            if v.get("remote_id"):
                value["before"] = nodes[v["remote_id"]]["inventoryItem"]["measurement"]["packedDimensions"]
                native.millimetres(value["before"])
                prior=next((p['native'] for p in (data.get('plan') or {}).get('variants',[]) if p['identity']==v['identity']),{})
                value['historical_finding']=native.historical_finding(value['before'],value['desired'],v['dimensions_mm'],dict(proof,plan=prior))
                if value['historical_finding']!='Correct and verified':
                    plan['warnings'].append(v['sku']+': '+value['historical_finding']+'. Remote values retained until an explicit measured packed-size correction is reviewed.')
            if value["desired"]:
                value["classification"] = "no_change" if value["before"] is not None and native.equal(value["before"],value["desired"]) else "update"
                if not allowed and value["classification"] != "no_change":
                    value["reason"] = "Missing write_inventory; retain metadata/media and complete native stage later."
            if value["reason"]:
                plan["warnings"].append(v["sku"] + ": " + value["reason"])
            elif value["before"] is not None:
                plan["fields"].append({"field":"native."+v["sku"],"before":value["before"],"after":value["desired"],"changed":value["classification"] != "no_change"})
        plan["native"].update(classification="checked", checked_at=drafts.now().isoformat(), can_write=allowed)
    except ShopifyError as exc:
        plan["native"].update(reason=str(exc),classification="blocked")
        for value in plans.values():
            value["reason"] = str(exc)
        plan["warnings"].append(str(exc))
    plan['native']['pending_capability'] = any(v.get('measurement_source') and v.get('reason') for v in plans.values())


def _creation_resume(row, data, plan, remote=None):
    """Only a conclusively acknowledged Create BEFORE any variant write.

    This is a fresh signed update of that exact Draft, never a Create retry or
    adoption of arbitrary media/variants. Ambiguous later mutations stay recovery.
    """
    if (not row or not row.shopify_product_id or data.get('kind')!='variable' or not data.get('create_attempted')
            or 'product_create' not in data.get('completed_steps',[]) or data.get('variant_creates_attempted')
            or data.get('variant_maps') or data.get('update_attempted')
            or any(s not in {'staged_uploads','original_image_uploads','product_create'} for s in data.get('completed_steps',[]))):
        raise ShopifyError('Partial creation is not conclusively before variant writes; use read-only identity recovery, never replay mutations.')
    if row.reviewed_digest!=data['plan']['digest']:
        raise ShopifyError('Retained reviewed digest conflicts with its creation receipt; inspect identity evidence before any continuation.')
    if data['plan']['digest']!=plan['digest']:
        raise ShopifyError('Local source changed since the retained Create; completion requires source/identity review, not a stale continuation.')
    uploads=data.get('upload_inputs') or [];images=plan['images'];created=data.get('created_media') or []
    if (len(uploads)!=len(images) or len(data.get('media',[]))!=len(images) or len(created)!=len(images)
            or data.get('upload_attempt',{}).get('state')!='completed'
            or any(u.get('source')!=i['source'] or u.get('sha256')!=i['sha256'] for u,i in zip(uploads,images))):
        raise ShopifyError('Original upload/Create media acknowledgements are incomplete; no image upload or product Create may be retried.')
    ids=[_gid(m.get('id'),'MediaImage') for m in created]
    if len(set(ids))!=len(ids):raise ShopifyError('Duplicate creation media receipts require recovery review.')
    if remote is None:return
    nodes=remote_identity(row,data,remote)
    if remote['status']!='DRAFT' or len(nodes)!=1 or nodes[0]['sku'] or nodes[0]['selectedOptions']!=plan['variants'][0]['options']:
        raise ShopifyError('Partial Draft initial variant changed or is no longer uniquely blank; no variant is overwritten, deleted or recreated.')
    actual=remote['media']['nodes']
    if ([m['id'] for m in actual]!=ids or any(m.get('alt')!=i['alt'] or m.get('mediaContentType')!='IMAGE'
            or m.get('status') not in {'READY','PROCESSING','UPLOADED'} for m,i in zip(actual,images))):
        raise ShopifyError('Remote gallery differs from acknowledged creation IDs/order/alt text; no media is adopted or re-uploaded.')
    for receipt,image,mid in zip(data['media'],images,ids):
        if receipt.get('source')!=image['source'] or receipt.get('sha256')!=image['sha256'] or receipt.get('id') not in {None,mid}:
            raise ShopifyError('Retained original-to-media acknowledgement conflicts; inspect recovery evidence.')
        receipt['id']=mid
    return nodes[0]


def preview(product, adapter=None, *, context=None, seed=None, scope="full", started=None, resume_creation=False):
    adapter = adapter or ShopifyAdapter()
    plan = local_plan(product)
    plan['comparison_started_at'] = started or drafts.now().isoformat()
    config = drafts.configuration()
    shop = context["shop"] if context and context["config"] == config.fingerprint() else adapter.test_connection(config)
    row = drafts.matching_identity(shop["shop_id"],product)
    if resume_creation:_creation_resume(row,drafts.evidence(row) if row else {},plan)
    from app.shopify_comparison import identity_revision
    plan['read_identity_digest'] = identity_revision(row)
    if row and any(p.get("state") in {"pending","uncertain"} for p in drafts.evidence(row).get("variant_native",{}).values()):
        raise ShopifyError("Recovery required for retained native measurements; reconcile before another write.")
    if scope == "inventory" and (not row or row.state not in {"verified","scoped_verified"}):
        raise ShopifyError("Inventory-only Review requires a trusted retained variable parent.")
    plan["scope"] = scope
    if row and row.state == "processing":
        drafts.finalize_processing(row,adapter)
        if row.state == "processing":
            raise ShopifyError("Variable media is still processing. Refresh shortly; no retry/upload is needed.")
    if row and row.state not in {"verified","failed_before_create","remote_deleted","attribute_review_required","scoped_verified"} and not drafts.upload_pending(row) and not resume_creation:
        raise ShopifyError("Recovery required for this variable identity. Reconcile the retained stage; no duplicate product/variant create.")
    if row and row.state in {"verified","attribute_review_required","scoped_verified"} and drafts.evidence(row).get("kind") != "variable":
        raise ShopifyError("Existing Simple-to-variable conversion requires a separately reviewed identity migration; another Create is blocked.")
    if row and (row.source_identity != product.source_relpath or row.sku != product.sku):
        raise ShopifyError("Retained parent source/SKU identity changed; restore it before any variable write.")
    plan.update(store_id=shop["shop_id"], store_key=drafts.digest({"id":shop["shop_id"]}), domain=config.domain,
        config_digest=config.fingerprint(), nonce=seed or (drafts.evidence(row)["plan"]["nonce"] if row and (resume_creation or row.state in {"verified","attribute_review_required","scoped_verified"} or drafts.upload_pending(row)) else uuid.uuid4().hex))
    plan["handle"] = plan["managed"]["product"].get("handle") or "tlc-draft-"+plan["nonce"]
    remote_id = row.shopify_product_id if row and (resume_creation or row.state in {"verified","attribute_review_required","scoped_verified"}) else None
    from app.shopify_taxonomy import membership_plan
    plan["collections"], blockers, standard = ([],[],None) if scope == "inventory" else membership_plan(product,shop["shop_id"],remote_id,config,adapter)
    plan["blockers"] += blockers
    plan["standard_category"] = adapter.standard_category(config,standard) if standard else None
    if standard:
        plan["managed"]["product"]["category"] = standard
    countries = {v["managed"]["inventoryItem"].get("countryCodeOfOrigin") for v in plan["variants"]} - {None}
    if countries and not countries.issubset(adapter.country_codes(config)):
        raise ShopifyError("Variant country of origin is unsupported; correct authored/default origin.")
    data = drafts.evidence(row) if remote_id else {}
    if remote_id:
        remote = adapter.read_variable(config,remote_id,plan["managed"]["namespace"])
        if remote is None:
            from app.shopify_connection import ShopifyProductAbsent
            raise ShopifyProductAbsent("Exact Shopify variable parent read returned absent; reconcile existence before any new Create.")
        identity_digest=drafts.digest(data)
        initial_variant=_creation_resume(row,data,plan,remote) if resume_creation else None
        compare(plan,row,data,remote,initial_variant=initial_variant)
        if resume_creation:
            plan['creation_resume']={'operation_id':row.operation_id,'media_ids':[m['id'] for m in data['created_media']]}
        plan.update(remote_id=remote_id,remote_digest=drafts.digest(remote),identity_digest=drafts.digest(data),
            identity_id=row.id,actual_status=remote["status"],remote_updated_at=remote.get("updatedAt"))
        if resume_creation:plan['identity_digest']=identity_digest
        if "tags" in plan["managed"]["product"]:
            removed = sorted(set(remote["tags"]) - set(plan["managed"]["product"]["tags"]))
            if removed: plan["warnings"].append("Explicit tag replacement removes: " + ", ".join(removed))
    else:
        matches = adapter.find_skus(config,[plan["sku"], *[v["sku"] for v in plan["variants"]]])
        if not isinstance(matches,dict) or not isinstance(matches.get("nodes"),list) or matches.get("pageInfo",{}).get("hasNextPage") is not False:
            raise ShopifyError('Parent/variant SKU discovery is incomplete. Resolve discovery before Create.')
        if matches['nodes']:
            from app.shopify_connection import ShopifyReadinessFinding
            raise ShopifyReadinessFinding('Existing parent/variant SKU candidates need reconciliation review; never create duplicates or auto-link ambiguous variants. First-time linking without a retained identity requires a separately supported workflow.', code='reconciliation')
        found = adapter.find_draft(config,plan["handle"])
        if found["nodes"] or found["pageInfo"].get("hasNextPage"):
            raise ShopifyError("Handle already exists or discovery is incomplete; resolve candidates before Create.")
        plan["fields"] = [{"field":key,"before":None,"after":value,"changed":value is not None} for key,value in plan["managed"]["product"].items()]
        plan["fields"] += [{"field":"variants."+v["sku"]+".create","before":None,"after":v["managed"]["price"],"changed":True} for v in plan["variants"]]
        for v in plan["variants"]: v["action"] = "Create"
        plan["actual_status"] = "Not yet created"
    # The attribute digest is folded into the final signed taxonomy contract below.
    plan["taxonomy_review_digest"] = drafts.digest({"collections":plan["collections"],"standard":standard})
    from app.shopify_attributes import attach
    if scope == "inventory":
        from app.taxonomy_assignments import product_document, resolve
        plan["attributes"] = []
        plan["attribute_review_digest"] = drafts.digest([])
        plan["attribute_diagnostics"] = [{"name":a["name"],"action":"Explicitly deferred; not compared or synchronised"} for a in resolve(product_document(product))["attributes"] if not a["variation"]]
        plan["native"] = {"enabled":False,"classification":"deferred","reason":"Not part of this inventory-only review","variants":{}}
    else:
        attach(product,plan,config,shop,adapter,remote_id)
        from app.shopify_customisations import attach as attach_customisations
        attach_customisations(product,plan,config,shop,adapter,remote_id)
        from app.shopify_dimension_metadata import attach as attach_dimensions
        attach_dimensions(plan, config, shop, adapter)
        from app.shopify_optional_packages import attach as attach_packages
        attach_packages(product, plan, row if remote_id else None, config, shop)
        native_review(plan,row if remote_id else None,data,config,shop,adapter)
    plan["classification"] = "create" if not remote_id else "update" if any(f["changed"] for f in plan["fields"]) or any(c["action"] in {"Join","Create & Join"} for c in plan["collections"]) else "no_change"
    if scope == "inventory":
        from app.shopify_inventory_review import restrict
        restrict(plan)
    else:
        from app.shopify_publication import annotate
        annotate(product,plan,row if remote_id else None,config,shop,adapter)
    if row and row.state=="attribute_review_required" and plan["classification"]=="no_change":
        plan["blockers"].append("Attributes now match; complete read-only identity reconciliation for final verification.")
    if "write_products" not in shop["scopes"] and plan["classification"] != "no_change":
        plan["blockers"].append("Missing write_products; read-only Preview remains available.")
    if plan.get("media_actions",{}).get("alt") and "write_files" not in shop["scopes"]:
        plan["blockers"].append("Missing write_files for reviewed gallery alt updates.")
    if remote_id:
        additions = [v["sku"] for v in plan["variants"] if v["action"] == "Create"]
        if additions:
            matches = adapter.find_skus(config,additions)
            if not isinstance(matches,dict) or not isinstance(matches.get("nodes"),list) or matches.get("pageInfo",{}).get("hasNextPage") is not False or matches["nodes"]:
                plan["blockers"].append("New variant SKU candidate already exists or discovery is incomplete; resolve before creation.")
    from app.shopify_publication import review_binding
    from app.shopify_package_assignments import binding as package_binding
    plan["taxonomy_review_digest"] = drafts.digest({"collections":plan["collections"],"standard":standard,"attributes":plan["attribute_review_digest"],
        "customisations":plan.get('customisations'),
        "finished_dimensions":plan.get('dimension_metadata'),
        "package_assignments":package_binding(plan),
        "native":{k:v for k,v in plan["native"].items() if k!="checked_at"},
        "publication":review_binding(plan.get('publication') or {})})
    # Leave room for durable media/maps/native receipts BEFORE any remote write.
    reserves={"version":1,"plan":plan,"media":[{"source":i["source"],"filename":i["filename"],"sha256":i["sha256"],"id":"x"*100,"url":"x"*1024,"status":"x"*24} for i in plan["images"]],
        "created_media":[{"id":"x"*100} for i in plan["images"]],
        "variant_maps":{v["identity"]:{"id":"x"*100,"inventory_id":"x"*100,"sku":v["sku"],"options":v["options"]} for v in plan["variants"]},
        "option_maps":[dict(o,id="x"*100,values=[dict(value,id="x"*100) for value in o["values"]]) for o in plan["options"]],
        "reserve":"x"*(3000+sum(len(a["resources"]) for a in plan.get("attributes",[]))*300)}
    if plan["native"]["enabled"]:
        reserves["variant_native"]={v["identity"]:{"version":1,"state":"uncertain","desired":v["native"]["desired"],"observed":v["native"]["desired"],"inventory_id":"x"*100,"operation_id":"x"*100,"checked_at":"x"*100,"message":"x"*512} for v in plan["variants"]}
    from app.shopify_customisations import reserve as customisation_reserve
    reserves['storefront_configuration'] = customisation_reserve(plan)
    drafts.encode(reserves)
    from app.shopify_comparison import record
    record(product,plan,row if remote_id else None)
    reviewed = {"mode":"variable_resume" if resume_creation else "variable", **{key:plan.get(key) for key in
        ("product_id","digest","store_id","config_digest","nonce","remote_id","remote_digest","identity_digest","taxonomy_review_digest","classification","identity_id","scope")}}
    if resume_creation:reviewed['creation_resume']=plan['creation_resume']
    from app.shopify_publication import has_reviewed_changes
    token = drafts.serializer().dumps(reviewed) if plan["classification"] != "no_change" and not plan["blockers"] and (not remote_id or scope=='inventory' or has_reviewed_changes(plan)) else None
    return plan,token


def _save_maps(row,data,remote, *, recover=False):
    nodes = remote_identity(row,data,remote)
    data["option_maps"]=[{"id":o["id"],"name":o["name"],"position":o["position"],"values":o["optionValues"]} for o in remote["options"]]
    for wanted in data["plan"]["variants"]:
        if wanted["identity"] in data.get("variant_maps",{}):
            continue
        if not recover and wanted["identity"] not in data.get("variant_creates_attempted",[]):
            continue
        candidates = [v for v in nodes if v["sku"]==wanted["sku"] and v["selectedOptions"]==wanted["options"]]
        if len(candidates)==1:
            v=candidates[0]
            data.setdefault("variant_maps",{})[wanted["identity"]] = {"id":v["id"],"inventory_id":v["inventoryItem"]["id"],"sku":v["sku"],"options":v["selectedOptions"]}
    drafts.save(row,data)
    return nodes


def _payload(wanted,media):
    result = copy.deepcopy(wanted["managed"])
    if wanted["media_source"]:
        mid = media.get(wanted["media_source"])
        if not mid:
            raise ShopifyError("Reviewed variant media mapping is not retained; no guessed association.")
        result["mediaId"] = mid
    return result


def _native_apply(row,data,config,adapter,operation_id):
    contract = data["plan"]["native"]
    if not contract["enabled"]:
        return "blocked" if any(v.get('measurement_source') for v in contract.get('variants', {}).values()) else "disabled"
    if all(v.get('unconfigured') for v in contract['variants'].values()):
        return 'unconfigured'  # preserve existing per-variant proof, no native write
    proofs = data.setdefault("variant_native",{})
    try:
        shop=adapter.test_connection(config)
        can_write=native.capability(adapter,config,shop)
        if shop["shop_id"]!=row.store_id or contract.get("reason"):
            raise ShopifyError(contract.get("reason") or "Native store identity changed; no native write attempted.")
        product=db.session.get(Product,row.product_id)
        if local_plan(product)["digest"]!=data["plan"]["digest"] or config.fingerprint()!=data["plan"]["config_digest"]:
            raise ShopifyError("Local source/configuration changed; rebuild a native review before any write.")
        remote=adapter.read_variable(config,row.shopify_product_id,data["plan"]["managed"]["namespace"],native=True)
        nodes={v["id"]:v for v in remote_identity(row,data,remote,native_only=True)}
    except ShopifyError as exc:
        for wanted in data["plan"]["variants"]:
            proofs[wanted["identity"]]={"state":"blocked","message":str(exc),"desired":wanted["native"]["desired"]}
        drafts.save(row,data)
        return "blocked"
    started = monotonic()
    for wanted in data["plan"]["variants"]:
        key = wanted["identity"]
        target = contract["variants"][key]
        mapping = data["variant_maps"][key]
        if target.get('unconfigured'): continue
        if target["reason"] or not target["desired"] or contract.get("reason") or monotonic()-started>120:
            proofs[key] = {"state":"blocked","message":target["reason"] or contract.get("reason") or "Native stage time budget reached; build a fresh reviewed completion.","desired":target["desired"]}
            drafts.save(row,data)
            continue
        before = nodes[mapping["id"]]["inventoryItem"]["measurement"]["packedDimensions"]
        if not can_write and not native.equal(before,target["desired"]):
            proofs[key]={"state":"blocked","message":"Missing write_inventory; no native write attempted.","desired":target["desired"]}
            drafts.save(row,data); continue
        if target.get("before") is not None and not native.equal(before,target["before"]):
            proofs[key] = {"state":"blocked","message":"Inventory measurements changed since Review; no native write attempted.","desired":target["desired"]}
            drafts.save(row,data); continue
        proof = {"version":1,"state":"verified" if native.equal(before,target["desired"]) else "pending",
            "desired":target["desired"],"inventory_id":mapping["inventory_id"],"operation_id":operation_id}
        if proofs.get(key,{}).get('state')=='verified':
            proof['prior_verified']={k:proofs[key][k] for k in ('desired','observed','checked_at','inventory_id') if k in proofs[key]}
        proof['measurement_source']='authored_packed_size'
        proofs[key] = proof
        drafts.save(row,data)  # durable per-inventory reservation BEFORE mutation
        try:
            if proof["state"] == "pending":
                payload = adapter.write_packed(config,mapping["inventory_id"],target["desired"])
                value = payload.get("inventoryItemUpdate") or {}
                if value.get("userErrors") and value.get("inventoryItem") is None:
                    proof.update(state="blocked",message="Shopify rejected packed dimensions; correct values/access and rebuild Review.")
                    drafts.save(row,data); continue
                if value.get("userErrors") or (value.get("inventoryItem") or {}).get("id") != mapping["inventory_id"]:
                    raise ShopifyError("Native mutation receipt is incomplete; reconcile without retry.")
            observed = adapter.read_packed_inventory(config,mapping["inventory_id"])
            if (not isinstance(observed,dict) or observed.get("id") != mapping["inventory_id"] or observed.get("sku") != wanted["sku"]
                    or not native.equal(observed["measurement"]["packedDimensions"],target["desired"])):
                raise ShopifyError("Per-variant packed dimensions are not independently verified; reconcile without retry.")
            proof.update(state="verified",observed=observed["measurement"]["packedDimensions"],checked_at=drafts.now().isoformat())
        except ShopifyNativeWriteRejected as exc:
            proof.update(state="blocked",message=str(exc))
        except Exception:
            proof.update(state="uncertain",message="Native outcome is uncertain; inspect the exact retained InventoryItem and reconcile using reads only.")
            drafts.save(row,data)
            return "uncertain"  # do not continue writes after uncertainty
        drafts.save(row,data)
    return "verified" if all(v['native'].get('desired') is not None and proofs.get(v["identity"],{}).get("state")=="verified" for v in data["plan"]["variants"]) else "blocked"


def verify(row,data,config,adapter, *, recover=False, approval=None, allow_attribute_review=False):
    from app.shopify_package_assignments import guard as package_guard
    package_guard(data)
    plan = data["plan"]
    remote = adapter.read_variable(config,row.shopify_product_id,plan["managed"]["namespace"])
    if getattr(adapter,'snapshot_restarts',None):data['gallery_snapshot_restarts']=(data.get('gallery_snapshot_restarts',[])+copy.deepcopy(adapter.snapshot_restarts))[-6:]
    from app.shopify_publication import expected_status
    if remote.get('status') != expected_status(data):
        raise ShopifyError('Product lifecycle changed during metadata verification; reconcile before another operation.')
    data["observed_status"] = remote.get("status")
    if recover:
        remote_identity(row,data,remote)
        data["recovery_remote_digest"]=drafts.digest(remote)
        data["observed_media"]=[{"id":m["id"],"status":m.get("status"),"alt":m.get("alt")} for m in remote["media"]["nodes"]]
        drafts.save(row,data)
        if approval:
            try:
                reviewed=drafts.serializer().loads(approval,max_age=900)
            except BadSignature:
                raise ShopifyError("Variable recovery review expired; inspect fresh evidence before approval.") from None
            local=local_plan(db.session.get(Product,row.product_id))
            expected={"identity":row.id,"remote":drafts.digest(remote),"id":row.shopify_product_id,"local":local["digest"]}
            if reviewed!=expected or local["images"]!=plan["images"] or len(remote["media"]["nodes"])!=len(plan["images"]):
                raise ShopifyError("Recovery source/remote gallery changed. Review again; no mapping adopted.")
            for node in remote["media"]["nodes"]:
                _gid(node.get("id"),"MediaImage")
            data["created_media"]=[{"id":m["id"]} for m in remote["media"]["nodes"]]
            if len(data["media"])!=len(plan["images"]):
                raise ShopifyError("Local upload receipts incomplete; recovery cannot replace missing uploads.")
            for receipt,node in zip(data["media"],remote["media"]["nodes"]):
                receipt["id"]=node["id"]
            data["media_mapping_approved_at"]=drafts.now().isoformat()
            drafts.save(row,data)
    _save_maps(row,data,remote,recover=recover)
    fields_found = parent_rows(plan,remote)
    media = {m["source"]:m.get("id") for m in data.get("media",[])}
    for wanted in plan["variants"]:
        mapping = data.get("variant_maps",{}).get(wanted["identity"])
        if not mapping:
            raise ShopifyError("Reviewed variant creation remains incomplete/uncertain: "+wanted["sku"]+". No blind retry; inspect the retained operation/Shopify product.")
        actual = next(v for v in remote["variants"]["nodes"] if v["id"]==mapping["id"])
        fields_found += variant_rows(dict(wanted,media_id=media.get(wanted["media_source"])),actual)
    data["verification"] = {"identity":"verified","fields":"verified" if not any(f["changed"] for f in fields_found) else "differences"}
    drafts.save(row,data)
    if any(f["changed"] for f in fields_found):
        raise ShopifyError("Managed variable fields differ from the retained reviewed plan. No whole-operation retry; reconcile the recorded stage.")
    from app.shopify_taxonomy import verify_memberships
    verify_memberships(plan,config,adapter,row.shopify_product_id)
    from app.shopify_attributes import verify as verify_attributes
    remaining=False
    try:
        verify_attributes(plan,config,adapter,row.shopify_product_id)
    except ShopifyError:
        if not allow_attribute_review: raise
        from app.shopify_attributes import remaining_review
        remaining=remaining_review(plan,config,adapter,row.shopify_product_id)
    data["verification"]["attributes"]="remaining_review" if remaining else "verified"
    from app.shopify_customisations import verify as verify_customisations
    verify_customisations(row, data, config, adapter)
    from app.shopify_dimension_metadata import verify as verify_dimensions
    verify_dimensions(row, data, config, adapter)
    data["verification"]["memberships"] = "verified"
    expected = [m.get("id") for m in data.get("created_media",[])]
    observed = remote["media"]["nodes"]
    order=[m["id"] for m in observed]
    if len(expected)!=len(plan["images"]) or set(order)!=set(expected) or len(order)!=len(set(order)):
        raise ShopifyError("Variable gallery receipts/order are untrusted. Inspect media mapping; no duplicate upload or deletion is permitted.")
    if order!=expected and not data.get("reorder_job"):
        raise ShopifyError("Variable gallery order changed without a reviewed reorder job; inspect the retained gallery.")
    for image,mid in zip(plan["images"],expected):
        actual=next(m for m in observed if m["id"]==mid)
        if actual.get("alt") != image["alt"] or actual.get("mediaContentType") != "IMAGE" or actual.get("status") == "FAILED":
            raise ShopifyError("Variable gallery alt/content/processing failure requires recovery review.")
        receipt=next(m for m in data["media"] if m["source"]==image["source"])
        if receipt.get("id")!=mid:
            raise ShopifyError("Source-to-media mapping differs from the reviewed gallery; no automatic reassignment.")
        url=(actual.get("image") or {}).get("url")
        if url is not None and (not isinstance(url,str) or len(url)>1024):
            raise ShopifyError("Returned CDN URL exceeds the durable evidence bound; retained media ID remains protected. No URL is fabricated or upload retried.")
        receipt.update(id=mid,url=url,status=actual.get("status"))
    ready = order==expected and all(m.get("status")=="READY" and (m.get("image") or {}).get("url","").startswith("https://") for m in observed)
    data["verification"]["media"] = "verified" if ready else "processing"
    if recover:
        for key,proof in data.get("variant_native",{}).items():
            if proof.get("state") in {"pending","uncertain"}:
                observed=adapter.read_packed_inventory(config,proof["inventory_id"])
                mapping=data["variant_maps"][key]
                if observed.get("id")!=mapping["inventory_id"] or observed.get("sku")!=mapping["sku"] or not native.equal(observed["measurement"]["packedDimensions"],proof["desired"]):
                    raise ShopifyError("Native outcome remains unresolved for this InventoryItem. No native write replay allowed.")
                proof.update(state="verified",observed=observed["measurement"]["packedDimensions"],checked_at=drafts.now().isoformat())
    row.verification_state="base_verified" if ready and remaining else "verified" if ready else "processing"
    if ready and not remaining: row.verified_at=drafts.now()
    data.update(stage="attribute_review_required" if ready and remaining else "verified" if ready else "media_reordering" if order!=expected else "media_processing",checked_at=drafts.now().isoformat())
    data.pop("error",None)
    drafts.save(row,data,"attribute_review_required" if ready and remaining else "verified" if ready else "processing")
    if ready and not remaining:
        from app.shopify_comparison import record
        result = copy.deepcopy(plan)
        result.update(classification="no_change",fields=[dict(f,changed=False) for f in fields_found], remote_id=row.shopify_product_id,
            actual_status=remote["status"],remote_updated_at=remote.get("updatedAt"),remote_digest=drafts.digest(remote),
            collections=[dict(c,action="Already assigned") for c in plan["collections"]])
        product = db.session.get(Product,row.product_id)
        from app.shopify_publication import comparison_after_write
        record(product,comparison_after_write(product,result,row,adapter),row,post_write=True)
    return row


def complete_deferred_availability(row,adapter):
    """Finish the ORIGINAL approved variable availability after async readback.

    Called only by the accepted manual task, outside its read-only polling scope.
    No uploads, Create or variant writes; stale source and uncertain steps block.
    """
    from app.shopify_capacity import guard_owner
    guard_owner()
    data=drafts.evidence(row);plan=data['plan'];config=drafts.configuration()
    from app.shopify_publication import apply_reviewed,complete
    if data.get('kind')!='variable' or row.state!='verified' or complete(data):return row
    if (local_plan(db.session.get(Product,row.product_id))['digest']!=plan['digest'] or config.fingerprint()!=plan['config_digest']
            or len(data.get('variant_maps',{}))!=len(plan['variants']) or data.get('verification',{}).get('media')!='verified'):
        raise ShopifyError('Deferred variable availability source or full verification changed; no activation is authorised.')
    from app.models import CatalogueOperation
    from app.shopify_capacity import fence_owner
    operation=db.session.get(CatalogueOperation,row.operation_id)
    operation.status='running';fence_owner();db.session.commit()
    apply_reviewed(row,data,config,adapter)
    verify(row,data,config,adapter)
    native_state=_native_apply(row,data,config,adapter,row.operation_id) if row.state=='verified' else 'blocked'
    from app.shopify_package_assignments import apply as packages
    package_state=packages(row,adapter,row.operation_id)
    finish_catalogue_operation(row.operation_id,status='succeeded' if row.state=='verified' and native_state in {'disabled','verified'}
        and package_state in {'none','accepted'} and complete(data) else 'partial',
        products_attempted=1,products_succeeded=int(row.state=='verified'),recovery_state='review_required' if row.state=='uncertain' else 'none')
    return row


def confirm(reviewed, adapter=None):
    adapter = adapter or ShopifyAdapter()
    product = db.session.get(Product,reviewed["product_id"])
    if not product or product.product_type != "variable":
        raise ShopifyError("Reviewed variable parent is unavailable; no write attempted.")
    lease = acquire_catalogue_operation("shopify_draft_publish",{"product_id":product.id,"mode":"variable"})
    row, data, attempted, creating, previous = None, None, False, None, None
    try:
        resuming=reviewed.get('mode')=='variable_resume'
        plan,_ = preview(product,adapter,seed=reviewed["nonce"],resume_creation=resuming)
        for key in ("digest","store_id","config_digest","remote_id","remote_digest","identity_digest","taxonomy_review_digest","classification"):
            if plan.get(key)!=reviewed.get(key):
                raise ShopifyError("Variable source, identity, remote state, scopes or memberships changed after Review. No write attempted.")
        if plan["blockers"] or plan["classification"]=="no_change":
            raise ShopifyError("Resolve reviewed blockers or matching state; no unreviewed write allowed.")
        if resuming and plan.get('creation_resume')!=reviewed.get('creation_resume'):
            raise ShopifyError('Retained creation scope changed after recovery Review; no write attempted.')
        config = drafts.configuration()
        def fresh():
            if local_plan(product)["digest"]!=plan["digest"] or drafts.configuration().fingerprint()!=plan["config_digest"]:
                raise ShopifyError("Source/configuration changed during variable execution; retained successful stages remain, no further write allowed.")
        row = drafts.matching_identity(plan["store_id"],product)
        creating = not plan.get("remote_id")
        if creating:
            if drafts.upload_pending(row):
                data = drafts.evidence(row)
                if data['plan']['digest'] != plan['digest'] or data['plan'].get('config_digest') != config.fingerprint():
                    raise ShopifyError('Incomplete gallery source/configuration changed; no product Create attempted.')
                data['plan'] = plan
            else:
                data = {"version":1,"kind":"variable","plan":plan,"stage":"pending","completed_steps":[],"media":[],"created_media":[],
                    "variant_maps":{},"variant_native":{},"variant_creates_attempted":[],"create_attempted":False}
            if row and row.state == "remote_deleted":
                data["history_operation"] = drafts.evidence(row).get("history_operation")
            if row is None:
                row = ShopifyProductIdentity(product_id=product.id,source_identity=product.source_relpath,sku=product.sku,
                    store_key=plan["store_key"],store_id=plan["store_id"],store_domain=config.domain,
                    operation_id=lease.id,reviewed_digest=plan["digest"],state="pending",verification_state="unverified",media_evidence_json=drafts.encode(data))
                db.session.add(row); db.session.commit()  # SQLite constraints reserve before remote writes
            else:
                if row.product_id is None: row.product_id=product.id
                row.operation_id,row.reviewed_digest=lease.id,plan["digest"]
                drafts.save(row,data,"pending")
            uploads=drafts.upload_gallery(product,row,data,plan,config,adapter)
            drafts.completed_step(row,data,"staged_uploads")
            drafts.completed_step(row,data,"original_image_uploads")
            if local_plan(product)["digest"]!=plan["digest"]:
                raise ShopifyError("Local source changed during uploads; no product Create attempted.")
            data.update(stage="product_create",create_attempted=True)
            drafts.save(row,data,"creating"); attempted=True
            payload=dict(plan["managed"]["product"],status="DRAFT",handle=plan["handle"],productOptions=plan["options"])
            payload["metafields"]=[{"namespace":"tlc_dashboard","key":"publish_attempt","type":"single_line_text_field","value":plan["nonce"]},*payload.get("metafields",[])]
            response=adapter.create_variable(config,payload,uploads)
            created=(response.get("productCreate") or {}).get("product")
            if isinstance(created,dict) and created.get("id"):
                row.shopify_product_id=_gid(created["id"],"Product")
                data["created_media"]=(created.get("media") or {}).get("nodes",[])
                drafts.save(row,data,"created")
            drafts.payload_result(response,"productCreate")
            if not row.shopify_product_id:
                raise ShopifyError("Product create receipt is incomplete; resolve the retained correlation, never Create again.")
            drafts.completed_step(row,data,"product_create")
            remote=adapter.read_variable(config,row.shopify_product_id,plan["managed"]["namespace"])
            if getattr(adapter,'snapshot_restarts',None):data['gallery_snapshot_restarts']=(data.get('gallery_snapshot_restarts',[])+copy.deepcopy(adapter.snapshot_restarts))[-6:]
            nodes=remote_identity(row,data,remote)
            first=plan["variants"][0]
            if len(nodes)!=1 or nodes[0]["selectedOptions"]!=first["options"] or nodes[0]["sku"]:
                raise ShopifyError("New product's initial option variant is unexpected; no variant is deleted or adopted blindly.")
            first["remote_id"],first["inventory_id"]=nodes[0]["id"],nodes[0]["inventoryItem"]["id"]
            first["action"]="Update"
            # Reserve its exact initial GIDs before setting the reviewed SKU.
            data["variant_creates_attempted"].append(first["identity"])
        else:
            previous={"data":copy.deepcopy(drafts.evidence(row)),"operation_id":row.operation_id,"reviewed_digest":row.reviewed_digest,
                'state':row.state,'verification_state':row.verification_state}
            data=drafts.evidence(row)
            if resuming:
                data['creation_resume_from']={'operation_id':row.operation_id,'stage':data.get('stage'),'completed_steps':data.get('completed_steps',[])}
                for receipt,mid in zip(data['media'],plan['creation_resume']['media_ids']):receipt['id']=mid
                data['variant_creates_attempted']=[plan['variants'][0]['identity']]
            data.update(plan=plan,stage="reviewed_update",update_attempted=True,completed_steps=[],publication_review_operation_id=lease.id)
            row.operation_id,row.reviewed_digest,row.verification_state=lease.id,plan["digest"],"unverified"
            drafts.save(row,data,"updating")
            metadata,_=drafts.update_inputs(dict(plan,variant_id="unused"))
            if len(metadata)>1:
                fresh()
                data["stage"]="product_update"; drafts.save(row,data); attempted=True
                drafts.payload_result(adapter.update_simple_product(config,metadata),"productUpdate")
                drafts.completed_step(row,data,"product_update")
            actions=plan["media_actions"]
            if actions["alt"]:
                fresh()
                data["stage"]="media_alt"; drafts.save(row,data); attempted=True
                drafts.payload_result(adapter.update_media_alt(config,actions["alt"]),"fileUpdate")
                drafts.completed_step(row,data,"media_alt")
            if actions["reorder"]:
                fresh()
                data["stage"]="media_order"; drafts.save(row,data); attempted=True
                payload=drafts.payload_result(adapter.reorder_media(config,row.shopify_product_id,actions["ids"]),"productReorderMedia")
                data["reorder_job"]=(payload.get("job") or {}).get("id")
                drafts.completed_step(row,data,"media_order")
            data["created_media"]=[{"id":i} for i in actions["ids"]]
            drafts.save(row,data)
        if len(data["created_media"])!=len(plan["images"]):
            raise ShopifyError("Creation media receipts are missing/incomplete; inspect the retained mapping, no upload retry.")
        if creating:
            for receipt,created in zip(data["media"],data["created_media"]):
                receipt["id"]=_gid(created["id"],"MediaImage")
        media={r["source"]:r["id"] for r in data["media"]}
        updates, additions = [], []
        changed = {f.get("variant_identity") for f in plan["fields"] if f["changed"]}
        for wanted in plan["variants"]:
            if wanted["action"]=="Create":
                additions.append({**_payload(wanted,media),"optionValues":[{"optionName":o["name"],"name":o["value"]} for o in wanted["options"]]})
            elif creating:
                updates.append(dict(_payload(wanted,media),id=wanted["remote_id"]))
            elif wanted["identity"] in changed:
                leaves={f["leaf"] for f in plan["fields"] if f.get("variant_identity")==wanted["identity"] and f["changed"]}
                full=_payload(wanted,media)
                patch={"id":wanted["remote_id"]}
                for key in ("price","compareAtPrice","mediaId","inventoryPolicy"):
                    if key in leaves and key in full: patch[key]=full[key]
                inventory={k:value for k,value in full["inventoryItem"].items() if k in leaves or k=="measurement" and "weight" in leaves}
                if inventory: patch["inventoryItem"]=inventory
                meta=[m for m in full.get("metafields",[]) if "dimensions."+m["key"].removeprefix("product_") in leaves]
                if meta: patch["metafields"]=meta
                if len(patch)>1: updates.append(patch)
        if updates:
            fresh()
            data["stage"]="variant_update"; drafts.save(row,data); attempted=True
            drafts.payload_result(adapter.update_variants(config,row.shopify_product_id,updates),"productVariantsBulkUpdate")
            drafts.completed_step(row,data,"variant_update")
        if additions:
            fresh()
            data["variant_creates_attempted"] += [v["identity"] for v in plan["variants"] if v["action"]=="Create"]
            data["stage"]="variant_create"; drafts.save(row,data); attempted=True
            drafts.payload_result(adapter.create_variants(config,row.shopify_product_id,additions),"productVariantsBulkCreate")
            drafts.completed_step(row,data,"variant_create")
        _save_maps(row,data,adapter.read_variable(config,row.shopify_product_id,plan["managed"]["namespace"]))
        from app.shopify_taxonomy import join_reviewed
        join_reviewed(row,data,config,adapter)
        from app.shopify_attributes import apply as apply_attributes
        if any(a["changed"] for a in plan.get("attributes",[])):
            attempted=True
        apply_attributes(row,data,config,adapter)
        from app.shopify_customisations import apply as apply_customisations
        if (plan.get('customisations') or {}).get('changed'):
            attempted=True
        apply_customisations(row,data,config,adapter)
        from app.shopify_dimension_metadata import apply as apply_dimensions
        if any(s['changed'] for s in (plan.get('dimension_metadata') or {}).get('definitions', [])):
            attempted=True
        apply_dimensions(row,data,config,adapter)
        from app.shopify_publication import apply_reviewed
        if (creating or resuming) and plan.get('publication'):
            verify(row,data,config,adapter)
        if (plan.get('publication') or {}).get('pending') and (plan.get('publication') or {}).get('read_complete') and not (plan.get('publication') or {}).get('blockers'):
            attempted=True
        apply_reviewed(row,data,config,adapter)
        verify(row,data,config,adapter)
        native_state="blocked" if row.state=="processing" and plan["native"]["enabled"] else _native_apply(row,data,config,adapter,lease.id) if row.state=="verified" else "disabled"
        if native_state=="uncertain":
            drafts.save(row,data,"uncertain")
        elif row.state=="verified":
            # Re-record after native evidence without borrowing metadata timestamps.
            from app.shopify_comparison import ShopifyProductComparison, store_summary
            summary=ShopifyProductComparison.query.filter_by(store_id=row.store_id,source_identity=row.source_identity).first()
            if summary:
                value=json.loads(summary.summary_json); value.update(identity=drafts.digest(row.media_evidence_json),native=plan["native"])
                store_summary(summary,value)
        from app.shopify_package_assignments import apply as apply_packages
        package_state=apply_packages(row,adapter,lease.id)
        from app.shopify_publication import complete as publication_complete
        finish_catalogue_operation(lease.id,status="succeeded" if row.state=="verified" and native_state in {"disabled","verified"} and package_state in {'none', 'accepted'} and publication_complete(data) else "partial",
            recovery_state="review_required" if row.state=="uncertain" else "none",
            products_attempted=1,products_succeeded=int(row.state=="verified"),
            operation_summary={"identity_id":row.id,"mode":"variable","stage":drafts.evidence(row)['stage'],"variants":len(plan["variants"]),"native_dimensions":native_state,'package_assignment':package_state})
        return row
    except drafts.GalleryPending:
        data['upload_no_progress'] = data.get('upload_no_progress',0)+1 if len(data.get('upload_inputs',[]))==data.get('upload_start_count') else 0
        data.update(stage='gallery_uploading', error=None)
        drafts.save(row,data,'uploading')
        finish_catalogue_operation(lease.id,status='partial',recovery_state='none',products_attempted=1,
            operation_summary={'identity_id':row.id,'stage':'gallery_uploading','create_attempted':False,
                'uploads_acknowledged':len(data.get('upload_inputs',[])),'images':len(plan['images'])})
        return row
    except Exception as exc:
        db.session.rollback()
        if row and data:
            from app.shopify_package_assignments import attempted as package_attempted
            retained=drafts.evidence(row)
            if package_attempted(retained,lease.id):data=retained;attempted=True
            if not attempted and creating is False and previous:
                row.operation_id,row.reviewed_digest,row.verification_state=previous["operation_id"],previous["reviewed_digest"],previous['verification_state']
                drafts.save(row,previous["data"],previous['state'])
                finish_catalogue_operation(lease.id,status="failed",error="Variable update stopped before writes; prior identity evidence retained.")
                raise
            data["error"] = str(exc)[:512] if isinstance(exc,ShopifyError) else "Variable operation interrupted; inspect its retained stage. No automatic retry."
            drafts.save(row,data,"uncertain" if attempted or data.get("create_attempted") else "failed_before_create" if not data.get("create_attempted") else "verified")
            finish_catalogue_operation(lease.id,status="partial" if attempted else "failed",recovery_state="review_required" if attempted else "none",operation_summary={"identity_id":row.id,"stage":data["stage"]})
            return row
        from app.utils.operation_control import sanitize_operation_error
        finish_catalogue_operation(lease.id,status="failed",error="Variable review failed before remote writes. "+sanitize_operation_error(exc))
        raise


def reconcile(row,adapter=None, *, candidate_id=None, approval=None, allow_attribute_review=False):
    adapter=adapter or ShopifyAdapter(); config=drafts.configuration()
    shop=adapter.test_connection(config)
    if shop["shop_id"]!=row.store_id:
        raise ShopifyError("Restore this identity's verified store configuration; no migration/copy is allowed.")
    data=drafts.evidence(row)
    if not data.get("create_attempted"):
        raise ShopifyError("No product Create was attempted; build a fresh signed Review with duplicate checks.")
    if not row.shopify_product_id:
        found=adapter.find_draft(config,data["plan"]["handle"])
        if found["pageInfo"].get("hasNextPage") or len(found["nodes"])!=1:
            raise ShopifyError("Uncertain variable Create is unresolved. Inspect its retained handle; absence does not authorise another Create.")
        candidate_id=candidate_id or found["nodes"][0]["id"]
        temporary=SimpleNamespace(shopify_product_id=_gid(candidate_id,"Product"))
        remote=adapter.read_variable(config,temporary.shopify_product_id,data["plan"]["managed"]["namespace"])
        remote_identity(temporary,data,remote)
        row.shopify_product_id=temporary.shopify_product_id
        drafts.save(row,data)
    try:
        return verify(row,data,config,adapter,recover=True,approval=approval,allow_attribute_review=allow_attribute_review)
    except ShopifyError as exc:
        data["error"]=str(exc); drafts.save(row,data,"uncertain")
        raise


def native_status(product,row=None,compared=None):
    enabled=native.flag("SHOPIFY_NATIVE_DIMENSIONS_ENABLED")
    data=drafts.evidence(row) if row else {}
    proofs=data.get("variant_native",{})
    if any(v.get("state") in {"pending","uncertain"} for v in proofs.values()):
        return {"state":"uncertain","message":"A per-variant native InventoryItem outcome requires read-only reconciliation."}
    if not enabled:
        from app.packaging_contract import effective
        document = product_document(product)
        if any(native.has_packed(effective(document, {a.name: a.value for a in v.attributes})) for v in product.variations):
            return {'state': 'pending_capability', 'message': 'Authored packed dimensions await the opt-in Shopify 2027-01 capability; no native publication is claimed.'}
        return {"state":"disabled","message":"Native packed dimensions disabled; per-variant metafields are separate."}
    from app.shopify_comparison import recent, MAX_AGE
    try:
        wanted=projection(product)
        plans=(compared or {}).get("variants",{})
        stale = False
        configured = False
        for v in wanted:
            proof=proofs.get(v["identity"],{})
            if v['native'].get('unconfigured'):
                continue  # no authored target; historical remote values are not cleared
            configured = True
            read=plans.get(v["identity"],{})
            if (proof.get("state")=="verified" and recent(proof.get("checked_at"),MAX_AGE) and proof.get("desired")==v["native"]["desired"]):
                continue
            if read.get("classification")=="no_change" and recent((compared or {}).get("checked_at"),MAX_AGE) and read.get("desired")==v["native"]["desired"]:
                continue
            blocked=read.get("reason") or proof.get("state")=="blocked" or v["native"].get("reason")
            changed=(proof.get("state")=="verified" and proof.get("desired")!=v["native"]["desired"]) or (read.get("classification")=="update" and recent((compared or {}).get("checked_at"),MAX_AGE))
            if not blocked and not changed and ((proof.get('state')=='verified' and proof.get('desired')==v['native']['desired']) or
                    (read.get('classification')=='no_change' and read.get('desired')==v['native']['desired'])):
                stale = True
                continue
            return {"state":"blocked" if blocked else "update" if changed else "unavailable",
                "message":"Per-variant packed dimensions need independent review/readback; no Product.updatedAt equality assumed."}
        if stale:
            return {'state':'stale','message':'Last per-variant packed-dimension checks matched; freshness expired. Bounded readback is due, not a confirmed mismatch.'}
        if not configured:
            return {'state':'disabled','message':'Packed shipping dimensions are unconfigured; finished dimensions never substitute. Existing remote measurements are retained pending review.'}
        return {"state":"verified","message":"All managed variants have fresh, identity-bound packed-dimension comparison evidence."}
    except (ValueError,ShopifyError):
        return {"state":"blocked","message":"Per-variant packed source/projection needs validation."}
