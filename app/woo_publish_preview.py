"""Read-only Woo payload preview, identity resolution, and two-pass planning."""

from __future__ import annotations

from collections import Counter, OrderedDict
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from time import monotonic
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

from flask import current_app
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    CatalogueOperation, Collection, Product, ProductAttribute, ProductRelationship,
    Variation, WooProductIdentity, WooVariationIdentity,
)
from app.publishing import projected_publishing_intent
from app.utils.discord import notify_woo_publish_preview_completed
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
from app.woo_payload_contract import (
    WooDimensionContractError,
    canonical_woo_dimensions,
)
from app.woo_managed_comparison import (
    managed_parent_attributes_equal, managed_taxonomy_membership_equal,
    managed_title_equal,
    managed_rich_text_equal,
    managed_variation_attributes_equal,
)
from app.woocommerce_connection import ReadOnlyWooClient, WooConnectionError, build_woocommerce_workspace, effective_configuration, normalize_store_url


OPERATION_TYPE = "woo_publish_preview"
BUILDER_VERSION = "phase3-m4-taxonomy-reconcile-v1"
MAPPING_VERSION = "woo-v3-managed-fields-v2"
MAX_SCOPE_PRODUCTS = 1000
LARGE_SCOPE_THRESHOLD = 100
MAX_CACHE_PLANS = 20
MAX_REMOTE_TAXONOMY_PAGES = 5
MAX_MEDIA_CANDIDATES = 20
DEFAULT_PRODUCT_CATEGORY_OPTION = "default_product_cat"
MANAGED_FIELDS = (
    "name", "type", "status", "description", "short_description", "sku",
    "regular_price", "sale_price", "date_on_sale_from", "date_on_sale_to",
    "weight", "dimensions", "manage_stock", "stock_quantity", "stock_status",
    "backorders", "categories", "tags", "attributes", "images",
)


class PreviewError(ValueError):
    def __init__(self, message, *, category="validation", details=None):
        super().__init__(message)
        self.category = category
        self.details = details or {}


class LinkCandidateError(PreviewError):
    """A reviewed exact-SKU identity can no longer be adopted safely."""


class WooUnlinkError(PreviewError):
    """A reviewed local Woo identity can no longer be removed safely."""


_PLAN_CACHE: OrderedDict[str, dict] = OrderedDict()


def _stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value):
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def store_identity(configuration=None):
    configuration = configuration or effective_configuration()
    origin = normalize_store_url(configuration.store_url)
    parsed = urlsplit(origin)
    host = (parsed.hostname or "").lower()[:253]
    safe_identity = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return {"key": hashlib.sha256(safe_identity.encode()).hexdigest(), "host": host, "origin": safe_identity}


def _number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _date(value):
    return value.isoformat() if value else None


def _attribute_values(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (TypeError, ValueError):
        pass
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _product_query():
    return Product.query.options(
        joinedload(Product.collection), selectinload(Product.categories),
        selectinload(Product.tags), selectinload(Product.attributes),
        selectinload(Product.images), selectinload(Product.assets),
        selectinload(Product.relationship_edges),
        selectinload(Product.variations).selectinload(Variation.attributes),
        selectinload(Product.variations).selectinload(Variation.images),
        selectinload(Product.variations).selectinload(Variation.assets),
    )


def resolve_scope(scope):
    if not isinstance(scope, dict):
        raise PreviewError("A valid preview scope is required.")
    kind = str(scope.get("kind") or "").strip()
    query = _product_query()
    if kind == "product":
        ids = [int(scope.get("product_id"))]
        query = query.filter(Product.id.in_(ids))
    elif kind == "selected":
        raw = scope.get("product_ids") or []
        if not isinstance(raw, list) or not raw:
            raise PreviewError("Select at least one product.")
        ids = list(dict.fromkeys(int(value) for value in raw))[:MAX_SCOPE_PRODUCTS]
        query = query.filter(Product.id.in_(ids))
    elif kind == "collection":
        query = query.filter(Product.collection_id == int(scope.get("collection_id")))
    elif kind == "all_active":
        query = query.filter(Product.catalogue_status == "active")
    else:
        raise PreviewError("Unsupported preview scope.")
    products = query.order_by(Product.title.asc(), Product.sku.asc(), Product.id.asc()).limit(MAX_SCOPE_PRODUCTS + 1).all()
    if not products:
        raise PreviewError("The selected scope contains no products.")
    if len(products) > MAX_SCOPE_PRODUCTS:
        raise PreviewError(f"Preview scope exceeds the {MAX_SCOPE_PRODUCTS}-product safety limit.")
    return products


def _estimate_products(products):
    media_urls = {
        image.url
        for product in products
        for image in list(product.images) + [image for variation in product.variations for image in variation.images]
        if image.url
    }
    return {
        "parent_products": len(products),
        "variations": sum(len(row.variations) for row in products),
        "images": sum(len(row.images) + sum(len(v.images) for v in row.variations) for row in products),
        "relationships": sum(len(row.relationship_edges) for row in products),
        # An exact-SKU match can reveal an existing variable parent even when no
        # store-scoped identity exists yet, so reserve one bounded variation GET
        # for every variable parent in the estimate.
        # Products, three taxonomy collections, one default-category settings
        # read, bounded variation reads, and one deduplicated media lookup per
        # distinct stored public URL.
        "estimated_woo_reads": len(products) + 4 + len(media_urls) + sum(
            min(MAX_REMOTE_TAXONOMY_PAGES, max(1, (len(row.variations) + 99) // 100))
            for row in products if row.product_type == "variable"
        ),
        "large_scope": len(products) >= LARGE_SCOPE_THRESHOLD,
    }


def scope_estimate(scope):
    return _estimate_products(resolve_scope(scope))


def preview_landing():
    collections = Collection.query.order_by(Collection.name.asc(), Collection.id.asc()).all()
    products = Product.query.filter(Product.catalogue_status == "active").order_by(Product.title.asc(), Product.sku.asc()).limit(100).all()
    history = CatalogueOperation.query.filter_by(operation_type=OPERATION_TYPE).order_by(CatalogueOperation.started_at.desc()).limit(20).all()
    return {
        "collections": collections,
        "products": products,
        "active_count": Product.query.filter_by(catalogue_status="active").count(),
        "history": history,
        "connection": build_woocommerce_workspace(),
    }


class PreviewWooReader:
    def __init__(self, client, namespace="wc/v3"):
        self.client = client
        self.namespace = namespace
        self.cache = {}

    def _url(self, route, params=None):
        base = f"{self.client.base_url}/wp-json/{self.namespace}/{route.lstrip('/')}"
        return f"{base}?{urlencode(params, doseq=True)}" if params else base

    def get(self, route, params=None, *, category="publish_preview"):
        key = (route, tuple(sorted((params or {}).items())))
        if key not in self.cache:
            payload, _ = self.client.request_json("GET", self._url(route, params), authenticated=True, endpoint_category=category)
            self.cache[key] = payload
        return self.cache[key]

    def product_by_id(self, remote_id):
        return self.get(f"products/{int(remote_id)}", {"context": "edit"})

    def products_by_sku(self, sku):
        payload = self.get("products", {"sku": sku, "per_page": 10, "context": "edit"})
        return payload if isinstance(payload, list) else []

    def variations(self, parent_id):
        rows = []
        for page in range(1, MAX_REMOTE_TAXONOMY_PAGES + 1):
            payload = self.get(f"products/{int(parent_id)}/variations", {"per_page": 100, "page": page})
            if not isinstance(payload, list):
                break
            rows.extend(payload[:100])
            if len(payload) < 100:
                break
        return rows[: MAX_REMOTE_TAXONOMY_PAGES * 100]

    def taxonomy(self, route):
        rows = []
        for page in range(1, MAX_REMOTE_TAXONOMY_PAGES + 1):
            payload = self.get(route, {"per_page": 100, "page": page})
            if not isinstance(payload, list):
                break
            rows.extend(payload[:100])
            if len(payload) < 100:
                break
        return rows[: MAX_REMOTE_TAXONOMY_PAGES * 100]

    def default_product_category_id(self):
        """Return the configured Woo default product category when readable."""

        try:
            payload = self.get("settings/products", category="publish_preview_settings")
        except WooConnectionError:
            payload = []
        for item in payload[:100] if isinstance(payload, list) else []:
            if not isinstance(item, dict) or item.get("id") not in {
                DEFAULT_PRODUCT_CATEGORY_OPTION, "woocommerce_default_category",
                "woocommerce_default_product_category",
            }:
                continue
            try:
                value = int(item.get("value"))
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None
        return self.admin_default_product_category_id()

    @staticmethod
    def _positive_category_id(value):
        if isinstance(value, dict):
            value = value.get("value", value.get("id"))
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def admin_default_product_category_id(self):
        """Read Woo's canonical product-category option through wc-admin.

        The current Products settings group does not necessarily register this
        legacy Woo option, even though Woo itself uses it when assigning a
        category to otherwise uncategorised products.  This is a bounded,
        authenticated, GET-only option read; it does not infer a category from
        a public product response.
        """

        url = f"{self.client.base_url}/wp-json/wc-admin/options?{urlencode({'options': DEFAULT_PRODUCT_CATEGORY_OPTION})}"
        key = ("wc_admin_option", DEFAULT_PRODUCT_CATEGORY_OPTION)
        try:
            if key not in self.cache:
                payload, _ = self.client.request_json(
                    "GET", url, authenticated=True,
                    endpoint_category="publish_preview_settings",
                )
                self.cache[key] = payload
            payload = self.cache[key]
        except WooConnectionError:
            return None
        if isinstance(payload, dict):
            direct = self._positive_category_id(payload.get(DEFAULT_PRODUCT_CATEGORY_OPTION))
            if direct:
                return direct
            data = payload.get("data")
            if isinstance(data, dict):
                return self._positive_category_id(data.get(DEFAULT_PRODUCT_CATEGORY_OPTION))
        return None

    def direct_default_product_category_id(self):
        """Read the individual setting only when a comparison requires it."""

        # Some stores omit the value from the collection response while still
        # exposing the documented individual setting resource.
        for setting_id in (
            DEFAULT_PRODUCT_CATEGORY_OPTION, "woocommerce_default_category",
            "woocommerce_default_product_category",
        ):
            try:
                item = self.get(
                    f"settings/products/{setting_id}",
                    category="publish_preview_settings",
                )
            except WooConnectionError:
                continue
            if not isinstance(item, dict):
                continue
            try:
                value = int(item.get("value"))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    def infer_default_product_category_id(self, remote):
        """Safely identify the default omitted by Woo's public Store API.

        The Store API deliberately excludes the configured default category
        from a product's category list.  A single set difference against the
        authenticated admin representation therefore identifies it without a
        hard-coded ID or destructive probe.
        """

        direct = self.direct_default_product_category_id()
        if direct:
            return direct
        if not isinstance(remote, dict) or not isinstance(remote.get("id"), int):
            return None
        admin_ids = {
            int(item["id"])
            for item in remote.get("categories", [])
            if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] > 0
        }
        if not admin_ids:
            return None
        url = f"{self.client.base_url}/wp-json/wc/store/v1/products/{remote['id']}"
        key = ("store_api_product", int(remote["id"]))
        try:
            if key not in self.cache:
                payload, _ = self.client.request_json(
                    "GET", url, authenticated=True,
                    endpoint_category="publish_preview_default_category",
                )
                self.cache[key] = payload
            public = self.cache[key]
        except WooConnectionError:
            return None
        if not isinstance(public, dict):
            return None
        public_ids = {
            int(item["id"])
            for item in public.get("categories", [])
            if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] > 0
        }
        missing = admin_ids - public_ids
        return next(iter(missing)) if public_ids <= admin_ids and len(missing) == 1 else None


def _normalised_public_media_url(value, store_origin):
    """Canonicalise identity-preserving public URL equivalences only."""

    try:
        parsed = urlsplit(str(value or "").strip())
        store = urlsplit(normalize_store_url(store_origin))
        port = parsed.port
    except (ValueError, WooConnectionError):
        return None
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    parsed_port = port or 443
    store_port = store.port or 443
    if (parsed.hostname.casefold(), parsed_port) != ((store.hostname or "").casefold(), store_port):
        return None
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    netloc = f"[{host}]" if ":" in host else host
    if parsed_port != 443:
        netloc = f"{netloc}:{parsed_port}"
    path = quote(unquote(parsed.path or "/"), safe="/:@-._~!$&'()*+,;=")
    return urlunsplit(("https", netloc, path, "", ""))


class WordPressMediaResolver:
    """Bounded, GET-only exact WordPress attachment identity resolution."""

    def __init__(self, client):
        self.client = client
        self.base_url = normalize_store_url(client.base_url)
        self.cache = {}

    def _url(self, params):
        return f"{self.base_url}/wp-json/wp/v2/media?{urlencode(params, doseq=True)}"

    def resolve(self, public_url):
        normalised = _normalised_public_media_url(public_url, self.base_url)
        if not normalised:
            return {
                "state": "invalid_url", "attachment_id": None,
                "message": "Stored final image URL is not valid for the configured Woo store.",
            }
        if normalised in self.cache:
            return dict(self.cache[normalised])
        filename = unquote(urlsplit(normalised).path.rsplit("/", 1)[-1])
        if not filename:
            result = {"state": "invalid_url", "attachment_id": None, "message": "Stored final image URL has no filename."}
            self.cache[normalised] = result
            return dict(result)
        search = filename.rsplit(".", 1)[0][:100]
        try:
            payload, response = self.client.request_json(
                "GET", self._url({"search": search, "per_page": MAX_MEDIA_CANDIDATES, "page": 1, "_fields": "id,source_url"}),
                authenticated=True, endpoint_category="wordpress_media_identity",
            )
        except WooConnectionError as error:
            result = {
                "state": "unreachable", "attachment_id": None,
                "message": f"WordPress Media identity lookup is unavailable ({error.category.replace('_', ' ')}).",
            }
            self.cache[normalised] = result
            return dict(result)
        candidates = payload[:MAX_MEDIA_CANDIDATES] if isinstance(payload, list) else []
        response_headers = getattr(response, "headers", {}) or {}
        try:
            total_candidates = int(response_headers.get("X-WP-Total", len(candidates)))
        except (TypeError, ValueError):
            total_candidates = len(candidates)
        matches = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            candidate = _normalised_public_media_url(item.get("source_url"), self.base_url)
            try:
                attachment_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if candidate == normalised and attachment_id > 0:
                matches.append(attachment_id)
        matches = list(dict.fromkeys(matches))
        if total_candidates > MAX_MEDIA_CANDIDATES:
            result = {
                "state": "ambiguous", "attachment_id": None,
                "message": "Media Library lookup exceeded the bounded candidate limit; no attachment was selected.",
            }
        elif len(matches) == 1:
            result = {
                "state": "existing_wordpress_media", "attachment_id": matches[0],
                "message": "Existing WordPress Media Library attachment verified.",
            }
        elif len(matches) > 1:
            result = {
                "state": "ambiguous", "attachment_id": None,
                "message": "More than one Media Library candidate matched the exact public URL.",
            }
        else:
            result = {
                "state": "not_found", "attachment_id": None,
                "message": "Existing WordPress Media Library attachment not found.",
            }
        self.cache[normalised] = result
        return dict(result)


def _normalised_slug(value):
    return "-".join(str(value or "").strip().casefold().replace("_", "-").split())


def canonical_taxonomy_slug(value, kind):
    """Return the authored identity used across Woo taxonomy representations."""

    slug = _normalised_slug(value)
    if kind == "attributes" and slug.startswith("pa-"):
        slug = slug[3:]
    return slug


def taxonomy_row_compatible(row, item, kind):
    if not isinstance(row, dict):
        return False
    if canonical_taxonomy_slug(row.get("slug") or row.get("name"), kind) != canonical_taxonomy_slug(item.get("slug"), kind):
        return False
    if _text_identity(row.get("name")) != _text_identity(item.get("name")):
        return False
    if kind == "attributes":
        expected = {"type": "select", "order_by": "menu_order", "has_archives": False}
        return all(row.get(key, value) == value for key, value in expected.items())
    return True


def _text_identity(value):
    return " ".join(str(value or "").strip().casefold().split())


def _taxonomy_plan(products, reader):
    categories = sorted({(row.slug or _normalised_slug(row.name), row.name) for product in products for row in product.categories})
    tags = sorted({(row.slug or _normalised_slug(row.name), row.name) for product in products for row in product.tags})
    attributes = sorted({(_normalised_slug(row.name), row.name) for product in products for row in product.attributes if row.is_global})
    routes = (("categories", categories), ("tags", tags), ("attributes", attributes))
    result, remote = {}, {}
    for kind, values in routes:
        try:
            remote[kind] = reader.taxonomy(f"products/{kind}")
        except WooConnectionError:
            remote[kind] = []
        by_slug = {}
        for item in remote[kind]:
            if isinstance(item, dict):
                by_slug.setdefault(canonical_taxonomy_slug(item.get("slug") or item.get("name"), kind), []).append(item)
        planned = []
        for slug, name in values:
            candidates = by_slug.get(canonical_taxonomy_slug(slug, kind), [])
            matches = [row for row in candidates if taxonomy_row_compatible(row, {"slug": slug, "name": name}, kind)]
            state = "existing" if len(candidates) == len(matches) == 1 else "ambiguous" if candidates else "create_required"
            affected = sum(
                any((getattr(row, "slug", None) or _normalised_slug(row.name)) == slug for row in getattr(product, kind))
                for product in products
            )
            planned.append({"name": name, "slug": slug, "state": state, "woo_id": matches[0].get("id") if len(matches) == 1 else None, "affected_products": affected})
        result[kind] = planned
    terms = []
    attribute_plan = {item["slug"]: item for item in result["attributes"]}
    used_terms = {}
    for product in products:
        for attribute in product.attributes:
            if attribute.is_global:
                used_terms.setdefault(_normalised_slug(attribute.name), set()).update(_attribute_values(attribute.values))
    for slug, values in sorted(used_terms.items()):
        attribute = attribute_plan.get(slug)
        remote_terms = []
        if attribute and attribute.get("woo_id"):
            try: remote_terms = reader.taxonomy(f"products/attributes/{attribute['woo_id']}/terms")
            except WooConnectionError: remote_terms = []
        by_slug = {}
        for item in remote_terms:
            if isinstance(item, dict): by_slug.setdefault(_normalised_slug(item.get("slug") or item.get("name")), []).append(item)
        for value in sorted(values):
            matches = by_slug.get(_normalised_slug(value), [])
            state = "existing" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "create_required"
            affected = sum(
                any(attribute.is_global and _normalised_slug(attribute.name) == slug and value in _attribute_values(attribute.values) for attribute in product.attributes)
                for product in products
            )
            terms.append({"attribute_slug": slug, "name": value, "slug": _normalised_slug(value), "state": state, "woo_id": matches[0].get("id") if len(matches) == 1 else None, "affected_products": affected})
    result["terms"] = terms
    return result


def _taxonomy_payload(product, taxonomy):
    def refs(kind, local):
        plan = {item["slug"]: item for item in taxonomy[kind]}
        return [{"id": plan[row.slug or _normalised_slug(row.name)]["woo_id"]} for row in local if plan.get(row.slug or _normalised_slug(row.name), {}).get("woo_id")]
    return refs("categories", product.categories), refs("tags", product.tags)


def _product_taxonomy_plan(product, taxonomy):
    result = {}
    for kind in ("categories", "tags", "attributes"):
        local = getattr(product, kind)
        slugs = {getattr(row, "slug", None) or _normalised_slug(row.name) for row in local}
        result[kind] = [item for item in taxonomy[kind] if item["slug"] in slugs]
    attribute_values = {
        _normalised_slug(row.name): set(_attribute_values(row.values))
        for row in product.attributes if row.is_global
    }
    result["terms"] = [item for item in taxonomy["terms"] if item["attribute_slug"] in attribute_values and item["name"] in attribute_values[item["attribute_slug"]]]
    return result


def _product_payload(product, taxonomy, media=None):
    categories, tags = _taxonomy_payload(product, taxonomy)
    intent = projected_publishing_intent(product.published)
    status = "publish" if product.published is True else "draft" if product.published is False else None
    payload = {
        "name": product.title, "type": "variable" if product.product_type == "variable" else "simple" if product.product_type == "simple" else product.product_type,
        "status": status, "description": product.description or "", "short_description": product.short_description or "", "sku": product.sku,
        "regular_price": _number(product.regular_price), "sale_price": _number(product.sale_price),
        "date_on_sale_from": _date(product.sale_start), "date_on_sale_to": _date(product.sale_end), "weight": _number(product.weight),
        "dimensions": canonical_woo_dimensions({"length": product.length, "width": product.width, "height": product.height}),
        "manage_stock": bool(product.manage_stock), "stock_quantity": product.stock_quantity, "stock_status": "instock" if product.in_stock is not False else "outofstock", "backorders": product.backorders,
        "categories": categories, "tags": tags,
        "attributes": [{"name": item.name, "options": _attribute_values(item.values), "visible": item.visible is not False, "variation": product.product_type == "variable"} for item in sorted(product.attributes, key=lambda row: (row.position, row.id))],
        "images": [
            {"id": item["attachment_id"], "position": item["position"]}
            for item in (media or {}).get("parent", [])
            if item.get("state") == "existing_wordpress_media" and item.get("attachment_id")
        ],
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    if product.product_type == "variable":
        # Scanner-resolved parent values remain useful defaults for constructing
        # variations, but Woo manages sellable prices and stock on variation
        # resources.  Do not claim those derived parent representations are
        # direct Variable-parent managed state.
        for key in (
            "regular_price", "sale_price", "date_on_sale_from", "date_on_sale_to",
            "manage_stock", "stock_quantity", "stock_status", "backorders",
        ):
            payload.pop(key, None)
    return payload, {"catalogue_state": product.catalogue_status, "publishing_intent": intent["label"], "planned_status": status}


def _variation_payload(variation, media=None):
    resolved_image = next((
        item for item in (media or {}).get("images", [])
        if item.get("state") == "existing_wordpress_media" and item.get("attachment_id")
    ), None)
    return {key: value for key, value in {
        "sku": variation.sku, "regular_price": _number(variation.regular_price), "sale_price": _number(variation.sale_price),
        "date_on_sale_from": _date(variation.sale_start), "date_on_sale_to": _date(variation.sale_end), "weight": _number(variation.weight),
        "dimensions": canonical_woo_dimensions({"length": variation.length, "width": variation.width, "height": variation.height}),
        "manage_stock": bool(variation.manage_stock), "stock_quantity": variation.stock_quantity, "stock_status": "instock" if variation.catalogue_status == "active" else "outofstock", "backorders": variation.backorders,
        "attributes": [{"name": item.name, "option": item.value} for item in sorted(variation.attributes, key=lambda row: row.id)],
        "image": {"id": resolved_image["attachment_id"]} if resolved_image else None,
    }.items() if value is not None}


def _variation_gallery_attachment_ids(media):
    """Return only the ordered secondary, already verified variation media."""

    return [
        item["attachment_id"]
        for item in (media or {}).get("images", [])[1:]
        if item.get("state") == "existing_wordpress_media"
        and isinstance(item.get("attachment_id"), int)
        and item["attachment_id"] > 0
    ]


def _normalise_remote(remote, managed_fields=None):
    result = {}
    for key in (managed_fields or MANAGED_FIELDS):
        if key not in remote:
            continue
        value = remote[key]
        if key in {"description", "short_description"} and isinstance(value, dict):
            value = value.get("raw") if value.get("raw") is not None else value.get("rendered")
        if key in {"categories", "tags"} and isinstance(value, list):
            value = [{"id": item.get("id")} for item in value if isinstance(item, dict) and item.get("id") is not None]
        elif key == "images" and isinstance(value, list):
            value = [
                ({"id": item.get("id"), "position": item.get("position", index)} if item.get("id") is not None else {"src": item.get("src"), "position": item.get("position", index)})
                for index, item in enumerate(value) if isinstance(item, dict)
            ]
        elif key == "image" and isinstance(value, dict):
            value = {"id": value.get("id")} if value.get("id") is not None else {"src": value.get("src")}
        elif key == "dimensions":
            try:
                value = canonical_woo_dimensions(value)
            except WooDimensionContractError:
                value = value if isinstance(value, dict) else {"invalid": str(value)[:100]}
        result[key] = value
    return result


def _comparison(payload, remote, *, default_category_id=None, known_attribute_ids=None, variation_attributes=False):
    remote_managed = _normalise_remote(remote or {}, payload.keys())
    if payload.get("categories") == [] and default_category_id:
        observed_categories = remote_managed.get("categories")
        if observed_categories == [{"id": int(default_category_id)}]:
            remote_managed["categories"] = []
    differences = []
    for key in payload:
        local = payload.get(key)
        observed = remote_managed.get(key)
        equal = (
            managed_rich_text_equal(local, observed)
            if key in {"description", "short_description"}
            else managed_title_equal(local, observed)
            if key == "name"
            else managed_taxonomy_membership_equal(local, observed)
            if key in {"categories", "tags"}
            else managed_parent_attributes_equal(local, observed, known_attribute_ids=known_attribute_ids)
            if key == "attributes" and payload.get("type") == "variable"
            else managed_variation_attributes_equal(local, observed, known_attribute_ids=known_attribute_ids)
            if key == "attributes" and variation_attributes
            else _stable_json(local) == _stable_json(observed)
        )
        if not equal:
            differences.append({"field": key, "local": local, "remote": observed, "planned": "update"})
    return remote_managed, differences


def _variation_identity_map(product_ids, store_key):
    return {row.variation_id: row for row in WooVariationIdentity.query.filter(WooVariationIdentity.product_id.in_(product_ids), WooVariationIdentity.store_key == store_key).all()}


def _product_identity_map(product_ids, store_key):
    return {row.product_id: row for row in WooProductIdentity.query.filter(WooProductIdentity.product_id.in_(product_ids), WooProductIdentity.store_key == store_key).all()}


def _identity_resolution(product, store, reader, *, identity=None, other=None):
    if other and not identity:
        return {"state": "store_mismatch", "action": "blocked", "blocker": "A Woo identity exists for another configured store and cannot be reused.", "identity": None, "remote": None}
    if not product.sku:
        return {"state": "missing_sku", "action": "blocked", "blocker": "A stable SKU is required for safe Woo identity resolution.", "identity": identity, "remote": None}
    if identity and identity.woo_product_id:
        try:
            remote = reader.product_by_id(identity.woo_product_id)
        except WooConnectionError as error:
            if error.category == "not_found":
                return {"state": "remote_missing", "action": "recovery_required", "blocker": "The stored Woo product ID no longer resolves.", "identity": identity, "remote": None}
            raise
        if not isinstance(remote, dict) or int(remote.get("id") or 0) != identity.woo_product_id or (remote.get("sku") and remote.get("sku") != product.sku):
            return {"state": "identity_conflict", "action": "recovery_required", "blocker": "The stored Woo identity resolves to a conflicting product or SKU.", "identity": identity, "remote": remote if isinstance(remote, dict) else None}
        return {"state": "verified", "action": None, "identity": identity, "remote": remote}
    matches = [row for row in reader.products_by_sku(product.sku) if isinstance(row, dict) and row.get("sku") == product.sku]
    if not matches:
        return {"state": "unlinked", "action": "create", "identity": identity, "remote": None}
    if len(matches) > 1:
        return {"state": "duplicate_remote_sku", "action": "blocked", "blocker": "Multiple Woo products use this exact SKU.", "identity": identity, "remote": None}
    remote = matches[0]
    expected_type = "variable" if product.product_type == "variable" else "simple"
    if remote.get("type") and remote.get("type") != expected_type:
        return {"state": "type_conflict", "action": "blocked", "blocker": "The exact-SKU Woo product has a conflicting product type.", "identity": identity, "remote": remote}
    return {"state": "link_candidate", "action": "link_candidate", "identity": identity, "remote": remote}


def _relationship_plan(product, selected_ids, identities, targets):
    groups = {"cross_sell": [], "upsell": []}; blockers = []; pending = 0
    for edge in sorted(product.relationship_edges, key=lambda row: (row.relationship_type, row.position, row.id)):
        item = {"sku": edge.target_sku, "position": edge.position, "woo_id": None, "state": "broken"}
        target = targets.get(edge.resolved_target_product_id)
        if not target:
            blockers.append(f"Relationship target {edge.target_sku} is unresolved locally.")
        else:
            identity = identities.get(target.id)
            if identity and identity.woo_product_id:
                item.update({"woo_id": identity.woo_product_id, "state": "ready"})
            elif target.id in selected_ids:
                item["state"] = "pending_pass_2"; pending += 1
            else:
                # Milestone 4 may safely publish Pass 1 while leaving an
                # outside-batch target without a verified Woo identity pending.
                # The relationship is never sent as a SKU or guessed ID.
                item["state"] = "pending_pass_2"; pending += 1
        groups.setdefault(edge.relationship_type, []).append(item)
    payload = {
        "cross_sell_ids": [item["woo_id"] for item in groups["cross_sell"] if item["woo_id"]],
        "upsell_ids": [item["woo_id"] for item in groups["upsell"] if item["woo_id"]],
        "pending_cross_sell_target_skus": [item["sku"] for item in groups["cross_sell"] if item["state"] == "pending_pass_2"],
        "pending_upsell_target_skus": [item["sku"] for item in groups["upsell"] if item["state"] == "pending_pass_2"],
    }
    return {"groups": groups, "payload": payload, "pending_count": pending, "blockers": blockers}


def _media_plan(product, resolver):
    def planned(image, ownership):
        identity = resolver.resolve(image.url) if image.url else {
            "state": "missing_url", "attachment_id": None,
            "message": "No stored final public image URL is available.",
        }
        return {
            "url": image.url, "position": image.position, "ownership": ownership,
            "state": identity["state"], "attachment_id": identity.get("attachment_id"),
            "message": identity.get("message"),
            "action": "Reuse existing attachment" if identity["state"] == "existing_wordpress_media" else "Review required",
        }
    parent = [planned(image, "parent") for image in sorted(product.images, key=lambda row: (row.position, row.id))]
    variations = [
        {
            "variation_id": variation.id, "sku": variation.sku,
            "images": [planned(image, "variation") for image in sorted(variation.images, key=lambda row: (row.position, row.id))],
        }
        for variation in sorted(product.variations, key=lambda row: (row.menu_order, row.id))
    ]
    all_images = parent + [image for row in variations for image in row["images"]]
    return {
        "parent": parent, "variations": variations,
        "parent_count": len(parent), "variation_count": sum(len(row["images"]) for row in variations),
        "missing_count": sum(item["state"] != "existing_wordpress_media" for item in all_images),
    }


def _local_state(products, identities, variation_identities):
    return [{
        "id": p.id, "stable_identity": p.source_relpath or f"product:{p.id}", "sku": p.sku, "resolved_row": p.resolved_row_json,
        "title": p.title, "slug": p.slug, "status": p.catalogue_status, "published": p.published, "type": p.product_type,
        "content": [p.description, p.short_description],
        "prices": [_number(p.regular_price), _number(p.sale_price), _date(p.sale_start), _date(p.sale_end)],
        "shipping": [_number(p.weight), _number(p.length), _number(p.width), _number(p.height), p.shipping_class],
        "stock": [p.manage_stock, p.stock_quantity, p.in_stock, p.backorders],
        "taxonomy": {"categories": [(row.slug, row.name) for row in p.categories], "tags": [(row.slug, row.name) for row in p.tags]},
        "attributes": [(row.position, row.name, row.values, row.visible, row.is_global) for row in p.attributes],
        "images": [(i.position, i.url) for i in p.images],
        "variations": [(
            v.id, v.source_identity, v.sku, v.resolved_row_json, v.catalogue_status,
            [_number(v.regular_price), _number(v.sale_price), _date(v.sale_start), _date(v.sale_end)],
            [_number(v.weight), _number(v.length), _number(v.width), _number(v.height)],
            [v.manage_stock, v.stock_quantity, v.backorders, v.visible, v.is_default, v.menu_order],
            [(a.position, a.name, a.value, a.visible, a.is_global) for a in v.attributes],
            [(i.position, i.url) for i in v.images],
        ) for v in p.variations],
        "relationships": [(e.relationship_type, e.position, e.target_sku) for e in p.relationship_edges],
        "identity": (identities[p.id].woo_product_id, identities[p.id].last_published_digest, identities[p.id].last_remote_digest) if p.id in identities else None,
        "variation_identities": [(v.id, variation_identities[v.id].woo_variation_id, variation_identities[v.id].last_published_digest) for v in p.variations if v.id in variation_identities],
    } for p in products]


def _scope_summary(scope):
    clean = {"kind": scope.get("kind")}
    for key in ("product_id", "collection_id"):
        if scope.get(key) is not None: clean[key] = int(scope[key])
    if scope.get("product_ids"): clean["product_ids"] = [int(value) for value in scope["product_ids"][:MAX_SCOPE_PRODUCTS]]
    return clean


def generate_publish_plan(scope, *, confirm_large=False, client=None, record_operation=True):
    products = resolve_scope(scope)
    estimate = _estimate_products(products)
    if len(products) >= LARGE_SCOPE_THRESHOLD and not confirm_large:
        raise PreviewError("Large catalogue previews require explicit confirmation.", category="confirmation_required", details=estimate)
    configuration = effective_configuration()
    if not configuration.complete:
        raise PreviewError("WooCommerce runtime credentials are not configured.", category="connection_required")
    health = build_woocommerce_workspace()["health"]
    if health["state"] not in {"connected", "connected_with_limitations"}:
        raise PreviewError("A successful read-only WooCommerce connection test is required.", category="connection_required")
    store = store_identity(configuration)
    woo_client = client or ReadOnlyWooClient(configuration)
    reader = PreviewWooReader(woo_client, (health.get("latest") or {}).get("selected_namespace") or "wc/v3")
    media_resolver = WordPressMediaResolver(woo_client)
    started = monotonic()
    lease = (
        acquire_catalogue_operation(
            OPERATION_TYPE,
            {
                "scope": _scope_summary(scope),
                "store_host": store["host"],
                "builder_version": BUILDER_VERSION,
            },
        )
        if record_operation
        else None
    )
    try:
        # Operation acquisition commits its history row. SQLAlchemy expires the
        # previously loaded graph on that commit, so reload the bounded scope
        # with the complete eager-loading profile before building the plan.
        # This keeps catalogue-sized previews from degrading into per-row
        # relationship queries while preserving the operation lock boundary.
        products = resolve_scope(scope)
        taxonomy = _taxonomy_plan(products, reader)
        # The current-store default is relevant only to a remote product whose
        # authored category set is intentionally empty.  Defer the bounded
        # setting read until that exact comparison is needed so ordinary and
        # large unlinked previews retain their established request budget.
        default_category_id = None
        product_ids = [row.id for row in products]
        identities = _product_identity_map(product_ids, store["key"])
        other_identities = {row.product_id: row for row in WooProductIdentity.query.filter(WooProductIdentity.product_id.in_(product_ids), WooProductIdentity.store_key != store["key"]).all()}
        variation_identities = _variation_identity_map(product_ids, store["key"])
        target_ids = {edge.resolved_target_product_id for product in products for edge in product.relationship_edges if edge.resolved_target_product_id}
        targets = {row.id: row for row in Product.query.filter(Product.id.in_(target_ids)).all()} if target_ids else {}
        target_identities = _product_identity_map(list(target_ids), store["key"]) if target_ids else {}
        local_state_digest = _digest(_local_state(products, identities, variation_identities))
        plans, selected = [], set(product_ids)
        for product in products:
            product_taxonomy = _product_taxonomy_plan(product, taxonomy)
            identity = _identity_resolution(product, store, reader, identity=identities.get(product.id), other=other_identities.get(product.id))
            media = _media_plan(product, media_resolver)
            payload, trace = _product_payload(product, taxonomy, media)
            blockers = [identity["blocker"]] if identity.get("blocker") else []
            warnings = []
            if payload.get("status") is None: blockers.append("Publishing intent is unresolved.")
            if payload.get("type") not in {"simple", "variable"}: blockers.append("The local product type cannot be mapped safely to WooCommerce.")
            if not payload.get("sku"): blockers.append("A valid SKU is required.")
            if product.catalogue_status != "active": warnings.append(f"Catalogue state is {product.catalogue_status}; it is not a Woo publication state.")
            if payload.get("status") == "draft": warnings.append("Publishing intent is Draft; the future Woo product will remain draft.")
            remote = identity.get("remote")
            if default_category_id is None and payload.get("categories") == [] and remote:
                default_category_id = reader.default_product_category_id()
                if default_category_id is None:
                    default_category_id = reader.infer_default_product_category_id(remote)
            known_attribute_ids = {
                item["name"]: int(item["woo_id"])
                for item in product_taxonomy["attributes"]
                if item.get("state") == "existing" and isinstance(item.get("woo_id"), int)
            }
            remote_managed, differences = _comparison(
                payload, remote, default_category_id=default_category_id,
                known_attribute_ids=known_attribute_ids,
            ) if remote else ({}, [])
            action = identity.get("action")
            if action is None: action = "update" if differences else "no_change"
            parent_action = action
            expected_type = payload.get("type")
            if remote and remote.get("type") and remote.get("type") != expected_type:
                blockers.append("Remote Woo product type conflicts with the planned local type.")
            relationships = _relationship_plan(product, selected, target_identities, targets)
            blockers.extend(relationships["blockers"])
            ambiguous_taxonomy = [item for values in product_taxonomy.values() for item in values if item["state"] == "ambiguous"]
            if ambiguous_taxonomy:
                blockers.append("One or more required taxonomy identities are ambiguous in WooCommerce.")
            if any(item["state"] == "create_required" for values in product_taxonomy.values() for item in values):
                warnings.append("One or more taxonomy dependencies require creation during controlled publishing.")
            if relationships["pending_count"]:
                warnings.append("Relationship targets included in this plan remain pending until Pass 1 produces Woo IDs.")
            unresolved_media = [
                image for image in media["parent"] + [image for row in media["variations"] for image in row["images"]]
                if image["state"] != "existing_wordpress_media"
            ]
            if unresolved_media:
                states = Counter(image["state"] for image in unresolved_media)
                blockers.append(
                    "Media identity required before publishing: "
                    + ", ".join(f"{count} {state.replace('_', ' ')}" for state, count in sorted(states.items()))
                    + "."
                )
            if not media["parent_count"] and not media["variation_count"]: warnings.append("No owned image is currently available; media remains a future publishing dependency.")
            variations = []
            remote_variations = []
            remote_parent_id = remote.get("id") if isinstance(remote, dict) else None
            if product.product_type == "variable" and remote_parent_id:
                try: remote_variations = reader.variations(remote_parent_id)
                except WooConnectionError as error: warnings.append(f"Variation comparison is unavailable: {error.message}")
            elif product.product_type == "variable":
                warnings.append("Variation publishing capability will be verified after the Woo parent product exists.")
            seen_skus = set()
            for variation in sorted(product.variations, key=lambda row: (row.menu_order, row.id)):
                variation_blockers = []
                if not variation.sku or variation.sku in seen_skus: variation_blockers.append("Variation SKU is missing or duplicated.")
                seen_skus.add(variation.sku)
                variation_media = next((row for row in media["variations"] if row["variation_id"] == variation.id), {"images": []})
                vp = _variation_payload(variation, variation_media)
                variation_gallery_ids = _variation_gallery_attachment_ids(variation_media)
                vid = variation_identities.get(variation.id)
                remote_variation = None
                variation_remote_managed = {}
                variation_differences = []
                variation_action = "pending_parent"
                if remote_parent_id:
                    if vid and vid.woo_variation_id:
                        candidates = [item for item in remote_variations if item.get("id") == vid.woo_variation_id]
                        if len(candidates) != 1 or (candidates[0].get("sku") and candidates[0].get("sku") != variation.sku):
                            variation_blockers.append("Stored Woo variation identity conflicts with the remote parent or SKU.")
                        else: remote_variation = candidates[0]
                    else:
                        candidates = [item for item in remote_variations if item.get("sku") == variation.sku]
                        if len(candidates) > 1: variation_blockers.append("Multiple remote variations use this exact SKU.")
                        elif len(candidates) == 1: remote_variation = candidates[0]; variation_action = "link_candidate"
                        else: variation_action = "create"
                    if remote_variation and variation_action != "link_candidate":
                        variation_remote_managed, variation_differences = _comparison(
                            vp, remote_variation, known_attribute_ids=known_attribute_ids,
                            variation_attributes=True,
                        )
                        variation_action = "update" if variation_differences else "no_change"
                    elif remote_variation:
                        variation_remote_managed, variation_differences = _comparison(
                            vp, remote_variation, known_attribute_ids=known_attribute_ids,
                            variation_attributes=True,
                        )
                if variation_blockers: variation_action = "blocked"
                if remote_variation:
                    if variation_gallery_ids and "gallery_image_ids" not in remote_variation:
                        variation_blockers.append(
                            "This WooCommerce variation endpoint does not expose the supported secondary gallery field."
                        )
                    elif "gallery_image_ids" in remote_variation:
                        remote_gallery = remote_variation.get("gallery_image_ids")
                        if remote_gallery != variation_gallery_ids:
                            variation_differences.append({
                                "field": "gallery_image_ids", "local": variation_gallery_ids,
                                "remote": remote_gallery, "planned": "update",
                            })
                            if variation_action == "no_change": variation_action = "update"
                if variation_blockers: variation_action = "blocked"
                variations.append({"id": variation.id, "stable_identity": variation.source_identity or f"variation:{variation.id}", "sku": variation.sku, "woo_id": remote_variation.get("id") if remote_variation else (vid.woo_variation_id if vid else None), "payload": vp, "gallery_image_ids": variation_gallery_ids, "remote_managed": variation_remote_managed, "differences": variation_differences, "action": variation_action, "blockers": variation_blockers})
            child_actions = {row["action"] for row in variations}
            if action == "no_change" and child_actions & {"create", "update"}:
                action = "update"
                warnings.append("One or more expected Woo child variations require publication or update.")
            elif action == "no_change" and child_actions & {"blocked", "link_candidate", "recovery_required"}:
                action = "recovery_required"
                warnings.append("One or more expected Woo child variations require identity recovery or review.")
            last = identity.get("identity")
            local_payload_digest = _digest(payload)
            remote_digest = _digest(remote_managed) if remote else None
            drift = "unknown"
            if last and last.last_published_digest:
                local_changed = last.last_published_digest != local_payload_digest
                remote_changed = bool(last.last_remote_digest and remote_digest and last.last_remote_digest != remote_digest)
                drift = "both_changed" if local_changed and remote_changed else "local_change" if local_changed else "remote_drift" if remote_changed else "unchanged"
                if drift == "both_changed": blockers.append("Local managed fields and remote managed fields both changed since the last successful sync.")
            if blockers and action not in {"recovery_required"}: action = "blocked"
            plans.append({
                "product_id": product.id, "stable_identity": product.source_relpath or f"product:{product.id}", "title": product.title, "sku": product.sku,
                "collection": product.collection.name if product.collection else "Unassigned", "local_type": product.product_type, "woo_type": payload.get("type"),
                "identity_state": identity["state"], "woo_id": remote.get("id") if remote else (last.woo_product_id if last else None), "action": action,
                "parent_action": parent_action,
                "remote_summary": {key: remote.get(key) for key in ("id", "name", "sku", "type", "status") if remote and remote.get(key) is not None},
                "trace": trace, "payload": payload, "payload_digest": local_payload_digest, "remote_managed": remote_managed, "remote_digest": remote_digest,
                "differences": differences, "drift": drift, "blockers": blockers, "warnings": warnings,
                "taxonomy": product_taxonomy,
                "media": media, "woo_default_category_id": default_category_id,
                "variations": variations, "relationships": relationships,
                "pass_1": ["Create parent product" if parent_action == "create" else "Review and link exact-SKU candidate" if parent_action == "link_candidate" else "Update managed product fields" if parent_action == "update" else "No parent mutation required"],
                "pass_2": ["Resolve ordered cross-sell and upsell Woo IDs"] if relationships["groups"]["cross_sell"] or relationships["groups"]["upsell"] else [],
                "pending_dependency": bool(relationships["pending_count"] or any(item["state"] == "create_required" for values in taxonomy.values() for item in values) or variations),
            })
        counts = Counter(item["action"] for item in plans)
        taxonomy_counts = Counter(item["state"] for values in taxonomy.values() for item in values)
        media_counts = Counter(image["state"] for item in plans for image in item["media"]["parent"] + [image for row in item["media"]["variations"] for image in row["images"]])
        relationship_counts = Counter(edge["state"] for item in plans for values in item["relationships"]["groups"].values() for edge in values)
        variation_counts = Counter(item["action"] for plan in plans for item in plan["variations"])
        warning_count = sum(len(item["warnings"]) for item in plans)
        blocker_count = sum(len(item["blockers"]) + sum(len(v["blockers"]) for v in item["variations"]) for item in plans)
        readiness = "blocked" if blocker_count else "identity_review_required" if counts["link_candidate"] or counts["recovery_required"] else "ready_with_warnings" if warning_count else "ready_for_controlled_publish"
        capability = {
            "state": health.get("state"),
            "selected_namespace": (health.get("latest") or {}).get("selected_namespace") or "wc/v3",
            "required_verified": int((health.get("latest") or {}).get("required_verified", 0) or 0),
            "required_total": int((health.get("latest") or {}).get("required_total", 0) or 0),
        }
        digest_state = {"scope": _scope_summary(scope), "local_state_digest": local_state_digest, "store_key": store["key"], "capability": capability, "products": plans, "taxonomy": taxonomy, "woo_default_category_id": default_category_id, "builder_version": BUILDER_VERSION, "mapping_version": MAPPING_VERSION}
        plan_digest = _digest(digest_state)
        summary = {
            "scope": _scope_summary(scope), "store_identity": store["key"], "store_host": store["host"], "generated_at": datetime.now(UTC).isoformat(),
            "product_counts": dict(counts), "taxonomy_counts": dict(taxonomy_counts), "media_counts": dict(media_counts), "variation_counts": dict(variation_counts), "relationship_counts": dict(relationship_counts),
            "warning_count": warning_count, "blocker_count": blocker_count, "readiness": readiness, "preview_digest": plan_digest,
            "local_state_digest": local_state_digest, "builder_version": BUILDER_VERSION, "mapping_version": MAPPING_VERSION,
            "woo_default_category_id": default_category_id,
            "request_count": woo_client.request_count, "estimated_request_count": estimate["estimated_woo_reads"], "duration_ms": max(0, int((monotonic() - started) * 1000)), "woo_writes": 0,
        }
        checklist = {
            "woo_connection_healthy": capability["state"] in {"connected", "connected_with_limitations"},
            "required_reads_verified": capability["required_total"] == 0 or capability["required_verified"] == capability["required_total"],
            "taxonomy_resolvable": not any(item["state"] == "ambiguous" for values in taxonomy.values() for item in values),
            "product_types_supported": all(item["woo_type"] in {"simple", "variable"} for item in plans),
            "variations_valid": not any(variation["blockers"] for item in plans for variation in item["variations"]),
            "media_ready_or_planned": not sum(count for state, count in media_counts.items() if state != "existing_wordpress_media"),
            "relationships_resolvable": not relationship_counts["broken"] and not relationship_counts["blocked"],
            "identity_conflicts_absent": not counts["blocked"] and not counts["recovery_required"],
            "blockers_absent": blocker_count == 0,
            "preview_digest_current": True,
        }
        summary["capability"] = capability
        summary["checklist"] = checklist
        plan = {"operation_id": lease.id if lease else None, "summary": summary, "capability": capability, "products": plans, "taxonomy": taxonomy, "digest": plan_digest}
        failed_products = counts["blocked"] + counts["recovery_required"]
        if lease:
            finish_catalogue_operation(lease.id, status="partial" if blocker_count or warning_count else "succeeded", products_attempted=len(products), products_succeeded=len(products) - failed_products, products_failed=failed_products, operation_summary=summary)
            cache_plan(plan)
            try: notify_woo_publish_preview_completed(summary, operation_id=lease.id)
            except Exception: current_app.logger.warning("Discord Woo preview notification failed safely")
        return plan
    except Exception as error:
        db.session.rollback()
        if lease:
            finish_catalogue_operation(lease.id, status="failed", products_attempted=len(products), products_failed=len(products), error=error, operation_summary={"scope": _scope_summary(scope), "store_host": store["host"], "failure_category": getattr(error, "category", "preview_failed"), "woo_writes": 0})
        raise


def regenerate_publish_plan(scope, *, client=None):
    """Rebuild the current plan without creating another preview operation."""

    return generate_publish_plan(
        scope,
        confirm_large=True,
        client=client,
        record_operation=False,
    )


def cache_plan(plan):
    _PLAN_CACHE[plan["operation_id"]] = plan
    _PLAN_CACHE.move_to_end(plan["operation_id"])
    while len(_PLAN_CACHE) > MAX_CACHE_PLANS:
        _PLAN_CACHE.popitem(last=False)


def cached_plan(operation_id):
    return _PLAN_CACHE.get(operation_id)


def _link_candidate_context(operation_id, preview_digest, product_id):
    operation = CatalogueOperation.query.filter_by(
        id=str(operation_id or "")[:32], operation_type=OPERATION_TYPE,
    ).first()
    if operation is None:
        raise LinkCandidateError("A valid Publish Preview is required before reviewing a Woo identity.", category="missing_preview")
    plan = cached_plan(operation.id)
    if plan is None:
        raise LinkCandidateError("Detailed Publish Preview is unavailable. Generate a fresh preview.", category="missing_preview")
    if str(preview_digest or "") != str(plan.get("digest") or ""):
        raise LinkCandidateError("Preview review data is stale. Generate a fresh preview.", category="stale_preview")
    if plan_is_stale(plan):
        raise LinkCandidateError("Preview review data is stale. Generate a fresh preview.", category="stale_preview")
    try:
        product_id = int(product_id)
    except (TypeError, ValueError) as error:
        raise LinkCandidateError("The local product identity is invalid.") from error
    item = next((row for row in plan.get("products", []) if row.get("product_id") == product_id), None)
    if not item or item.get("action") != "link_candidate":
        raise LinkCandidateError("This product is not an eligible Link Candidate in the current Preview.", category="not_link_candidate")
    candidate_id = item.get("woo_id")
    if not isinstance(candidate_id, int) or candidate_id <= 0:
        raise LinkCandidateError("The reviewed Woo product identity is invalid.", category="invalid_candidate")
    product = db.session.get(Product, product_id)
    if product is None or (product.source_relpath or f"product:{product.id}") != item.get("stable_identity"):
        raise LinkCandidateError("The local product identity changed. Generate a fresh preview.", category="stale_preview")
    if product.sku != item.get("sku"):
        raise LinkCandidateError("The local SKU changed. Generate a fresh preview.", category="stale_preview")
    return operation, plan, item, product, candidate_id


def _unlink_context(operation_id, preview_digest, product_id):
    operation = CatalogueOperation.query.filter_by(
        id=str(operation_id or "")[:32], operation_type=OPERATION_TYPE,
    ).first()
    if operation is None:
        raise WooUnlinkError("A valid Publish Preview is required before unlinking a Woo identity.", category="missing_preview")
    plan = cached_plan(operation.id)
    if plan is None:
        raise WooUnlinkError("Detailed Publish Preview is unavailable. Generate a fresh preview.", category="missing_preview")
    if str(preview_digest or "") != str(plan.get("digest") or "") or plan_is_stale(plan):
        raise WooUnlinkError("Preview review data is stale. Generate a fresh preview.", category="stale_preview")
    try:
        product_id = int(product_id)
    except (TypeError, ValueError) as error:
        raise WooUnlinkError("The local product identity is invalid.") from error
    item = next((row for row in plan.get("products", []) if row.get("product_id") == product_id), None)
    product = db.session.get(Product, product_id)
    if not item or product is None:
        raise WooUnlinkError("The local product is no longer part of this Preview.", category="stale_preview")
    stable_identity = product.source_relpath or f"product:{product.id}"
    if stable_identity != item.get("stable_identity") or product.sku != item.get("sku"):
        raise WooUnlinkError("The local product identity changed. Generate a fresh preview.", category="stale_preview")
    configuration = effective_configuration()
    if not configuration.complete:
        raise WooUnlinkError("WooCommerce runtime credentials are not configured.", category="connection_required")
    store = store_identity(configuration)
    if store["key"] != plan.get("summary", {}).get("store_identity"):
        raise WooUnlinkError("The configured Woo store changed. Generate a fresh preview.", category="store_changed")
    identity = WooProductIdentity.query.filter_by(store_key=store["key"], product_id=product.id).one_or_none()
    if (
        identity is None or not identity.woo_product_id
        or identity.stable_identity != stable_identity or identity.sku != product.sku
        or identity.store_host != store["host"]
    ):
        raise WooUnlinkError("The reviewed trusted Woo identity changed. Generate a fresh preview.", category="stale_preview")
    if item.get("woo_id") != identity.woo_product_id:
        raise WooUnlinkError("The reviewed Woo identity changed. Generate a fresh preview.", category="stale_preview")
    return operation, plan, item, product, store, identity


def prepare_woo_unlink_review(operation_id, preview_digest, product_id):
    """Return a local-only identity removal review; it never reads Woo."""

    _operation, plan, item, product, store, identity = _unlink_context(
        operation_id, preview_digest, product_id,
    )
    variation_count = WooVariationIdentity.query.filter_by(
        store_key=store["key"], product_id=product.id,
    ).count()
    return {
        "operation_id": plan["operation_id"], "preview_digest": plan["digest"],
        "store_host": store["host"], "store_identity": store["key"],
        "product": {"id": product.id, "title": product.title, "sku": product.sku, "local_type": item.get("local_type")},
        "woo_id": identity.woo_product_id,
        "variation_identity_count": variation_count,
    }


def unlink_woo_identity(operation_id, preview_digest, product_id, reviewed_woo_id):
    """Forget one current-store trusted identity without contacting Woo."""

    _operation, _plan, _item, product, store, identity = _unlink_context(
        operation_id, preview_digest, product_id,
    )
    try:
        reviewed_woo_id = int(reviewed_woo_id)
    except (TypeError, ValueError) as error:
        raise WooUnlinkError("The reviewed Woo identity is invalid.", category="invalid_identity") from error
    if reviewed_woo_id != identity.woo_product_id:
        raise WooUnlinkError("The reviewed Woo identity changed. Generate a fresh preview.", category="stale_preview")
    WooVariationIdentity.query.filter_by(store_key=store["key"], product_id=product.id).delete(
        synchronize_session=False,
    )
    db.session.delete(identity)
    db.session.commit()
    return {"product_id": product.id, "woo_id": reviewed_woo_id, "variation_identities_removed": product.product_type == "variable"}


def prepare_link_candidate_review(operation_id, preview_digest, product_id):
    """Return a current, read-only candidate review contract for the UI."""

    _operation, plan, item, product, candidate_id = _link_candidate_context(
        operation_id, preview_digest, product_id,
    )
    return {
        "operation_id": plan["operation_id"],
        "preview_digest": plan["digest"],
        "store_identity": plan["summary"]["store_identity"],
        "store_host": plan["summary"]["store_host"],
        "product": {
            "id": product.id, "title": product.title, "sku": product.sku,
            "local_type": item.get("local_type"), "stable_identity": item.get("stable_identity"),
        },
        "candidate": {
            "woo_id": candidate_id, "title": (item.get("remote_summary") or {}).get("name"),
            "sku": (item.get("remote_summary") or {}).get("sku"),
            "woo_type": (item.get("remote_summary") or {}).get("type"),
            "status": (item.get("remote_summary") or {}).get("status"),
        },
        "differences": item.get("differences") or [],
        "blockers": item.get("blockers") or [],
    }


def link_candidate_identity(operation_id, preview_digest, product_id, candidate_id, *, client=None):
    """Explicitly adopt one revalidated, exact-SKU Woo parent identity.

    This is deliberately read-only against WooCommerce.  It verifies the
    reviewed candidate again before persisting the existing local, store-scoped
    identity record; it never creates or links variation identities.
    """

    _operation, plan, item, product, planned_candidate_id = _link_candidate_context(
        operation_id, preview_digest, product_id,
    )
    try:
        candidate_id = int(candidate_id)
    except (TypeError, ValueError) as error:
        raise LinkCandidateError("The reviewed Woo product identity is invalid.", category="invalid_candidate") from error
    if candidate_id != planned_candidate_id:
        raise LinkCandidateError("The reviewed Woo product changed. Generate a fresh preview.", category="stale_preview")

    configuration = effective_configuration()
    if not configuration.complete:
        raise LinkCandidateError("WooCommerce runtime credentials are not configured.", category="connection_required")
    health = build_woocommerce_workspace()["health"]
    if health.get("state") not in {"connected", "connected_with_limitations"}:
        raise LinkCandidateError("A healthy WooCommerce connection test is required.", category="connection_required")
    store = store_identity(configuration)
    if store["key"] != plan["summary"].get("store_identity"):
        raise LinkCandidateError("The configured Woo store changed. Generate a fresh preview.", category="store_changed")

    reader = PreviewWooReader(
        client or ReadOnlyWooClient(configuration),
        (plan.get("capability") or {}).get("selected_namespace") or "wc/v3",
    )
    try:
        remote = reader.product_by_id(candidate_id)
    except WooConnectionError as error:
        if error.category == "not_found":
            raise LinkCandidateError("The reviewed Woo product no longer exists. Generate a fresh preview.", category="remote_missing") from error
        raise LinkCandidateError("The reviewed Woo product could not be revalidated. Generate a fresh preview.", category="revalidation_failed") from error
    if not isinstance(remote, dict) or int(remote.get("id") or 0) != candidate_id:
        raise LinkCandidateError("The reviewed Woo product no longer exists. Generate a fresh preview.", category="remote_missing")
    if remote.get("sku") != product.sku:
        raise LinkCandidateError("The reviewed Woo product SKU changed. Generate a fresh preview.", category="sku_changed")
    expected_type = "variable" if product.product_type == "variable" else "simple"
    if remote.get("type") != expected_type:
        raise LinkCandidateError("The reviewed Woo product type changed. Generate a fresh preview.", category="type_changed")
    try:
        matches = [
            row for row in reader.products_by_sku(product.sku)
            if isinstance(row, dict) and row.get("sku") == product.sku
        ]
    except WooConnectionError as error:
        raise LinkCandidateError("The exact Woo SKU could not be revalidated. Generate a fresh preview.", category="revalidation_failed") from error
    if len(matches) != 1 or int(matches[0].get("id") or 0) != candidate_id:
        raise LinkCandidateError("The exact Woo SKU is ambiguous or changed. Generate a fresh preview.", category="ambiguous_sku")

    existing = WooProductIdentity.query.filter_by(store_key=store["key"], product_id=product.id).one_or_none()
    if existing and existing.woo_product_id and existing.woo_product_id != candidate_id:
        raise LinkCandidateError("A conflicting trusted Woo identity already exists for this local product.", category="identity_conflict")
    remote_owner = WooProductIdentity.query.filter_by(store_key=store["key"], woo_product_id=candidate_id).one_or_none()
    if remote_owner and remote_owner.product_id != product.id:
        raise LinkCandidateError("The reviewed Woo product is already linked to another local product.", category="identity_conflict")
    if existing and existing.verification_state == "verified" and existing.woo_product_id == candidate_id:
        raise LinkCandidateError("This Woo identity is already linked. Generate a fresh preview.", category="already_linked")

    row = existing or WooProductIdentity(
        product_id=product.id, stable_identity=item["stable_identity"], sku=product.sku,
        store_key=store["key"], store_host=store["host"],
    )
    if existing is None:
        db.session.add(row)
    row.woo_product_id = candidate_id
    row.stable_identity = item["stable_identity"]
    row.sku = product.sku
    row.store_host = store["host"]
    row.sync_state = "linked"
    row.verification_state = "verified"
    row.last_successful_sync_at = datetime.now(UTC).replace(tzinfo=None)
    row.last_remote_digest = _digest(_normalise_remote(remote, item["payload"].keys()))
    # Linking proves identity; it must not claim this operation published data.
    row.last_published_digest = None
    db.session.commit()
    return row


def operation_summary(operation):
    try: return json.loads(operation.scope or "{}").get("operation_summary", {})
    except (TypeError, ValueError): return {}


def plan_is_stale(plan):
    products = resolve_scope(plan["summary"]["scope"])
    store_key = plan["summary"]["store_identity"]
    identities = _product_identity_map([row.id for row in products], store_key)
    variations = _variation_identity_map([row.id for row in products], store_key)
    return _digest(_local_state(products, identities, variations)) != plan["summary"]["local_state_digest"]


def reset_preview_cache_for_tests():
    _PLAN_CACHE.clear()
