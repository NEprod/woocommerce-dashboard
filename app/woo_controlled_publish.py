"""Explicit, bounded, two-pass WooCommerce publishing.

The catalogue and relationship JSON remain authoritative.  This module writes
only a currently regenerated Milestone 3 plan and stores verified, store-scoped
integration state in SQLite.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
import json
import threading
from time import monotonic
from urllib.parse import urlencode

from flask import current_app

from app import db
from app.models import (
    CatalogueOperation,
    CatalogueOperationItem,
    Product,
    WooProductIdentity,
    WooVariationIdentity,
)
from app.utils.discord import notify_woo_publish_completed
from app.utils.operation_control import (
    acquire_catalogue_operation,
    finish_catalogue_operation,
    sanitize_operation_error,
)
from app.utils.operation_live import persist_live_state
from app.utils.redaction import redact_diagnostic
from app.woo_publish_preview import (
    WordPressMediaResolver, _digest,
    _normalise_remote, canonical_taxonomy_slug, taxonomy_row_compatible,
    cached_plan,
    regenerate_publish_plan,
    store_identity,
)
from app.woo_managed_comparison import (
    managed_parent_attributes_equal, managed_taxonomy_membership_equal,
    managed_title_equal,
    managed_rich_text_equal,
    managed_variation_attributes_equal,
)
from app.woo_payload_contract import WooDimensionContractError, assert_woo_dimension_payload
from app.woocommerce_connection import (
    PublisherWooClient,
    WooConnectionError,
    build_woocommerce_workspace,
    effective_configuration,
)


OPERATION_TYPE = "woo_controlled_publish"
MAX_PUBLISH_PRODUCTS = 10
MAX_RESULT_ITEMS = 10
MAX_PENDING_RELATIONSHIPS = 100
MAX_WOO_DIAGNOSTICS = 10
ALLOWED_ACTIONS = {"create", "update", "no_change"}
UNCERTAIN_CATEGORIES = {
    "connect_timeout",
    "read_timeout",
    "connection_failed",
    "network_failure",
    "content_decoding_failed",
    "malformed_json",
    "json_too_deep",
    "response_too_large",
    "write_redirect_refused",
    "server_error",
}
MAX_TAXONOMY_CANDIDATES = 500
MAX_TAXONOMY_PAGES = 5
STAGES = (
    "revalidating_preview",
    "acquiring_publication_lock",
    "rechecking_woo_connection",
    "resolving_taxonomy",
    "publishing_parent_products",
    "verifying_parent_products",
    "publishing_variations",
    "verifying_variations",
    "attaching_verifying_images",
    "resolving_pass_2_relationships",
    "applying_relationships",
    "final_remote_verification",
    "saving_sync_state",
    "completed",
)


class ControlledPublishError(ValueError):
    def __init__(self, message, *, category="validation", recovery_required=False, remote_candidate_id=None):
        super().__init__(message)
        self.category = str(category)[:64]
        self.recovery_required = bool(recovery_required)
        self.remote_candidate_id = remote_candidate_id if isinstance(remote_candidate_id, int) else None


def _safe_ids(values):
    try:
        result = list(dict.fromkeys(int(value) for value in values))
    except (TypeError, ValueError) as error:
        raise ControlledPublishError("The selected product identity is invalid.") from error
    if not result:
        raise ControlledPublishError("Select at least one product to publish.")
    if len(result) > MAX_PUBLISH_PRODUCTS:
        raise ControlledPublishError(
            f"Controlled publishing is limited to {MAX_PUBLISH_PRODUCTS} parent products per operation."
        )
    return result


def _preview_operation(operation_id):
    operation = CatalogueOperation.query.filter_by(
        id=str(operation_id or "")[:32], operation_type="woo_publish_preview"
    ).first()
    if operation is None:
        raise ControlledPublishError("A valid Publish Preview is required.", category="missing_preview")
    plan = cached_plan(operation.id)
    if plan is None:
        raise ControlledPublishError(
            "Detailed Publish Preview is unavailable. Generate and review a fresh preview.",
            category="missing_preview",
        )
    return operation, plan


def _selected_products(plan, product_ids):
    product_ids = _safe_ids(product_ids)
    by_id = {int(item["product_id"]): item for item in plan.get("products", [])}
    if any(product_id not in by_id for product_id in product_ids):
        raise ControlledPublishError("The selected scope is not part of this Publish Preview.")
    selected = [by_id[product_id] for product_id in product_ids]
    for item in selected:
        if item.get("action") not in ALLOWED_ACTIONS:
            raise ControlledPublishError(
                f"{item.get('sku') or 'Product'} is classified {item.get('action', 'blocked').replace('_', ' ')} and cannot be published by this workflow."
            )
        if item.get("blockers"):
            raise ControlledPublishError(f"{item.get('sku') or 'Product'} has blocking preview findings.")
        unsafe_variations = [
            row for row in item.get("variations", [])
            if row.get("action") in {"blocked", "link_candidate"} or row.get("blockers")
        ]
        if unsafe_variations:
            raise ControlledPublishError(
                f"{item.get('sku') or 'Product'} has a variation identity that requires separate review."
            )
    return selected


def _reviewed_contract(plan, product_ids):
    """Identity-independent authored contract used only for safe resume."""

    selected = _selected_products(plan, product_ids)
    def taxonomy_contract(item):
        return {
            kind: [
                {key: row.get(key) for key in ("name", "slug", "attribute_slug") if row.get(key) is not None}
                for row in item.get("taxonomy", {}).get(kind, [])
            ]
            for kind in ("categories", "tags", "attributes", "terms")
        }

    def media_contract(item):
        return {
            "parent": [
                {key: image.get(key) for key in ("url", "attachment_id", "position", "ownership", "state")}
                for image in item.get("media", {}).get("parent", [])
            ],
            "variations": [{
                "variation_id": row.get("variation_id"),
                "sku": row.get("sku"),
                "images": [
                    {key: image.get(key) for key in ("url", "attachment_id", "position", "ownership", "state")}
                    for image in row.get("images", [])
                ],
            } for row in item.get("media", {}).get("variations", [])],
        }

    def payload_contract(payload):
        value = deepcopy(payload)
        value.pop("categories", None)
        value.pop("tags", None)
        for attribute in value.get("attributes", []):
            if isinstance(attribute, dict):
                attribute.pop("id", None)
        return _digest(value)

    return _digest({
        "store_identity": plan.get("summary", {}).get("store_identity"),
        "builder_version": plan.get("summary", {}).get("builder_version"),
        "mapping_version": plan.get("summary", {}).get("mapping_version"),
        "products": [{
            "product_id": item["product_id"],
            "stable_identity": item["stable_identity"],
            "sku": item["sku"],
            "woo_type": item["woo_type"],
            "payload_digest": payload_contract(item["payload"]),
            "taxonomy": taxonomy_contract(item),
            "media": media_contract(item),
            "relationships": {
                kind: [edge.get("sku") for edge in item.get("relationships", {}).get("groups", {}).get(kind, [])]
                for kind in ("cross_sell", "upsell")
            },
            "variations": [{
                "id": variation["id"],
                "stable_identity": variation["stable_identity"],
                "sku": variation["sku"],
                "payload_digest": payload_contract(variation["payload"]),
            } for variation in item.get("variations", [])],
        } for item in selected],
    })


def _confirmation(preview, approved_digest, plan, product_ids):
    selected = _selected_products(plan, product_ids)
    configuration = effective_configuration()
    if not configuration.complete:
        raise ControlledPublishError("WooCommerce runtime credentials are not configured.", category="connection_required")
    health = build_woocommerce_workspace()["health"]
    if health.get("state") not in {"connected", "connected_with_limitations"}:
        raise ControlledPublishError("A healthy WooCommerce connection test is required.", category="connection_required")
    actions = Counter(item.get("parent_action", item["action"]) for item in selected)
    taxonomy_rows = {
        (kind, row["slug"])
        for item in selected
        for kind in ("categories", "tags", "attributes")
        for row in item.get("taxonomy", {}).get(kind, [])
    }
    term_rows = {
        (row["attribute_slug"], row["slug"])
        for item in selected
        for row in item.get("taxonomy", {}).get("terms", [])
    }
    taxonomy_create = sum(
        row.get("state") == "create_required"
        for item in selected
        for rows in item.get("taxonomy", {}).values()
        for row in rows
    )
    variation_counts = Counter(
        variation.get("action") for item in selected for variation in item.get("variations", [])
    )
    relationship_count = sum(
        len(edges)
        for item in selected
        for edges in item.get("relationships", {}).get("groups", {}).values()
    )
    image_count = sum(
        item.get("media", {}).get("parent_count", 0)
        + item.get("media", {}).get("variation_count", 0)
        for item in selected
    )
    planned_writes = (
        actions["create"]
        + actions["update"]
        + variation_counts["create"]
        + variation_counts["update"]
        + variation_counts["pending_parent"]
        + taxonomy_create
        + sum(bool(item.get("relationships", {}).get("groups", {}).get(kind)) for item in selected for kind in ("cross_sell", "upsell"))
    )
    return {
        "preview_operation_id": preview.id,
        "preview_digest": approved_digest,
        "current_plan_digest": plan["digest"],
        "generated_at": plan["summary"]["generated_at"],
        "store_host": plan["summary"]["store_host"],
        "store_identity": plan["summary"]["store_identity"],
        "product_ids": [item["product_id"] for item in selected],
        "products": selected,
        "counts": {
            "create": actions["create"],
            "update": actions["update"],
            "no_change": actions["no_change"],
            "taxonomy_create": taxonomy_create,
            "taxonomy_reuse": len(taxonomy_rows) + len(term_rows) - taxonomy_create,
            "variation_create": variation_counts["create"] + variation_counts["pending_parent"],
            "variation_update": variation_counts["update"],
            "images": image_count,
            "relationships": relationship_count,
            "warnings": sum(len(item.get("warnings", [])) for item in selected),
            "blockers": 0,
            "estimated_writes": planned_writes,
        },
        "requires_live_acknowledgement": any(item.get("payload", {}).get("status") == "publish" for item in selected),
        "plan": plan,
    }


def prepare_publish_confirmation(
    preview_operation_id,
    preview_digest,
    product_ids,
    *,
    client=None,
):
    """Regenerate and validate one explicitly selected bounded plan."""

    preview, prior_plan = _preview_operation(preview_operation_id)
    if not preview_digest or preview_digest != prior_plan.get("digest"):
        raise ControlledPublishError("Preview stale — publishing refused.", category="stale_preview")
    regenerated = regenerate_publish_plan(prior_plan["summary"]["scope"], client=client)
    if regenerated["digest"] != prior_plan["digest"]:
        raise ControlledPublishError("Preview stale — publishing refused.", category="stale_preview")
    if regenerated["summary"]["store_identity"] != prior_plan["summary"]["store_identity"]:
        raise ControlledPublishError("The configured WooCommerce store changed after preview.", category="store_mismatch")
    return _confirmation(preview, prior_plan["digest"], regenerated, product_ids)


class PublishGateway:
    """Publisher-only route builder; every request is authenticated and same-origin."""

    def __init__(self, client, namespace):
        if not isinstance(client, PublisherWooClient) and not getattr(client, "publisher_policy", False):
            raise ControlledPublishError("The publisher-only Woo request policy is required.", category="unsafe_client")
        self.client = client
        self.namespace = namespace or "wc/v3"
        self.write_count = 0
        self.stage = "rechecking_woo_connection"

    def set_stage(self, stage):
        self.stage = str(stage or "controlled_publish")[:64]

    def url(self, route, params=None):
        base = f"{self.client.base_url}/wp-json/{self.namespace}/{route.lstrip('/')}"
        return f"{base}?{urlencode(params, doseq=True)}" if params else base

    def get(self, route, params=None):
        try:
            payload, _ = self.client.request_json(
                "GET", self.url(route, params), authenticated=True, endpoint_category="controlled_publish"
            )
            return payload
        except WooConnectionError as error:
            _attach_publish_diagnostic(error, method="GET", stage=self.stage, route=route)
            raise

    def write(self, method, route, payload):
        method = str(method).upper()
        if method not in {"POST", "PUT", "PATCH"}:
            raise ControlledPublishError("DELETE and unreviewed Woo methods are forbidden.", category="unsafe_method")
        try:
            assert_woo_dimension_payload(payload)
        except WooDimensionContractError as error:
            raise ControlledPublishError(
                "The WooCommerce dimension payload contract is invalid; publishing was refused before any request.",
                category="internal_contract",
            ) from error
        _assert_no_image_import_payload(payload)
        self.write_count += 1
        try:
            result, _ = self.client.request_json(
                method,
                self.url(route),
                authenticated=True,
                endpoint_category="controlled_publish",
                json_body=payload,
            )
            return result
        except WooConnectionError as error:
            _attach_publish_diagnostic(
                error, method=method, stage=self.stage, route=route,
                sku=payload.get("sku") if isinstance(payload, dict) else None,
                title=payload.get("name") if isinstance(payload, dict) else None,
            )
            raise


def _route_object(route):
    value = str(route or "").strip("/")
    if "/variations" in value:
        return "variation", "Variation"
    if "/terms" in value:
        return "attribute_term", "Attribute term"
    if value.startswith("products/attributes"):
        return "attribute", "Product attribute"
    if value.startswith("products/categories"):
        return "category", "Product category"
    if value.startswith("products/tags"):
        return "tag", "Product tag"
    return "product", "Parent product"


def _attach_publish_diagnostic(error, *, method, stage, route, sku=None, title=None):
    remote = error.remote_error if isinstance(getattr(error, "remote_error", None), dict) else {}
    status = error.status_code
    method = str(method).upper()[:8]
    uncertain = method in {"POST", "PUT", "PATCH"} and error.category in UNCERTAIN_CATEGORIES
    if status == 400:
        retry_state, guidance = "payload_correction_required", "Correct the reviewed payload or authored metadata, generate a fresh preview, then retry."
    elif status in {401, 403}:
        retry_state, guidance = "configuration_review_required", "Review WooCommerce credentials and permissions before retrying."
    elif status == 429:
        retry_state, guidance = "retry_later", "Wait for the store rate limit to clear, then review a safe resume."
    elif isinstance(status, int) and status >= 500:
        retry_state = "reconciliation_required" if uncertain else "transient_remote_failure"
        guidance = "Remote write state may be uncertain. Reconcile it after store health recovers before a safe resume." if uncertain else "Review store health before attempting a safe resume."
    elif uncertain:
        retry_state, guidance = "reconciliation_required", "Remote state is uncertain and must be reconciled before a safe resume."
    else:
        retry_state, guidance = "review_required", "Review this bounded WooCommerce diagnostic before retrying."
    object_type, object_label = _route_object(route)
    fields = {}
    for source in (remote.get("params"), remote.get("details")):
        if isinstance(source, dict):
            for key, value in source.items():
                if len(fields) >= 12:
                    break
                fields[str(key)[:80]] = redact_diagnostic(value, limit=240)
    error.publish_diagnostic = {
        "method": method, "stage": str(stage or "controlled_publish")[:64],
        "sku": redact_diagnostic(sku, limit=100) if sku else None,
        "title": redact_diagnostic(title, limit=160) if title else None,
        "object_type": object_type, "object_label": object_label,
        "http_status": status, "category": error.category,
        "remote_code": redact_diagnostic(remote.get("code"), limit=96) if remote.get("code") else None,
        "message": redact_diagnostic(remote.get("message") or error.message, limit=400),
        "fields": fields, "retry_state": retry_state,
        "remote_verified": False, "uncertain": uncertain,
        "recovery_required": uncertain,
        "guidance": guidance,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    error.recovery_required = uncertain


def _error_diagnostic(error, *, sku=None, title=None):
    candidate = error
    while candidate is not None:
        diagnostic = getattr(candidate, "publish_diagnostic", None)
        if isinstance(diagnostic, dict):
            result = dict(diagnostic)
            result["sku"] = result.get("sku") or (redact_diagnostic(sku, limit=100) if sku else None)
            result["title"] = result.get("title") or (redact_diagnostic(title, limit=160) if title else None)
            return result
        candidate = getattr(candidate, "__cause__", None)
    return None


def _record_publish_error(progress, error, *, result=None, item=None):
    gallery_diagnostic = getattr(error, "gallery_diagnostic", None)
    if result is not None and isinstance(gallery_diagnostic, dict):
        result["gallery_diagnostic"] = gallery_diagnostic
    diagnostic = _error_diagnostic(
        error,
        sku=(result or {}).get("sku"),
        title=(result or {}).get("title"),
    )
    if not diagnostic:
        return
    if len(progress.summary["woo_errors"]) < MAX_WOO_DIAGNOSTICS:
        progress.summary["woo_errors"].append(diagnostic)
    else:
        progress.summary["diagnostics_truncated"] += 1
    if result is not None:
        result["diagnostic"] = diagnostic
    if item is not None and item.database_state != "taxonomy_failed":
        item.database_state = "remote_publish_failed" if not diagnostic["uncertain"] else "remote_reconciliation_required"
    status = f"HTTP {diagnostic['http_status']}" if diagnostic.get("http_status") else diagnostic["category"].replace("_", " ")
    progress.logs.append({
        "sequence": progress.sequence, "severity": "error",
        "line": f"WooCommerce {diagnostic['stage'].replace('_', ' ')} failed for {diagnostic.get('sku') or diagnostic['object_label']}: {status} — {diagnostic['message']}",
    })
    progress.sequence += 1


def _exact(rows, *, key, value):
    return [row for row in rows if isinstance(row, dict) and str(row.get(key) or "").casefold() == str(value or "").casefold()]


def _verified_id(row, label):
    remote_id = row.get("id") if isinstance(row, dict) else None
    if not isinstance(remote_id, int) or remote_id <= 0:
        raise ControlledPublishError(f"WooCommerce did not return a valid {label} identity.", recovery_required=True)
    return remote_id


def _taxonomy_kind(route):
    if "/terms" in route:
        return "terms"
    return str(route).rstrip("/").rsplit("/", 1)[-1]


def _taxonomy_rows(gateway, route, item, kind):
    params = {"per_page": 100}
    if kind != "attributes":
        params["slug"] = item["slug"]
        rows = gateway.get(route, params) or []
        return rows if isinstance(rows, list) else []

    rows, seen_pages = [], set()
    for page in range(1, MAX_TAXONOMY_PAGES + 1):
        batch = gateway.get(route, {**params, "page": page}) or []
        if not isinstance(batch, list):
            return []
        signature = tuple(
            (row.get("id"), row.get("slug"), row.get("name"))
            for row in batch if isinstance(row, dict)
        )
        if signature in seen_pages:
            break
        seen_pages.add(signature)
        rows.extend(batch)
        if len(batch) < 100:
            break
    if len(rows) >= MAX_TAXONOMY_CANDIDATES:
        raise ControlledPublishError(
            "WooCommerce returned too many taxonomy candidates for bounded exact reconciliation.",
            category="taxonomy_reconciliation_required",
            recovery_required=True,
        )
    return rows


def _taxonomy_candidates(gateway, route, item, kind):
    authored = canonical_taxonomy_slug(item["slug"], kind)
    return [
        row for row in _taxonomy_rows(gateway, route, item, kind)
        if isinstance(row, dict)
        and canonical_taxonomy_slug(row.get("slug") or row.get("name"), kind) == authored
    ]


def _compatible_candidate(candidates, item, kind, label):
    matches = [row for row in candidates if taxonomy_row_compatible(row, item, kind)]
    if len(candidates) > 1 or len(matches) > 1:
        raise ControlledPublishError(f"{label} {item['name']} has ambiguous exact Woo matches.")
    if candidates and not matches:
        raise ControlledPublishError(
            f"{label} {item['name']} conflicts with an existing Woo object using the authored slug.",
            category="taxonomy_conflict",
            recovery_required=True,
        )
    return matches[0] if matches else None


def _taxonomy_create_payload(item, kind):
    if kind == "attributes":
        return {
            # Preserve the existing authored write contract. Woo adds the
            # semantic ``pa_`` prefix to global attribute slugs on read.
            "name": item["name"], "slug": item["slug"],
            "type": "select", "order_by": "menu_order", "has_archives": False,
        }
    return {"name": item["name"], "slug": item["slug"]}


def _resolve_one_taxonomy(gateway, route, item, *, label):
    kind = _taxonomy_kind(route)
    if item.get("state") == "ambiguous":
        raise ControlledPublishError(f"{label} {item['name']} has ambiguous exact Woo matches.")
    remote_id = item.get("woo_id")
    if remote_id:
        candidate = _compatible_candidate(_taxonomy_candidates(gateway, route, item, kind), item, kind, label)
        if candidate and _verified_id(candidate, label) == int(remote_id):
            try:
                observed = gateway.get(f"{route}/{int(remote_id)}")
            except WooConnectionError:
                observed = candidate
            if taxonomy_row_compatible(observed, item, kind):
                return int(remote_id), "reused"
        raise ControlledPublishError(
            f"Stored {label} identity no longer matches {item['name']}.",
            category="taxonomy_conflict", recovery_required=True,
            remote_candidate_id=int(remote_id),
        )
    candidate = _compatible_candidate(_taxonomy_candidates(gateway, route, item, kind), item, kind, label)
    if candidate:
        return _verified_id(candidate, label), "reused"
    try:
        created = gateway.write("POST", route, _taxonomy_create_payload(item, kind))
    except WooConnectionError as error:
        candidate = _compatible_candidate(_taxonomy_candidates(gateway, route, item, kind), item, kind, label)
        if candidate:
            return _verified_id(candidate, label), "reused"
        if error.category in UNCERTAIN_CATEGORIES:
            raise ControlledPublishError(
                f"The {label} create response was uncertain and exact reconciliation was inconclusive.",
                category="uncertain_response",
                recovery_required=True,
            ) from error
        raise
    remote_id = _verified_id(created, label)
    for _attempt in range(2):
        try:
            observed = gateway.get(f"{route}/{remote_id}")
        except WooConnectionError:
            observed = None
        if observed is not None:
            if taxonomy_row_compatible(observed, item, kind):
                return remote_id, "created"
            raise ControlledPublishError(
                f"Created {label} conflicts with the reviewed managed identity.",
                category="taxonomy_conflict", recovery_required=True,
                remote_candidate_id=remote_id,
            )
    candidate = _compatible_candidate(_taxonomy_candidates(gateway, route, item, kind), item, kind, label)
    if candidate and _verified_id(candidate, label) == remote_id:
        return remote_id, "created"
    raise ControlledPublishError(
        f"Created {label} could not be verified; the retained remote identity requires reconciliation.",
        category="taxonomy_reconciliation_required", recovery_required=True,
        remote_candidate_id=remote_id,
    )


def _resolve_taxonomy(gateway, confirmation):
    selected = confirmation["products"]
    unique = {kind: {} for kind in ("categories", "tags", "attributes")}
    terms = {}
    for product in selected:
        for kind in unique:
            for item in product.get("taxonomy", {}).get(kind, []):
                unique[kind][item["slug"]] = item
        for item in product.get("taxonomy", {}).get("terms", []):
            terms[(item["attribute_slug"], item["slug"])] = item
    resolved, counts, errors = {kind: {} for kind in unique}, Counter(), {}
    for kind, rows in unique.items():
        route = f"products/{kind}"
        for slug, item in sorted(rows.items()):
            try:
                remote_id, action = _resolve_one_taxonomy(gateway, route, item, label=kind[:-1] if kind.endswith("s") else kind)
                resolved[kind][slug] = remote_id
                counts[action] += 1
            except (ControlledPublishError, WooConnectionError) as error:
                errors[(kind, slug)] = error
                counts["failed"] += 1
    resolved["terms"] = {}
    for (attribute_slug, slug), item in sorted(terms.items()):
        attribute_id = resolved["attributes"].get(attribute_slug)
        if not attribute_id:
            errors[("terms", attribute_slug, slug)] = errors.get(
                ("attributes", attribute_slug),
                ControlledPublishError(f"Attribute identity for {attribute_slug} is unavailable."),
            )
            counts["failed"] += 1
            continue
        try:
            remote_id, action = _resolve_one_taxonomy(
                gateway,
                f"products/attributes/{attribute_id}/terms",
                item,
                label="attribute term",
            )
            resolved["terms"][(attribute_slug, slug)] = remote_id
            counts[action] += 1
        except (ControlledPublishError, WooConnectionError) as error:
            errors[("terms", attribute_slug, slug)] = error
            counts["failed"] += 1
    return resolved, counts, errors


def _product_taxonomy_error(product_plan, errors):
    for kind in ("categories", "tags", "attributes"):
        for item in product_plan.get("taxonomy", {}).get(kind, []):
            if (kind, item["slug"]) in errors:
                return errors[(kind, item["slug"])]
    for item in product_plan.get("taxonomy", {}).get("terms", []):
        key = ("terms", item["attribute_slug"], item["slug"])
        if key in errors:
            return errors[key]
    return None


def _resolved_product_payload(product_plan, taxonomy):
    payload = deepcopy(product_plan["payload"])
    payload["categories"] = [
        {"id": taxonomy["categories"][row["slug"]]}
        for row in product_plan.get("taxonomy", {}).get("categories", [])
    ]
    payload["tags"] = [
        {"id": taxonomy["tags"][row["slug"]]}
        for row in product_plan.get("taxonomy", {}).get("tags", [])
    ]
    global_ids = taxonomy.get("attributes", {})
    for attribute in payload.get("attributes", []):
        slug = "-".join(str(attribute.get("name") or "").strip().casefold().replace("_", "-").split())
        if slug in global_ids:
            attribute["id"] = global_ids[slug]
    return payload


def _resolved_variation_payload(variation_plan, taxonomy):
    """Bind a child selection to already verified global Woo taxonomies.

    The preview deliberately keeps scanner-authored attribute names.  At the
    write boundary, only verified global IDs are substituted; local/custom
    attributes retain Woo's name-and-option representation.
    """

    payload = deepcopy(variation_plan["payload"])
    global_ids = taxonomy.get("attributes", {})
    for attribute in payload.get("attributes", []):
        slug = canonical_taxonomy_slug(attribute.get("name"), "attributes")
        attribute_id = global_ids.get(slug)
        if isinstance(attribute_id, int) and attribute_id > 0:
            attribute["id"] = attribute_id
            attribute.pop("name", None)
    return payload


def _assert_no_image_import_payload(payload):
    """Controlled publishing must never import Media Library URLs as new media."""

    images = payload.get("images", []) if isinstance(payload, dict) else []
    image = payload.get("image") if isinstance(payload, dict) else None
    candidates = list(images) if isinstance(images, list) else []
    if isinstance(image, dict):
        candidates.append(image)
    if any(isinstance(item, dict) and item.get("src") for item in candidates):
        raise ControlledPublishError(
            "Existing WordPress media must use a verified attachment ID; URL import was refused before any request.",
            category="internal_contract",
        )
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), int) or item["id"] <= 0 for item in candidates):
        raise ControlledPublishError(
            "A verified WordPress attachment ID is required for every planned image.",
            category="media_identity_required",
        )


def _revalidate_media_identity(client, product_plan):
    resolver = WordPressMediaResolver(client)
    rows = product_plan.get("media", {}).get("parent", []) + [
        image
        for variation in product_plan.get("media", {}).get("variations", [])
        for image in variation.get("images", [])
    ]
    for item in rows:
        if item.get("state") != "existing_wordpress_media" or not item.get("attachment_id"):
            raise ControlledPublishError(
                "Media identity required before publishing.", category="media_identity_required"
            )
        current = resolver.resolve(item.get("url"))
        if current.get("state") != "existing_wordpress_media" or current.get("attachment_id") != item.get("attachment_id"):
            raise ControlledPublishError(
                "Preview stale — a WordPress media attachment identity changed.", category="stale_preview"
            )


def _verification_differences(payload, remote, *, default_category_id=None):
    """Compare only reviewed fields while tolerating documented Woo decoration."""

    observed = _normalise_remote(remote or {}, payload.keys())
    expected = deepcopy(payload)
    if expected.get("categories") == [] and default_category_id:
        if observed.get("categories") == [{"id": int(default_category_id)}]:
            observed["categories"] = []
    return [
        key for key in payload
        if not (
            managed_rich_text_equal(expected.get(key), observed.get(key))
            if key in {"description", "short_description"}
            else managed_title_equal(expected.get(key), observed.get(key))
            if key == "name"
            else managed_taxonomy_membership_equal(expected.get(key), observed.get(key))
            if key in {"categories", "tags"}
            else managed_parent_attributes_equal(expected.get(key), observed.get(key))
            if key == "attributes" and expected.get("type") == "variable"
            else managed_variation_attributes_equal(expected.get(key), observed.get(key))
            if key == "attributes" and isinstance(expected.get(key), list)
            and any(isinstance(row, dict) and "option" in row for row in expected["attributes"])
            else json.dumps(expected.get(key), sort_keys=True, default=str) == json.dumps(observed.get(key), sort_keys=True, default=str)
        )
    ]


def _product_matches(remote, plan, payload):
    if not isinstance(remote, dict):
        return False
    if remote.get("sku") != plan.get("sku") or remote.get("type") != plan.get("woo_type"):
        return False
    return not _verification_differences(
        payload, remote, default_category_id=plan.get("woo_default_category_id")
    )


def _upsert_product_identity(plan, store, remote, payload, operation_id):
    row = WooProductIdentity.query.filter_by(store_key=store["key"], product_id=plan["product_id"]).one_or_none()
    remote_id = _verified_id(remote, "product")
    conflict = WooProductIdentity.query.filter_by(store_key=store["key"], woo_product_id=remote_id).first()
    if conflict and conflict.product_id != plan["product_id"]:
        raise ControlledPublishError("The verified Woo product ID belongs to another local product.", recovery_required=True)
    if row is None:
        row = WooProductIdentity(
            product_id=plan["product_id"], stable_identity=plan["stable_identity"], sku=plan["sku"],
            store_key=store["key"], store_host=store["host"],
        )
        db.session.add(row)
    elif row.woo_product_id and row.woo_product_id != remote_id:
        raise ControlledPublishError("A conflicting verified Woo product identity cannot be overwritten.", recovery_required=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    row.woo_product_id = remote_id
    row.last_successful_sync_at = now
    row.last_published_digest = _digest(payload)
    row.last_remote_digest = _digest(_normalise_remote(remote, payload.keys()))
    row.sync_state = "synced"
    row.verification_state = "verified"
    row.stable_identity = plan["stable_identity"]
    row.sku = plan["sku"]
    row.store_host = store["host"]
    db.session.commit()
    return row


def _publish_parent(gateway, plan, payload, store, operation_id):
    # A Variable plan can be visibly actionable solely because expected child
    # work remains.  Its verified parent still uses the no-change path.
    action = plan.get("parent_action", plan["action"])
    if action == "no_change":
        remote_id = plan.get("woo_id")
        if not remote_id:
            raise ControlledPublishError("No-change product has no verified Woo identity.", recovery_required=True)
        remote = gateway.get(f"products/{int(remote_id)}", {"context": "edit"})
        if not _product_matches(remote, plan, payload):
            raise ControlledPublishError("The no-change product drifted after preview.", category="stale_preview")
        return _upsert_product_identity(plan, store, remote, payload, operation_id), remote, "no_change"
    if action == "create":
        matches = _exact(gateway.get("products", {"sku": plan["sku"], "per_page": 10, "context": "edit"}) or [], key="sku", value=plan["sku"])
        if matches:
            raise ControlledPublishError("An exact remote SKU appeared after preview; create was refused.", category="identity_conflict", recovery_required=len(matches) != 1)
        try:
            remote = gateway.write("POST", "products", payload)
        except WooConnectionError as error:
            if error.category not in UNCERTAIN_CATEGORIES:
                raise
            matches = _exact(gateway.get("products", {"sku": plan["sku"], "per_page": 10, "context": "edit"}) or [], key="sku", value=plan["sku"])
            if len(matches) != 1:
                raise ControlledPublishError("Product create response was uncertain and exact-SKU reconciliation was inconclusive.", category="uncertain_response", recovery_required=True) from error
            remote = matches[0]
    else:
        remote_id = plan.get("woo_id")
        if not remote_id:
            raise ControlledPublishError("A verified Woo identity is required for update.", recovery_required=True)
        before = gateway.get(f"products/{int(remote_id)}", {"context": "edit"})
        if before.get("sku") != plan["sku"] or before.get("type") != plan["woo_type"]:
            raise ControlledPublishError("The remote product identity conflicts with the reviewed plan.", recovery_required=True)
        try:
            remote = gateway.write("PUT", f"products/{int(remote_id)}", payload)
        except WooConnectionError as error:
            if error.category not in UNCERTAIN_CATEGORIES:
                raise
            remote = gateway.get(f"products/{int(remote_id)}", {"context": "edit"})
            if not _product_matches(remote, plan, payload):
                raise ControlledPublishError("Product update response was uncertain and reconciliation did not verify the reviewed state.", category="uncertain_response", recovery_required=True) from error
    remote_id = _verified_id(remote, "product")
    verified = gateway.get(f"products/{remote_id}", {"context": "edit"})
    if not _product_matches(verified, plan, payload):
        raise ControlledPublishError("Woo product verification did not match the reviewed managed fields.", recovery_required=True)
    return _upsert_product_identity(plan, store, verified, payload, operation_id), verified, action


def _variation_matches(remote, plan):
    return isinstance(remote, dict) and remote.get("sku") == plan.get("sku") and not _verification_differences(plan["payload"], remote)


def _gallery_ids(value):
    if not isinstance(value, list):
        return None
    ids = []
    for item in value:
        try:
            item = int(item)
        except (TypeError, ValueError):
            return None
        if item <= 0 or item in ids:
            return None
        ids.append(item)
    return ids


def _variation_gallery_diagnostic(plan, remote, *, capability):
    """Return bounded IDs which make a gallery result auditable without URLs."""

    image = (plan.get("payload") or {}).get("image") or {}
    observed_image = (remote or {}).get("image") or {}
    return {
        "capability": capability,
        "expected_primary_id": image.get("id"),
        "expected_gallery_ids": _gallery_ids(plan.get("gallery_image_ids", [])),
        "observed_primary_id": observed_image.get("id"),
        "observed_gallery_ids": _gallery_ids((remote or {}).get("gallery_image_ids")),
        "variation_sku": str(plan.get("sku") or "")[:100],
    }


def _sync_variation_gallery(gateway, parent_id, variation_id, plan, remote):
    """Verify the core wc/v3 secondary-variation gallery after its child write.

    The documented REST field is supplied in the create/update payload.  A
    read response which omits an *empty* gallery is accepted as indeterminate:
    there is no gallery state to prove.  A requested non-empty gallery must be
    returned in the authoritative read-back in the reviewed order.
    """

    expected = _gallery_ids(plan.get("gallery_image_ids", []))
    if expected is None:
        raise ControlledPublishError("The reviewed variation gallery identity is invalid.", category="internal_contract")
    if "gallery_image_ids" not in (remote or {}):
        if expected:
            error = ControlledPublishError(
                "WooCommerce did not return the requested variation secondary gallery after the child write.",
                category="variation_gallery_unsupported", recovery_required=True,
            )
            error.gallery_diagnostic = _variation_gallery_diagnostic(plan, remote, capability="unadvertised")
            raise error
        return remote, _variation_gallery_diagnostic(plan, remote, capability="unadvertised_empty")
    observed = _gallery_ids(remote.get("gallery_image_ids"))
    if observed == expected:
        return remote, _variation_gallery_diagnostic(plan, remote, capability="advertised")
    error = ControlledPublishError(
        "Variation secondary gallery verification did not match the reviewed attachment IDs.",
        recovery_required=True,
    )
    error.gallery_diagnostic = _variation_gallery_diagnostic(plan, remote, capability="advertised_mismatch")
    raise error


def _upsert_variation_identity(product_plan, variation_plan, store, parent_id, remote):
    row = WooVariationIdentity.query.filter_by(store_key=store["key"], variation_id=variation_plan["id"]).one_or_none()
    remote_id = _verified_id(remote, "variation")
    if row is None:
        row = WooVariationIdentity(
            variation_id=variation_plan["id"], product_id=product_plan["product_id"],
            stable_identity=variation_plan["stable_identity"], sku=variation_plan["sku"],
            store_key=store["key"], store_host=store["host"],
        )
        db.session.add(row)
    elif row.woo_variation_id and row.woo_variation_id != remote_id:
        raise ControlledPublishError("A conflicting verified Woo variation identity cannot be overwritten.", recovery_required=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    row.woo_parent_product_id = parent_id
    row.woo_variation_id = remote_id
    row.last_successful_sync_at = now
    row.last_published_digest = _digest(variation_plan["payload"])
    row.last_remote_digest = _digest(_normalise_remote(remote, variation_plan["payload"].keys()))
    row.verification_state = "verified"
    db.session.commit()
    return row


def _remote_variations(gateway, parent_id):
    rows = []
    for page in range(1, 6):
        batch = gateway.get(f"products/{parent_id}/variations", {"per_page": 100, "page": page, "context": "edit"}) or []
        if not isinstance(batch, list):
            break
        rows.extend(batch[:100])
        if len(batch) < 100:
            break
    return rows[:500]


def _publish_variations(gateway, product_plan, parent_id, store, taxonomy):
    results = []
    existing = _remote_variations(gateway, parent_id)
    for plan in product_plan.get("variations", []):
        plan = {**plan, "payload": _resolved_variation_payload(plan, taxonomy)}
        gallery_ids = _gallery_ids(plan.get("gallery_image_ids", []))
        if gallery_ids is None:
            raise ControlledPublishError("The reviewed variation gallery identity is invalid.", category="internal_contract")
        # wc/v3 defines this separately from the featured ``image``.  Include
        # actual secondary galleries with the first child write; do not infer
        # support from whether a different empty response serialised the key.
        write_payload = {
            **plan["payload"],
            **({"gallery_image_ids": gallery_ids} if gallery_ids else {}),
        }
        action = plan.get("action")
        remote = None
        if action == "no_change":
            remote = gateway.get(f"products/{parent_id}/variations/{int(plan['woo_id'])}", {"context": "edit"})
        elif action in {"create", "pending_parent"}:
            matches = _exact(existing, key="sku", value=plan["sku"])
            if matches:
                raise ControlledPublishError(f"Variation SKU {plan['sku']} appeared after preview; create was refused.", recovery_required=len(matches) != 1)
            try:
                remote = gateway.write("POST", f"products/{parent_id}/variations", write_payload)
            except WooConnectionError as error:
                if error.category not in UNCERTAIN_CATEGORIES:
                    raise
                matches = _exact(gateway.get(f"products/{parent_id}/variations", {"sku": plan["sku"], "per_page": 10, "context": "edit"}) or [], key="sku", value=plan["sku"])
                if len(matches) != 1:
                    raise ControlledPublishError("Variation create response was uncertain and reconciliation was inconclusive.", recovery_required=True) from error
                remote = matches[0]
        elif action == "update":
            remote_id = plan.get("woo_id")
            before = gateway.get(f"products/{parent_id}/variations/{int(remote_id)}", {"context": "edit"}) if remote_id else None
            if not before or before.get("sku") != plan["sku"]:
                raise ControlledPublishError("The remote variation identity conflicts with the reviewed plan.", recovery_required=True)
            try:
                remote = gateway.write("PUT", f"products/{parent_id}/variations/{int(remote_id)}", write_payload)
            except WooConnectionError as error:
                if error.category not in UNCERTAIN_CATEGORIES:
                    raise
                remote = gateway.get(f"products/{parent_id}/variations/{int(remote_id)}", {"context": "edit"})
                if not _variation_matches(remote, plan):
                    raise ControlledPublishError("Variation update response was uncertain and reconciliation failed.", recovery_required=True) from error
        else:
            raise ControlledPublishError(f"Variation {plan.get('sku')} is not eligible for controlled publishing.")
        remote_id = _verified_id(remote, "variation")
        verified = gateway.get(f"products/{parent_id}/variations/{remote_id}", {"context": "edit"})
        if not _variation_matches(verified, plan):
            raise ControlledPublishError(f"Variation {plan['sku']} did not match after verification.", recovery_required=True)
        verified, gallery_diagnostic = _sync_variation_gallery(gateway, parent_id, remote_id, plan, verified)
        _upsert_variation_identity(product_plan, plan, store, parent_id, verified)
        results.append({
            "sku": plan["sku"],
            "woo_id": remote_id,
            "action": action,
            "verified": True,
            "images": (1 if plan.get("payload", {}).get("image") else 0) + len(plan.get("gallery_image_ids", [])),
            "gallery": gallery_diagnostic,
        })
    return results


def _relationship_payload(product_plan, store):
    payload, pending = {"cross_sell_ids": [], "upsell_ids": []}, []
    for relationship_type, field in (("cross_sell", "cross_sell_ids"), ("upsell", "upsell_ids")):
        for edge in product_plan.get("relationships", {}).get("groups", {}).get(relationship_type, []):
            remote_id = edge.get("woo_id")
            if not remote_id:
                target = Product.query.filter_by(sku=edge.get("sku")).one_or_none()
                identity = (
                    WooProductIdentity.query.filter_by(store_key=store["key"], product_id=target.id).one_or_none()
                    if target else None
                )
                remote_id = identity.woo_product_id if identity and identity.verification_state == "verified" else None
            if remote_id:
                payload[field].append(int(remote_id))
            else:
                pending.append({"type": relationship_type, "sku": edge.get("sku"), "reason": "No verified current-store Woo identity."})
    return payload, pending[:MAX_PENDING_RELATIONSHIPS]


def _apply_relationships(gateway, product_plan, parent_id, store):
    payload, pending = _relationship_payload(product_plan, store)
    has_authored = any(product_plan.get("relationships", {}).get("groups", {}).get(kind) for kind in ("cross_sell", "upsell"))
    if not has_authored:
        return {"applied": 0, "pending": pending, "payload": payload}
    current = gateway.get(f"products/{parent_id}")
    current_payload = {
        "cross_sell_ids": [int(value) for value in current.get("cross_sell_ids", [])],
        "upsell_ids": [int(value) for value in current.get("upsell_ids", [])],
    }
    if current_payload != payload:
        try:
            gateway.write("PUT", f"products/{parent_id}", payload)
        except WooConnectionError as error:
            if error.category not in UNCERTAIN_CATEGORIES:
                raise
            observed = gateway.get(f"products/{parent_id}")
            if any([int(value) for value in observed.get(key, [])] != payload[key] for key in payload):
                raise ControlledPublishError("Relationship update response was uncertain and verification failed.", recovery_required=True) from error
    verified = gateway.get(f"products/{parent_id}")
    if any([int(value) for value in verified.get(key, [])] != payload[key] for key in payload):
        raise ControlledPublishError("Remote relationship ordering did not match the reviewed local order.", recovery_required=True)
    return {"applied": sum(len(value) for value in payload.values()), "pending": pending, "payload": payload}


class _Progress:
    def __init__(self, operation_id, confirmation):
        self.operation_id = operation_id
        self.confirmation = confirmation
        self.logs = []
        self.sequence = 1
        self.counts = Counter({
            "taxonomy_created": 0, "taxonomy_reused": 0,
            "parents_verified": 0, "variations_verified": 0,
            "images_verified": 0, "relationships_applied": 0,
            "failures": 0,
        })
        self.summary = {
            "preview_operation_id": confirmation["preview_operation_id"],
            "preview_digest": confirmation["preview_digest"],
            "store_host": confirmation["store_host"],
            "selected_products": len(confirmation["product_ids"]),
            "product_results": [],
            "taxonomy": {},
            "pending_relationships": [],
            "audit": [],
            "woo_errors": [],
            "diagnostics_truncated": 0,
        }

    def update(self, stage, message, *, current_item=""):
        self.logs.append({"sequence": self.sequence, "severity": "info", "line": message})
        self.sequence += 1
        persist_live_state(self.operation_id, {
            "stage": stage,
            "status": "running",
            "current_item": current_item,
            "latest_message": message,
            "progress": {"completed": STAGES.index(stage), "total": len(STAGES) - 1, "unit": "publishing stages"},
            "counts": dict(self.counts),
            "summary": self.summary,
            "next_sequence": self.sequence,
        }, self.logs)


def execute_publish_operation(operation_id, confirmation, *, client=None):
    """Execute an already revalidated plan. Tests pass a fictional publisher."""

    started = monotonic()
    progress = _Progress(operation_id, confirmation)
    configuration = effective_configuration()
    store = store_identity(configuration)
    gateway = PublishGateway(
        client or PublisherWooClient(configuration),
        confirmation["plan"].get("capability", {}).get("selected_namespace") or "wc/v3",
    )
    recovery_required = False
    fatal_error = None
    try:
        progress.update("revalidating_preview", "The approved Publish Preview digest was regenerated and verified.")
        gateway.set_stage("revalidating_preview")
        if store["key"] != confirmation["store_identity"]:
            raise ControlledPublishError("The configured WooCommerce store changed before publishing.", category="store_mismatch")
        progress.update("acquiring_publication_lock", "The dedicated controlled-publication lock is active.")
        progress.update("rechecking_woo_connection", "Required WooCommerce reads remain verified.")
        gateway.set_stage("rechecking_woo_connection")
        for plan in confirmation["products"]:
            db.session.add(CatalogueOperationItem(
                operation_id=operation_id, sku=plan["sku"], status="pending",
                database_state="pending", marker_state="not_applicable",
            ))
        db.session.commit()
        progress.update("resolving_taxonomy", "Resolving exact taxonomy identities required by the selected products.")
        gateway.set_stage("resolving_taxonomy")
        taxonomy, taxonomy_counts, taxonomy_errors = _resolve_taxonomy(gateway, confirmation)
        progress.summary["taxonomy"] = {
            **dict(taxonomy_counts),
            "failures": [
                {
                    "resource": "/".join(key),
                    "error": sanitize_operation_error(error),
                    "remote_candidate_id": getattr(error, "remote_candidate_id", None),
                }
                for key, error in list(taxonomy_errors.items())[:MAX_RESULT_ITEMS]
            ],
        }
        progress.counts.update({"taxonomy_created": taxonomy_counts["created"], "taxonomy_reused": taxonomy_counts["reused"]})
        progress.update("publishing_parent_products", "Creating or updating reviewed parent products.")
        gateway.set_stage("publishing_parent_products")
        for plan in confirmation["products"]:
            item = CatalogueOperationItem.query.filter_by(operation_id=operation_id, sku=plan["sku"]).one()
            result = {"product_id": plan["product_id"], "sku": plan["sku"], "title": plan["title"], "action": plan["action"], "status": "pending", "variations": [], "relationships": {}}
            taxonomy_error = None
            try:
                taxonomy_error = _product_taxonomy_error(plan, taxonomy_errors)
                if taxonomy_error:
                    raise ControlledPublishError(
                        f"Required taxonomy could not be resolved: {sanitize_operation_error(taxonomy_error)}",
                        category=getattr(taxonomy_error, "category", "taxonomy_failed"),
                        recovery_required=getattr(taxonomy_error, "recovery_required", False),
                    ) from taxonomy_error
                _revalidate_media_identity(gateway.client, plan)
                payload = _resolved_product_payload(plan, taxonomy)
                identity, remote, action = _publish_parent(gateway, plan, payload, store, operation_id)
                result.update({"woo_id": identity.woo_product_id, "status": "pass_1_verified", "action": action, "images": len(payload.get("images", []))})
                item.database_state = "parent_verified"
                item.status = "running"
                db.session.commit()
                progress.counts["parents_verified"] += 1
                progress.summary["audit"].append({"sku": plan["sku"], "woo_id": identity.woo_product_id, "action": action, "verification": "verified", "payload_digest": identity.last_published_digest, "remote_digest": identity.last_remote_digest, "operation_id": operation_id})
            except (ControlledPublishError, WooConnectionError) as error:
                result.update({"status": "recovery_required" if getattr(error, "recovery_required", False) else "failed", "error": sanitize_operation_error(error)})
                item.status = result["status"]
                item.database_state = "taxonomy_failed" if taxonomy_error else "identity_not_persisted"
                item.error = result["error"]
                _record_publish_error(progress, error, result=result, item=item)
                db.session.commit()
                progress.counts["failures"] += 1
                recovery_required = recovery_required or getattr(error, "recovery_required", False)
            progress.summary["product_results"].append(result)
        parent_verified = progress.counts["parents_verified"]
        parent_skipped = len(confirmation["products"]) - parent_verified
        progress.update(
            "verifying_parent_products",
            f"Parent publishing complete: {parent_verified} verified, {parent_skipped} skipped after a blocking dependency."
            if parent_skipped else
            f"Parent publishing complete: {parent_verified} verified and committed to store-scoped sync state.",
        )
        progress.update("publishing_variations", "Publishing reviewed variations beneath verified parent products.")
        gateway.set_stage("publishing_variations")
        for plan, result in zip(confirmation["products"], progress.summary["product_results"]):
            if result["status"] != "pass_1_verified" or not plan.get("variations"):
                continue
            try:
                result["variations"] = _publish_variations(gateway, plan, result["woo_id"], store, taxonomy)
                result["status"] = "pass_1_verified"
                progress.counts["variations_verified"] += len(result["variations"])
            except (ControlledPublishError, WooConnectionError) as error:
                result.update({"status": "recovery_required" if getattr(error, "recovery_required", False) else "failed", "error": sanitize_operation_error(error)})
                _record_publish_error(progress, error, result=result)
                progress.counts["failures"] += 1
                recovery_required = recovery_required or getattr(error, "recovery_required", False)
        planned_variations = sum(len(plan.get("variations", [])) for plan in confirmation["products"])
        variation_verified = progress.counts["variations_verified"]
        variation_skipped = max(0, planned_variations - variation_verified)
        progress.update(
            "verifying_variations",
            f"Variation publishing complete: {variation_verified} verified, {variation_skipped} skipped because a parent identity was unavailable."
            if variation_skipped else
            f"Variation publishing complete: {variation_verified} verified.",
        )
        progress.counts["images_verified"] = sum(
            int(result.get("images", 0))
            + sum(int(variation.get("images", 0)) for variation in result.get("variations", []))
            for result in progress.summary["product_results"]
            if result.get("status") == "pass_1_verified"
        )
        images_verified = progress.counts["images_verified"]
        safe_pass_1 = sum(result.get("status") == "pass_1_verified" for result in progress.summary["product_results"])
        progress.update(
            "attaching_verifying_images",
            f"Managed-object image verification complete: {images_verified} attachment references verified."
            if safe_pass_1 else
            "Managed-object image verification skipped — no safely published parent was available.",
        )
        progress.update(
            "resolving_pass_2_relationships",
            "Resolving ordered local relationship target SKUs to verified Woo product IDs."
            if safe_pass_1 else
            "Pass 2 relationship resolution skipped — no safe Pass 1 parent identity was available.",
        )
        progress.update(
            "applying_relationships",
            "Applying relationships only to products with safe Pass 1 identities."
            if safe_pass_1 else
            "Pass 2 relationship writes skipped — no safe Pass 1 parent identity was available.",
        )
        gateway.set_stage("applying_relationships")
        for plan, result in zip(confirmation["products"], progress.summary["product_results"]):
            if result["status"] != "pass_1_verified":
                continue
            try:
                relationship = _apply_relationships(gateway, plan, result["woo_id"], store)
                result["relationships"] = relationship
                progress.summary["pending_relationships"].extend({"source_sku": plan["sku"], **item} for item in relationship["pending"])
                progress.counts["relationships_applied"] += relationship["applied"]
                result["status"] = "verified_with_warnings" if relationship["pending"] else "verified"
            except (ControlledPublishError, WooConnectionError) as error:
                result.update({"status": "recovery_required" if getattr(error, "recovery_required", False) else "pass_2_failed", "error": sanitize_operation_error(error)})
                _record_publish_error(progress, error, result=result)
                progress.counts["failures"] += 1
                recovery_required = recovery_required or getattr(error, "recovery_required", False)
        progress.update(
            "final_remote_verification",
            f"Final remote verification complete for {safe_pass_1} safe parent product(s)."
            if safe_pass_1 else
            "Final remote verification skipped — no safe parent identity was available.",
        )
        progress.update("saving_sync_state", "Saving bounded verified sync and audit state.")
        for result in progress.summary["product_results"]:
            item = CatalogueOperationItem.query.filter_by(operation_id=operation_id, sku=result["sku"]).one_or_none()
            if item:
                item.status = "succeeded" if result["status"] in {"verified", "verified_with_warnings"} else "failed"
                if item.status == "succeeded":
                    item.database_state = "verified"
                elif item.database_state not in {
                    "remote_publish_failed", "remote_reconciliation_required",
                    "identity_not_persisted", "taxonomy_failed",
                }:
                    item.database_state = "remote_publish_failed"
                item.error = result.get("error")
                item.finished_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()
    except (ControlledPublishError, WooConnectionError) as error:
        fatal_error = error
        _record_publish_error(progress, error)
        recovery_required = recovery_required or getattr(error, "recovery_required", False)
        progress.counts["failures"] += 1
    except Exception:
        current_app.logger.exception("Controlled WooCommerce publishing failed unexpectedly")
        fatal_error = ControlledPublishError("Controlled publishing failed unexpectedly. Review the operation before retrying.", recovery_required=True)
        recovery_required = True
        progress.counts["failures"] += 1

    if fatal_error:
        for item in CatalogueOperationItem.query.filter_by(operation_id=operation_id, status="pending").all():
            item.status = "recovery_required" if recovery_required else "failed"
            item.database_state = (
                "taxonomy_failed" if gateway.stage == "resolving_taxonomy"
                else "remote_reconciliation_required" if recovery_required
                else "remote_publish_failed"
            )
            item.error = sanitize_operation_error(fatal_error)
            item.finished_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()

    verified = sum(result.get("status") in {"verified", "verified_with_warnings"} for result in progress.summary["product_results"])
    failed = len(confirmation["products"]) - verified
    pending = len(progress.summary["pending_relationships"])
    progress.summary.update({
        "created": sum(bool(result.get("action") == "create" and result.get("woo_id")) for result in progress.summary["product_results"]),
        "updated": sum(bool(result.get("action") == "update" and result.get("woo_id")) for result in progress.summary["product_results"]),
        "no_change": sum(bool(result.get("action") == "no_change" and result.get("woo_id")) for result in progress.summary["product_results"]),
        "verified_products": verified,
        "failed_products": failed,
        "pending_relationship_count": pending,
        "request_count": gateway.client.request_count,
        "write_request_count": gateway.write_count,
        "duration_ms": max(0, int((monotonic() - started) * 1000)),
        "recovery_required": recovery_required,
        "failure": sanitize_operation_error(fatal_error) if fatal_error else None,
        "counts": dict(progress.counts),
    })
    status = "failed" if failed == len(confirmation["products"]) else "partial" if failed or pending or recovery_required else "succeeded"
    progress.logs.append({"sequence": progress.sequence, "severity": "error" if status == "failed" else "warning" if status == "partial" else "info", "line": "Controlled publishing completed with recovery items." if status == "partial" else "Controlled publishing failed safely." if status == "failed" else "Controlled two-pass publishing completed and verified."})
    try:
        discord_ok, discord_message = notify_woo_publish_completed(progress.summary, operation_id=operation_id)
    except Exception:
        discord_ok, discord_message = False, "delivery failed"
    discord = {"state": "sent" if discord_ok else str(discord_message).replace(" ", "_")[:64], "label": "Discord sent" if discord_ok else "Discord delivery failed", "events": [{"event": "terminal_summary", "state": "sent" if discord_ok else "failed"}]}
    persist_live_state(operation_id, {
        "stage": "completed", "status": status, "current_item": "", "latest_message": progress.logs[-1]["line"],
        "progress": {"completed": len(STAGES) - 1, "total": len(STAGES) - 1, "unit": "publishing stages"}, "counts": dict(progress.counts),
        "summary": progress.summary, "discord": discord, "next_sequence": progress.sequence + 1,
    }, progress.logs)
    finish_catalogue_operation(
        operation_id,
        status=status,
        products_attempted=len(confirmation["products"]),
        products_succeeded=verified,
        products_failed=failed,
        error=fatal_error,
        recovery_state="review_required" if recovery_required else "none",
        marker_state="not_applicable",
        operation_summary=progress.summary,
    )
    return progress.summary


def start_publish_operation(confirmation, *, app=None, client=None, run_async=True):
    scope = {
        "preview_operation_id": confirmation["preview_operation_id"],
        "preview_digest": confirmation["preview_digest"],
        "store_identity": confirmation["store_identity"],
        "store_host": confirmation["store_host"],
        "product_ids": confirmation["product_ids"],
        "planned_actions": [{"product_id": item["product_id"], "sku": item["sku"], "action": item["action"]} for item in confirmation["products"]],
    }
    lease = acquire_catalogue_operation(OPERATION_TYPE, scope)
    persist_live_state(lease.id, {
        "stage": "acquiring_publication_lock", "status": "running", "current_item": confirmation["store_host"],
        "latest_message": "Controlled WooCommerce publication was confirmed.",
        "progress": {"completed": 1, "total": len(STAGES) - 1, "unit": "publishing stages"}, "counts": {}, "summary": {}, "next_sequence": 2,
    }, [{"sequence": 1, "severity": "info", "line": "Controlled WooCommerce publication was explicitly confirmed."}])
    if not run_async:
        execute_publish_operation(lease.id, confirmation, client=client)
        return lease.id
    app = app or current_app._get_current_object()

    def target():
        with app.app_context():
            execute_publish_operation(lease.id, confirmation, client=client)

    thread = threading.Thread(target=target, name=f"woo-publish-{lease.id[:8]}", daemon=True)
    try:
        thread.start()
    except Exception as error:
        finish_catalogue_operation(
            lease.id,
            status="failed",
            products_attempted=len(confirmation["products"]),
            products_failed=len(confirmation["products"]),
            error="Controlled publishing could not start; no Woo write was sent.",
            recovery_state="none",
            marker_state="not_applicable",
            operation_summary={
                "preview_operation_id": confirmation["preview_operation_id"],
                "preview_digest": confirmation["preview_digest"],
                "store_host": confirmation["store_host"],
                "selected_products": len(confirmation["products"]),
                "write_request_count": 0,
                "failure": "Controlled publishing could not start; no Woo write was sent.",
            },
        )
        raise ControlledPublishError("Controlled publishing could not start; no Woo write was sent.") from error
    return lease.id


def resume_confirmation(operation_id, *, client=None):
    operation = CatalogueOperation.query.filter_by(id=operation_id, operation_type=OPERATION_TYPE).first()
    if operation is None:
        raise ControlledPublishError("Publishing operation was not found.")
    try:
        scope = json.loads(operation.scope or "{}")
    except (TypeError, ValueError):
        scope = {}
    if not isinstance(scope, dict) or not scope.get("preview_operation_id"):
        raise ControlledPublishError("This operation does not retain a resumable reviewed scope.")
    preview, prior_plan = _preview_operation(scope["preview_operation_id"])
    if scope.get("preview_digest") != prior_plan.get("digest"):
        raise ControlledPublishError("Preview stale — operation cannot resume from the old plan. Generate and approve a new preview.", category="stale_preview")
    regenerated = regenerate_publish_plan(prior_plan["summary"]["scope"], client=client)
    product_ids = scope.get("product_ids") or []
    if regenerated["summary"]["store_identity"] != scope.get("store_identity"):
        raise ControlledPublishError("The configured WooCommerce store changed after publishing.", category="store_mismatch")
    try:
        unchanged = _reviewed_contract(prior_plan, product_ids) == _reviewed_contract(regenerated, product_ids)
    except ControlledPublishError as error:
        raise ControlledPublishError(
            "Preview stale — operation cannot resume from the old plan. Generate and approve a new preview.",
            category="stale_preview",
        ) from error
    if not unchanged:
        raise ControlledPublishError(
            "Preview stale — operation cannot resume from the old plan. Generate and approve a new preview.",
            category="stale_preview",
        )
    return _confirmation(preview, prior_plan["digest"], regenerated, product_ids)
