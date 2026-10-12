"""Read-only Shopify transport and bounded discovery; no mutation entry point."""
from dataclasses import dataclass, field
import json
import re
from time import monotonic
from urllib.parse import urlsplit, unquote

import requests

QUERY = "query Connection { shop { id myshopifyDomain } currentAppInstallation { accessScopes { handle } } }"
VERSIONS = ("2026-10", "2026-07", "2026-04", "2026-01")

PAGE = "pageInfo { hasNextPage endCursor }"
PRODUCT_FIELDS = "id title handle status description descriptionHtml options { id name position values }"
MEDIA_FIELDS = """id alt mediaContentType status
  ... on MediaImage { mimeType fileStatus originalSource { url }
    image { id url width height altText } }"""
VARIANT_FIELDS = """id title sku price compareAtPrice position selectedOptions { name value }
  media(first: 5) { nodes { id mediaContentType status } pageInfo { hasNextPage endCursor } }"""
COLLECTION_FIELDS = "id title handle description sortOrder ruleSet { appliedDisjunctively }"
PRODUCTS_QUERY = "query Products($after: String) { products(first: 20, after: $after, sortKey: ID) { nodes { " + PRODUCT_FIELDS + " } " + PAGE + " } }"
PRODUCT_QUERY = """query Product($id: ID!, $variants: String, $media: String) {
  product(id: $id) { """ + PRODUCT_FIELDS + """
    variants(first: 20, after: $variants) { nodes { """ + VARIANT_FIELDS + " } " + PAGE + """ }
    media(first: 20, after: $media) { edges { cursor node { """ + MEDIA_FIELDS + " } } " + PAGE + " } } }"
VARIANT_QUERY = "query Variant($id: ID!, $after: String) { productVariant(id: $id) { id title sku media(first: 20, after: $after) { edges { cursor node { " + MEDIA_FIELDS + " } } " + PAGE + " } } }"
COLLECTIONS_QUERY = "query Collections($after: String) { collections(first: 20, after: $after, sortKey: ID) { nodes { " + COLLECTION_FIELDS + " } " + PAGE + " } }"
COLLECTION_QUERY = "query Collection($id: ID!, $after: String) { collection(id: $id) { " + COLLECTION_FIELDS + " products(first: 20, after: $after, sortKey: ID) { nodes { id title handle status } " + PAGE + " } } }"
READ_QUERIES = frozenset((QUERY, PRODUCTS_QUERY, PRODUCT_QUERY, VARIANT_QUERY, COLLECTIONS_QUERY, COLLECTION_QUERY))


def _cursor(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise ShopifyError("Invalid pagination cursor. Restart discovery from its first page.")
    return value


def _gid(value, kind):
    if not isinstance(value, str) or not re.fullmatch(r"gid://shopify/" + kind + r"/\d{1,30}", value):
        raise ShopifyError("Invalid Shopify resource ID. Retrieve the list again.")
    return value


def _connection(value, cursor=None, *, edges=False):
    """Do not mistake incomplete discovery for an empty store, or loop a cursor."""
    if not isinstance(value, dict):
        raise ShopifyError("Incomplete Shopify page. Retry discovery.")
    rows = value.get("edges" if edges else "nodes")
    info = value.get("pageInfo", {})
    if (not isinstance(rows, list) or len(rows) > 20 or any(not isinstance(r, dict) for r in rows)
            or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool)):
        raise ShopifyError("Incomplete Shopify pagination evidence. Retry discovery.")
    end = _cursor(info.get("endCursor"))
    if info["hasNextPage"] and (not rows or not end or end == cursor):
        raise ShopifyError("Shopify pagination did not advance. Restart discovery.")
    return value


def _media(connection, cursor=None):
    _connection(connection, cursor, edges=True)
    for ordinal, edge in enumerate(connection["edges"], 1):
        media = edge.get("node")
        if not isinstance(media, dict) or not media.get("id"):
            raise ShopifyError("Incomplete Shopify media evidence. Retry discovery.")
        image = media.get("image") or {}
        original = media.get("originalSource") or {}
        if not isinstance(image, dict) or not isinstance(original, dict):
            raise ShopifyError("Incomplete Shopify image evidence. Retry discovery.")
        # URL basename is only a hint, not proof of the uploaded/local filename.
        url = original.get("url") or image.get("url") or ""
        if not isinstance(url, str):
            raise ShopifyError("Invalid Shopify image URL evidence. Retry discovery.")
        media["filename_hint"] = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) if url else None
        media["page_order"] = ordinal
        media["order_cursor"] = edge.get("cursor")
        media["local_path"] = None
        media["local_filename"] = None
    return connection


class ShopifyError(ValueError):
    pass


@dataclass(frozen=True)
class ShopifyConfiguration:
    domain: str
    token: str = field(repr=False)
    api_version: str = "2026-10"

    def validated(self):
        if not isinstance(self.domain, str):
            raise ShopifyError("Enter your shop-name.myshopify.com domain.")
        domain = self.domain.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com", domain):
            raise ShopifyError("Enter only your shop-name.myshopify.com domain, without a URL, path or port.")
        if not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", self.token):
            raise ShopifyError("Enter a valid Admin API access token, not a storefront token or client secret.")
        if self.api_version not in VERSIONS:
            raise ShopifyError("Select a supported pinned Shopify API version.")
        return ShopifyConfiguration(domain, self.token, self.api_version)


class ShopifyAdapter:
    platform = "shopify"
    can_publish = False

    def _read(self, configuration, query, variables=None, *, session=None):
        if query not in READ_QUERIES:
            raise ShopifyError("Only fixed read-only Shopify queries are supported.")
        config = configuration.validated()
        own = session is None
        session = session or requests.Session()
        if own:
            session.trust_env = False  # Never send the token through an inherited proxy/netrc.
        try:
            with session.post(
                f"https://{config.domain}/admin/api/{config.api_version}/graphql.json",
                headers={"X-Shopify-Access-Token": config.token, "Accept": "application/json"},
                json={"query": query, **({"variables": variables} if variables is not None else {})},
                timeout=(5, 15), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code in (401, 403):
                    raise ShopifyError("Shopify rejected authentication or access. Check the installed app, token expiry and scopes.")
                if response.status_code == 429:
                    raise ShopifyError("Shopify is rate limiting reads. Wait before retrying; no automatic retry or store write occurred.")
                if response.status_code != 200:
                    raise ShopifyError("Shopify connection failed. Check the domain and service availability; no store data was changed.")
                if response.headers.get("X-Shopify-API-Version", config.api_version) != config.api_version:
                    raise ShopifyError("Shopify served a different API version. Select a supported version and test again.")
                raw = bytearray()
                started = monotonic()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw) > (65536 if query == QUERY else 1048576) or monotonic() - started > 20:
                        raise ShopifyError("Shopify returned an oversized or incomplete response. Try again later.")
                data = json.loads(raw)
                if data.get("errors"):
                    errors = data["errors"]
                    if isinstance(errors, list) and any(isinstance(e, dict) and
                            (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                        raise ShopifyError("Shopify query budget is exhausted. Wait before retrying this read-only page.")
                    raise ShopifyError("Shopify could not complete the read query. Check token permissions or rate limits.")
                if not isinstance(data["data"], dict):
                    raise ShopifyError("Shopify returned incomplete read evidence. Retry later.")
                return data["data"]
        except requests.Timeout:
            raise ShopifyError("Shopify timed out. Retry the read-only connection test later.") from None
        except requests.RequestException:
            raise ShopifyError("Unable to reach Shopify securely. Check connectivity and the store domain.") from None
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError):
            raise ShopifyError("Shopify returned incomplete or invalid connection evidence. Nothing was saved.") from None
        finally:
            if own:
                session.close()

    def test_connection(self, configuration, *, session=None):
        config = configuration.validated()
        data = self._read(config, QUERY, session=session)
        try:
            shop = data["shop"]
            scopes = data["currentAppInstallation"]["accessScopes"]
            if (shop["myshopifyDomain"].lower() != config.domain
                    or not re.fullmatch(r"gid://shopify/Shop/\d+", shop["id"])):
                raise ShopifyError("Shop identity did not match the configured domain. Nothing was saved.")
            handles = sorted({s["handle"] for s in scopes if re.fullmatch(r"[a-z_]{1,80}", s["handle"])})
            return {"shop_id": shop["id"], "domain": config.domain, "api_version": config.api_version,
                    "scopes": handles, "products_readable": bool({"read_products", "write_products"} & set(handles))}
        except (KeyError, TypeError, AttributeError):
            raise ShopifyError("Shopify returned incomplete or invalid connection evidence. Nothing was saved.") from None

    def discover_products(self, configuration, after=None, *, session=None):
        after = _cursor(after)
        return _connection(self._read(configuration, PRODUCTS_QUERY, {"after": after}, session=session).get("products"), after)

    def inspect_product(self, configuration, product_id, variants=None, media=None, *, session=None):
        variables = {"id": _gid(product_id, "Product"), "variants": _cursor(variants), "media": _cursor(media)}
        product = self._read(configuration, PRODUCT_QUERY, variables, session=session).get("product")
        if not isinstance(product, dict) or product.get("id") != product_id:
            raise ShopifyError("Shopify product is missing or unavailable. Retrieve products again.")
        _connection(product.get("variants"), variables["variants"])
        for variant in product["variants"]["nodes"]:
            _connection(variant.get("media"))
        _media(product.get("media"), variables["media"])
        return product

    def inspect_variant(self, configuration, variant_id, after=None, *, session=None):
        variables = {"id": _gid(variant_id, "ProductVariant"), "after": _cursor(after)}
        variant = self._read(configuration, VARIANT_QUERY, variables, session=session).get("productVariant")
        if not isinstance(variant, dict) or variant.get("id") != variant_id:
            raise ShopifyError("Shopify variant is missing or unavailable. Retrieve products again.")
        _media(variant.get("media"), variables["after"])
        return variant

    def discover_collections(self, configuration, after=None, *, session=None):
        after = _cursor(after)
        return _connection(self._read(configuration, COLLECTIONS_QUERY, {"after": after}, session=session).get("collections"), after)

    def inspect_collection(self, configuration, collection_id, after=None, *, session=None):
        variables = {"id": _gid(collection_id, "Collection"), "after": _cursor(after)}
        collection = self._read(configuration, COLLECTION_QUERY, variables, session=session).get("collection")
        if not isinstance(collection, dict) or collection.get("id") != collection_id:
            raise ShopifyError("Shopify collection is missing or unavailable. Retrieve collections again.")
        _connection(collection.get("products"), variables["after"])
        return collection
