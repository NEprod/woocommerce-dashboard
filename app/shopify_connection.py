"""One bounded transport for fixed reads and separately reviewed Shopify mutations."""
from dataclasses import dataclass, field
import json
import re
import hashlib
import threading
from collections import OrderedDict
from time import monotonic, sleep
from urllib.parse import urlsplit, unquote

import requests

QUERY = "query Connection { shop { id myshopifyDomain primaryDomain { host } domains { host } } currentAppInstallation { accessScopes { handle } } }"
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
STAGE_QUERY = "mutation Stage($input: [StagedUploadInput!]!) { stagedUploadsCreate(input: $input) { stagedTargets { url resourceUrl parameters { name value } } userErrors { field message } } }"
CREATE_QUERY = """mutation Draft($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
 productCreate(product: $product, media: $media) { product { id variants(first: 2) { nodes { id } }
 media(first: 21) { nodes { id alt } pageInfo { hasNextPage endCursor } } }
 userErrors { field message } } }"""
PRICE_QUERY = """mutation Price($id: ID!, $variants: [ProductVariantsBulkInput!]!) {
 productVariantsBulkUpdate(productId: $id, variants: $variants) { productVariants { id sku price }
 userErrors { field message } } }"""
DRAFT_FIELDS = """id handle title descriptionHtml status updatedAt
 metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 variants(first: 2) { nodes { id sku price } pageInfo { hasNextPage endCursor } }
 media(first: 21) { nodes { id alt status mediaContentType ... on MediaImage { image { url } } }
 pageInfo { hasNextPage endCursor } }"""
DRAFT_QUERY = "query DraftRead($id: ID!) { product(id: $id) { " + DRAFT_FIELDS + " } }"
FIND_DRAFT_QUERY = "query DraftFind($query: String!) { products(first: 2, query: $query) { nodes { " + DRAFT_FIELDS + " } pageInfo { hasNextPage endCursor } } }"
READ_QUERIES = READ_QUERIES | frozenset((DRAFT_QUERY, FIND_DRAFT_QUERY))
WRITE_QUERIES = frozenset((STAGE_QUERY, CREATE_QUERY, PRICE_QUERY))

# Full Simple managed readback, separate from the legacy M3A recovery contract.
SIMPLE_QUERY = """query SimpleRead($id: ID!, $namespace: String!) {
 product(id: $id) { """ + DRAFT_FIELDS + """
 vendor tags productType category { id fullName } seo { title description }
 length: metafield(namespace: $namespace, key: "product_length") { id type value }
 width: metafield(namespace: $namespace, key: "product_width") { id type value }
 height: metafield(namespace: $namespace, key: "product_height") { id type value }
 simpleVariants: variants(first: 2) { nodes { id sku price compareAtPrice inventoryPolicy
 inventoryItem { id sku requiresShipping countryCodeOfOrigin tracked
 measurement { weight { value unit } } } } pageInfo { hasNextPage endCursor } }
 } }"""
COUNTRIES_QUERY = 'query SupportedCountries { __type(name: "CountryCode") { enumValues { name } } }'
SKU_QUERY = 'query ExistingSku($query: String!) { productVariants(first: 2, query: $query) { nodes { id sku product { id } } pageInfo { hasNextPage endCursor } } }'
UPDATE_QUERY = """mutation SimpleUpdate($product: ProductUpdateInput!) {
 productUpdate(product: $product) { product { id handle } userErrors { field message } } }"""
FILES_QUERY = """mutation SimpleAlt($files: [FileUpdateInput!]!) {
 fileUpdate(files: $files) { files { id alt } userErrors { field message } } }"""
ORDER_QUERY = """mutation SimpleOrder($id: ID!, $moves: [MoveInput!]!) {
 productReorderMedia(id: $id, moves: $moves) { job { id } mediaUserErrors { field message } } }"""
READ_QUERIES = READ_QUERIES | frozenset((SIMPLE_QUERY, COUNTRIES_QUERY, SKU_QUERY))
REVISIONS_QUERY = '''query TLCRevisions($ids: [ID!]!) { nodes(ids: $ids) { id ... on Product { updatedAt status } } }'''
READ_QUERIES = READ_QUERIES | frozenset((REVISIONS_QUERY,))
PUBLICATIONS_QUERY = '''query TLCChannels($after: String) { publications(first: 20, after: $after, catalogType: APP) {
 nodes { id autoPublish channels(first: 20) { nodes { id name handle } pageInfo { hasNextPage } } }
 pageInfo { hasNextPage endCursor } } }'''
PUBLICATION_STATE = '''query TLCPublicationState($id: ID!) { product(id: $id) { id status
 metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 resourcePublicationsV2(first: 100, onlyPublished: false) { nodes { isPublished publishDate publication { id } } pageInfo { hasNextPage } }
} }'''
PUBLICATION_PUBLISH = '''mutation TLCChannelPublish($id: ID!, $input: [PublicationInput!]!) {
 publishablePublish(id: $id, input: $input) { publishable { __typename } userErrors { field message } } }'''
PUBLICATION_UNPUBLISH = '''mutation TLCChannelUnpublish($id: ID!, $input: [PublicationInput!]!) {
 publishableUnpublish(id: $id, input: $input) { publishable { __typename } userErrors { field message } } }'''
READ_QUERIES |= frozenset((PUBLICATIONS_QUERY, PUBLICATION_STATE))
WRITE_QUERIES |= frozenset((PUBLICATION_PUBLISH, PUBLICATION_UNPUBLISH))
COLLECTION_LOOKUP = '''query CollectionCandidates($query: String!) { collections(first: 20, query: $query) { nodes { id title handle ruleSet { appliedDisjunctively } metafield(namespace: "tlc_dashboard", key: "collection_attempt") { value } } pageInfo { hasNextPage endCursor } } }'''
COLLECTION_STATE = '''query CollectionState($id: ID!, $product: ID!, $check: Boolean!) { collection(id: $id) { id title handle ruleSet { appliedDisjunctively } sources { __typename ... on CollectionConditionsSource { targetType shareable inclusion { conditions { __typename } } exclusion { conditions { __typename } selections(first: 1) { nodes { product { id } } } } } } hasProduct(id: $product) @include(if: $check) metafield(namespace: "tlc_dashboard", key: "collection_attempt") { value } } }'''
COLLECTION_CREATE = '''mutation TLCCollection($input: CollectionInput!) { collectionCreate(input: $input) { collection { id } userErrors { field message } } }'''
COLLECTION_JOIN = '''mutation TLCMembership($id: ID!, $products: [ID!]!) { collectionAddProducts(id: $id, productIds: $products) { collection { id } userErrors { field message } } }'''
STANDARD_CATEGORY = '''query StandardCategory($id: ID!) { node(id: $id) { ... on TaxonomyCategory { id fullName isArchived } } }'''
READ_QUERIES = READ_QUERIES | frozenset((COLLECTION_LOOKUP, COLLECTION_STATE, STANDARD_CATEGORY))
WRITE_QUERIES = WRITE_QUERIES | frozenset((UPDATE_QUERY, FILES_QUERY, ORDER_QUERY, COLLECTION_CREATE, COLLECTION_JOIN))
NATIVE_CAPABILITY = '''query PackedCapability {
 measurement: __type(name: "InventoryItemMeasurement") { fields { name } }
 measurementInput: __type(name: "InventoryItemMeasurementInput") { inputFields { name } }
 dimensionsInput: __type(name: "ObjectDimensionsInput") { inputFields { name } }
 units: __type(name: "LengthUnit") { enumValues { name } }
}'''
NATIVE_READ = '''query PackedRead($id: ID!) { product(id: $id) {
 id status metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 variants(first: 2) { nodes { id sku inventoryItem { id measurement {
 packedDimensions { length width height unit } } } } pageInfo { hasNextPage } }
} }'''
NATIVE_WRITE = '''mutation PackedUpdate($id: ID!, $input: InventoryItemInput!) {
 inventoryItemUpdate(id: $id, input: $input) { inventoryItem { id } userErrors { field message } }
}'''
NATIVE_QUERIES = frozenset((NATIVE_CAPABILITY, NATIVE_READ, NATIVE_WRITE))
READ_QUERIES = READ_QUERIES | frozenset((NATIVE_CAPABILITY, NATIVE_READ))
WRITE_QUERIES = WRITE_QUERIES | frozenset((NATIVE_WRITE,))
VARIABLE_FIELDS = '''id title handle status updatedAt descriptionHtml vendor tags productType category { id fullName } seo { title description }
 metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 options { id name position optionValues { id name } }
 length: metafield(namespace: $namespace, key: "product_length") { id type value }
 width: metafield(namespace: $namespace, key: "product_width") { id type value }
 height: metafield(namespace: $namespace, key: "product_height") { id type value }
 media(first: 21) { nodes { id alt status mediaContentType ... on MediaImage { image { url } } } pageInfo { hasNextPage } }
 variants(first: 33) { nodes { id sku price compareAtPrice inventoryPolicy selectedOptions { name value }
 media(first: 2) { nodes { id } pageInfo { hasNextPage } }
 length: metafield(namespace: $namespace, key: "product_length") { id type value }
 width: metafield(namespace: $namespace, key: "product_width") { id type value }
 height: metafield(namespace: $namespace, key: "product_height") { id type value }
 inventoryItem { id sku countryCodeOfOrigin requiresShipping harmonizedSystemCode tracked measurement { weight { value unit } PACKED_FIELD } }
 } pageInfo { hasNextPage } }'''
VARIABLE_READ = 'query VariableRead($id: ID!, $namespace: String!, $after: String) { product(id: $id) { ' + VARIABLE_FIELDS.replace('PACKED_FIELD', '').replace('variants(first: 33)', 'variants(first: 16, after: $after)').replace('} pageInfo { hasNextPage } }', '} pageInfo { hasNextPage endCursor } }') + ' } }'
VARIABLE_NATIVE_READ = '''query VariablePackedRead($id: ID!, $after: String) { product(id: $id) {
 id status updatedAt metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 variants(first: 16, after: $after) { nodes { id sku selectedOptions { name value }
 inventoryItem { id sku measurement { packedDimensions { length width height unit } } }
 } pageInfo { hasNextPage endCursor } }
} }'''
NATIVE_INVENTORY_READ = '''query PackedInventory($id: ID!) { inventoryItem(id: $id) { id sku measurement { packedDimensions { length width height unit } } } }'''
VARIABLE_CREATE = '''mutation VariableCreate($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
 productCreate(product: $product, media: $media) { product { id media(first: 21) { nodes { id alt } pageInfo { hasNextPage } } } userErrors { field message } }
}'''
VARIANTS_CREATE = '''mutation VariantsCreate($id: ID!, $variants: [ProductVariantsBulkInput!]!) {
 productVariantsBulkCreate(productId: $id, variants: $variants, strategy: PRESERVE_STANDALONE_VARIANT) {
 productVariants { id sku selectedOptions { name value } inventoryItem { id } } userErrors { field message }
} }'''
VARIANTS_UPDATE = '''mutation VariantsUpdate($id: ID!, $variants: [ProductVariantsBulkInput!]!) {
 productVariantsBulkUpdate(productId: $id, variants: $variants, allowPartialUpdates: false) {
 productVariants { id sku selectedOptions { name value } inventoryItem { id } } userErrors { field message }
} }'''
READ_QUERIES |= frozenset((VARIABLE_READ, VARIABLE_NATIVE_READ, NATIVE_INVENTORY_READ))
WRITE_QUERIES |= frozenset((VARIABLE_CREATE, VARIANTS_CREATE, VARIANTS_UPDATE))
NATIVE_QUERIES |= frozenset((VARIABLE_NATIVE_READ, NATIVE_INVENTORY_READ))

LIFECYCLE_READ = '''query Lifecycle($id: ID!) { product(id: $id) { id title status updatedAt
 metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 variants(first: 33) { nodes { id sku inventoryItem { id sku } } pageInfo { hasNextPage } }
 media(first: 21) { nodes { id } pageInfo { hasNextPage } }
} }'''
PRODUCT_DELETE = '''mutation DeleteReviewedProduct($id: ID!) {
 productDelete(input: {id: $id}, synchronous: true) { deletedProductId userErrors { field message } }
}'''
READ_QUERIES |= frozenset((LIFECYCLE_READ,))
WRITE_QUERIES |= frozenset((PRODUCT_DELETE,))

ATTRIBUTE_SCHEMA = '''query AttributeSchema {
 definition: __type(name: "MetaobjectDefinitionCreateInput") { inputFields { name } }
 term: __type(name: "MetaobjectCreateInput") { inputFields { name } }
 assignment: __type(name: "MetafieldsSetInput") { inputFields { name } }
 fieldCreate: __type(name: "MetafieldDefinitionInput") { inputFields { name } }
 fieldUpdate: __type(name: "MetafieldDefinitionUpdateInput") { inputFields { name } }
 capabilityCreate: __type(name: "MetafieldCapabilityCreateInput") { inputFields { name } }
 capabilityUpdate: __type(name: "MetafieldCapabilityUpdateInput") { inputFields { name } }
 capabilityOutput: __type(name: "MetafieldCapabilities") { fields { name } }
 adminCapability: __type(name: "MetafieldCapabilityAdminFilterable") { fields { name } }
 collectionCapability: __type(name: "MetafieldCapabilitySmartCollectionCondition") { fields { name } }
 adminCapabilityInput: __type(name: "MetafieldCapabilityAdminFilterableInput") { inputFields { name } }
 collectionCapabilityInput: __type(name: "MetafieldCapabilitySmartCollectionConditionInput") { inputFields { name } }
}'''
ATTRIBUTE_DEFINITION = '''query AttributeDefinition($type: String!) { metaobjectDefinitionByType(type: $type) {
 id name type fieldDefinitions { key type { name } } access { storefront } } }'''
ATTRIBUTE_TERM = '''query AttributeTerm($type: String!, $handle: String!) { metaobjectByHandle(handle: {type: $type, handle: $handle}) {
 id type handle fields { key value } definition { id type access { storefront } } capabilities { publishable { status } } } }'''
ATTRIBUTE_FIELD = '''query AttributeField($namespace: String!, $key: String!) {
 metafieldDefinitions(first: 2, ownerType: PRODUCT, namespace: $namespace, key: $key) {
 nodes { id namespace key ownerType type { name } validations { name value } access { storefront } } pageInfo { hasNextPage } } }'''
ATTRIBUTE_FIELD_CAPABILITIES = {}
for _keys in (("adminFilterable",), ("smartCollectionCondition",), ("adminFilterable", "smartCollectionCondition")):
    _selection = " ".join(key + " { eligible enabled" + (" status" if key == "adminFilterable" else "") + " }" for key in _keys)
    ATTRIBUTE_FIELD_CAPABILITIES[_keys] = ATTRIBUTE_FIELD.replace("access { storefront }", "access { storefront } capabilities { " + _selection + " }")
ATTRIBUTE_VALUES = '''query AttributeValues($id: ID!, $namespace: String!) { product(id: $id) {
 id metafields(first: 129, namespace: $namespace) { nodes { id key type value compareDigest } pageInfo { hasNextPage } } } }'''
ATTRIBUTE_ENTRIES = '''query AttributeEntries($ids: [ID!]!) { nodes(ids: $ids) { ... on Metaobject {
 id type handle fields { key value } capabilities { publishable { status } }
 definition { id type access { storefront } } } } }'''
ATTRIBUTE_DEF_CREATE = '''mutation AttributeDefinitionCreate($definition: MetaobjectDefinitionCreateInput!) {
 metaobjectDefinitionCreate(definition: $definition) { metaobjectDefinition { id } userErrors { field message } } }'''
ATTRIBUTE_TERM_CREATE = '''mutation AttributeTermCreate($metaobject: MetaobjectCreateInput!) {
 metaobjectCreate(metaobject: $metaobject) { metaobject { id } userErrors { field message } } }'''
ATTRIBUTE_FIELD_CREATE = '''mutation AttributeFieldCreate($definition: MetafieldDefinitionInput!) {
 metafieldDefinitionCreate(definition: $definition) { createdDefinition { id } userErrors { field message } } }'''
ATTRIBUTE_SET = '''mutation AttributeAssignment($metafields: [MetafieldsSetInput!]!) {
 metafieldsSet(metafields: $metafields) { metafields { id key value } userErrors { field message } } }'''
ATTRIBUTE_DEF_ACCESS = '''mutation AttributeDefinitionAccess($id: ID!, $definition: MetaobjectDefinitionUpdateInput!) {
 metaobjectDefinitionUpdate(id: $id, definition: $definition) { metaobjectDefinition { id } userErrors { field message } } }'''
ATTRIBUTE_FIELD_ACCESS = '''mutation AttributeFieldAccess($definition: MetafieldDefinitionUpdateInput!) {
 metafieldDefinitionUpdate(definition: $definition) { updatedDefinition { id } userErrors { field message } } }'''
READ_QUERIES |= frozenset((ATTRIBUTE_SCHEMA,ATTRIBUTE_DEFINITION,ATTRIBUTE_TERM,ATTRIBUTE_FIELD,ATTRIBUTE_VALUES,ATTRIBUTE_ENTRIES))
READ_QUERIES |= frozenset(ATTRIBUTE_FIELD_CAPABILITIES.values())
WRITE_QUERIES |= frozenset((ATTRIBUTE_DEF_CREATE,ATTRIBUTE_TERM_CREATE,ATTRIBUTE_FIELD_CREATE,ATTRIBUTE_SET,ATTRIBUTE_DEF_ACCESS,ATTRIBUTE_FIELD_ACCESS))
NAVIGATION_READ = '''query NavigationEligibility($ids: [ID!]!, $publication: ID!) {
 publication(id: $publication) { id name app { title } }
 nodes(ids: $ids) { ... on Product { id status metafield(namespace: "tlc_dashboard", key: "publish_attempt") { value }
 publishedOnPublication(publicationId: $publication) } }
}'''
READ_QUERIES |= frozenset((NAVIGATION_READ,))


def _cursor(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise ShopifyError("Invalid pagination cursor. Restart discovery from its first page.")
    return value


def _gid(value, kind):
    suffix = r"\d{1,30}"
    if kind == "Job":
        suffix = r"(?:\d{1,30}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    if not isinstance(value, str) or not re.fullmatch(r"gid://shopify/" + kind + "/" + suffix, value):
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


class ShopifyIdentityConflict(ShopifyError):
    """Complete reads contradict a retained identity; never repair by creation."""


class ShopifyVariantMissing(ShopifyError):
    """A retained variant is absent from a COMPLETE bounded parent read."""


class ShopifyProductAbsent(ShopifyError):
    """A successful exact product read returned null, not a transport failure."""


class ShopifyNativeWriteRejected(ShopifyError):
    """Authoritative HTTP authentication rejection: native mutation did not run."""


@dataclass(frozen=True)
class ShopifyConfiguration:
    domain: str
    token: str = field(default="", repr=False)
    api_version: str = "2026-10"
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.get("domain", ""), settings.get("token", ""),
                   settings.get("api_version", "2026-10"), settings.get("client_id", ""),
                   settings.get("client_secret", ""))

    def fingerprint(self):
        # Stable credentials, never the acquired/rotating token. Preserve M3A's
        # exact manual-token fingerprint and signed Preview freshness semantics.
        data = {"domain": self.domain, "token": self.token, "version": self.api_version}
        if self.client_id or self.client_secret:
            data.update(client_id=self.client_id, client_secret=self.client_secret)
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def validated(self):
        if not isinstance(self.domain, str):
            raise ShopifyError("Enter your shop-name.myshopify.com domain.")
        domain = self.domain.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com", domain):
            raise ShopifyError("Enter only your shop-name.myshopify.com domain, without a URL, path or port.")
        if bool(self.client_id) != bool(self.client_secret):
            raise ShopifyError("Incomplete Shopify client credentials. Supply both Client ID and Client Secret; do not mix deployment and saved settings.")
        if self.client_id and self.token:
            raise ShopifyError("Conflicting Shopify authentication. Remove the Admin API token when using Client ID and Client Secret.")
        if self.client_id:
            if (not isinstance(self.client_id, str) or not isinstance(self.client_secret, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", self.client_id)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", self.client_secret)):
                raise ShopifyError("Invalid Shopify Client ID or Client Secret. Check Dev Dashboard Settings; values must not contain whitespace.")
        elif not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", self.token):
            raise ShopifyError("Enter a valid Admin API access token, not a storefront token or client secret.")
        if self.api_version not in VERSIONS:
            raise ShopifyError("Select a supported pinned Shopify API version.")
        return ShopifyConfiguration(domain, self.token, self.api_version, self.client_id, self.client_secret)


# Process-private and bounded; the supported Docker runtime uses one worker.
# The lock coalesces concurrent acquisitions, including failure cooldowns.
_token_lock = threading.Lock()
_tokens = OrderedDict()


def _token_key(config):
    return hashlib.sha256(json.dumps([config.domain, config.client_id, config.client_secret]).encode()).hexdigest()


def _authentication_failed(config):
    if config.client_id:
        with _token_lock:
            _tokens[_token_key(config)] = {"failed_until": monotonic() + 30}
            while len(_tokens) > 16:
                _tokens.popitem(last=False)


def _access_token(config):
    if not config.client_id:
        return config.token
    key = _token_key(config)
    with _token_lock:
        entry = _tokens.get(key, {})
        if monotonic() < entry.get("failed_until", 0):
            raise ShopifyError("Shopify authentication is temporarily paused after a failure. Check credentials, installed app and same-organization eligibility; retry after 30 seconds.")
        if monotonic() < entry.get("refresh_at", 0):
            _tokens.move_to_end(key)
            return entry["token"]
        session = requests.Session()
        session.trust_env = False
        try:
            started = monotonic()
            with session.post(f"https://{config.domain}/admin/oauth/access_token",
                    data={"grant_type": "client_credentials", "client_id": config.client_id,
                          "client_secret": config.client_secret},
                    headers={"Accept": "application/json"}, timeout=(5, 15),
                    allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ShopifyError("Shopify Client Credentials authentication failed. Check Dev Dashboard credentials, app installation and that app/store belong to the same organization.")
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536 or monotonic() - started > 20:
                        raise ShopifyError("Shopify authentication returned oversized or incomplete evidence. Retry later.")
                data = json.loads(raw)
                token, expiry = data.get("access_token"), data.get("expires_in")
                if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", token)
                        or type(expiry) is not int or not 0 < expiry <= 2592000
                        or not isinstance(data.get("scope"), str)):
                    raise ShopifyError("Shopify authentication returned invalid token/expiry evidence. No token was retained.")
                refresh_at = started + expiry - min(60, expiry / 10)
                if refresh_at <= monotonic():
                    raise ShopifyError("Shopify token expired during acquisition. Retry later.")
                _tokens[key] = {"token": token, "refresh_at": refresh_at}
                _tokens.move_to_end(key)
                while len(_tokens) > 16:
                    _tokens.popitem(last=False)
                return token
        except (ShopifyError, requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            _tokens[key] = {"failed_until": monotonic() + 30}
            while len(_tokens) > 16:
                _tokens.popitem(last=False)
            if isinstance(exc, ShopifyError):
                raise
            raise ShopifyError("Unable to authenticate with Shopify securely. Check connectivity and credentials; no automatic retry was attempted.") from None
        finally:
            session.close()


class ShopifyReadThrottled(ShopifyError):
    def __init__(self, delay):
        super().__init__("Shopify read budget is exhausted; bounded read retries ended. Wait before another comparison.")
        self.delay=delay


class ShopifyAdapter:
    platform = "shopify"
    can_publish = False

    def product_revisions(self, configuration, ids):
        if not 1 <= len(ids) <= 25 or len(set(ids)) != len(ids):
            raise ShopifyError("Revision checks require one to 25 distinct product identities.")
        nodes = self._read(configuration, REVISIONS_QUERY, {"ids": [_gid(i, "Product") for i in ids]}).get("nodes")
        if not isinstance(nodes, list) or len(nodes) != len(ids):
            raise ShopifyError("Shopify revision evidence is incomplete.")
        result = {}
        for expected, row in zip(ids, nodes):
            if row is not None and (row.get("id") != expected or not isinstance(row.get("updatedAt"), str)
                    or row.get("status") not in {"DRAFT", "ACTIVE", "ARCHIVED"}):
                raise ShopifyError("Shopify revision identity/status evidence is incomplete.")
            result[expected] = row
        return result

    def _read(self, configuration, query, variables=None, *, session=None):
        if query not in READ_QUERIES:
            raise ShopifyError("Only fixed read-only Shopify queries are supported.")
        return self._transport(configuration, query, variables, session=session)

    def _write(self, configuration, query, variables):
        if query not in WRITE_QUERIES:
            raise ShopifyError("Unsupported Shopify draft mutation.")
        return self._transport(configuration, query, variables)

    def _transport(self, configuration, query, variables=None, *, session=None, api_version=None):
        retries=0; waited=0
        while True:
            try:
                return self._request(configuration,query,variables,session=session,api_version=api_version)
            except ShopifyReadThrottled as exc:
                # Connection/auth tests and every mutation stay single-attempt.
                if query==QUERY or query in WRITE_QUERIES or retries>=2 or not 0<=exc.delay<=2 or waited+exc.delay>3:
                    raise
                sleep(exc.delay); waited+=exc.delay; retries+=1

    def _note_request(self,query,version,status,cost=None,*,throttled=False):
        name=re.search(r"(?:query|mutation)\s+(\w+)",query)
        detail={"query":name.group(1) if name else "fixed-query","version":version,"http_status":status,
            "writing":query in WRITE_QUERIES,"throttled":status==429 or throttled}
        if isinstance(cost,dict):
            detail.update({k:cost[k] for k in ("requestedQueryCost","actualQueryCost") if isinstance(cost.get(k),(int,float))})
            throttle=cost.get("throttleStatus",{})
            if isinstance(throttle,dict): detail["throttle"]={k:throttle[k] for k in ("currentlyAvailable","restoreRate") if isinstance(throttle.get(k),(int,float))}
        self.request_trace=(getattr(self,"request_trace",[])+[detail])[-128:]
        self.request_count=getattr(self,"request_count",0)+1
        from flask import has_app_context,has_request_context,current_app,g
        if has_request_context():
            g.shopify_request_summary={"count":self.request_count,"last":detail,
                "throttled":sum(r["throttled"] for r in self.request_trace),"timeouts":sum(r["http_status"]=="timeout" for r in self.request_trace)}
        if has_app_context(): current_app.logger.info("Shopify request %s",json.dumps(detail,sort_keys=True))

    def _request(self, configuration, query, variables=None, *, session=None, api_version=None):
        if query not in READ_QUERIES | WRITE_QUERIES:
            raise ShopifyError("Unsupported Shopify operation.")
        writing = query in WRITE_QUERIES
        config = configuration.validated()
        from app.shopify_capacity import before
        before(config, writing=writing, query=query)
        if api_version is not None and (api_version != "2027-01" or query not in NATIVE_QUERIES):
            raise ShopifyError("Only packed-dimension operations may use the release-candidate API.")
        version = api_version or config.api_version
        token = _access_token(config)
        own = session is None
        session = session or requests.Session()
        if own:
            session.trust_env = False  # Never send the token through an inherited proxy/netrc.
        try:
            with session.post(
                f"https://{config.domain}/admin/api/{version}/graphql.json",
                headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
                json={"query": query, **({"variables": variables} if variables is not None else {})},
                timeout=(5, 15), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code!=200: self._note_request(query,version,response.status_code)
                if response.status_code in (401, 403):
                    if response.status_code == 401:
                        _authentication_failed(config)
                    if query == NATIVE_WRITE:
                        raise ShopifyNativeWriteRejected("Shopify denied the native update before execution. Check credentials and write_inventory; rebuild Review after resolving access.")
                    raise ShopifyError("Shopify rejected authentication or access. Check the installed app, token expiry and scopes.")
                if response.status_code == 429:
                    from app.shopify_capacity import observed
                    observed(config, None, throttled=True)
                    if writing:
                        raise ShopifyError("Shopify throttled this mutation. Review retained operation evidence before any retry.")
                    try: delay=float(response.headers.get("Retry-After","0.5"))
                    except (TypeError,ValueError): delay=3
                    raise ShopifyReadThrottled(delay)
                if response.status_code != 200:
                    if writing:
                        raise ShopifyError("Shopify mutation failed with uncertain evidence. Reconcile the retained operation; do not blindly retry.")
                    raise ShopifyError("Shopify connection failed. Check the domain and service availability; no store data was changed.")
                if response.headers.get("X-Shopify-API-Version", version) != version:
                    raise ShopifyError("Shopify served a different API version. Select a supported version and test again.")
                raw = bytearray()
                started = monotonic()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw) > (65536 if query == QUERY else 1048576) or monotonic() - started > 20:
                        raise ShopifyError("Shopify returned incomplete mutation evidence. Reconcile before retrying." if writing else "Shopify returned an oversized or incomplete response. Try again later.")
                data = json.loads(raw)
                cost=(data.get("extensions") or {}).get("cost") if isinstance(data,dict) else None
                errors=data.get("errors",[]) if isinstance(data,dict) else []
                throttled=isinstance(errors,list) and any(isinstance(e,dict) and (e.get("extensions") or {}).get("code")=="THROTTLED" for e in errors)
                self._note_request(query,version,response.status_code,cost,throttled=throttled)
                from app.shopify_capacity import observed
                observed(config, cost, throttled=throttled)
                if data.get("errors"):
                    errors = data["errors"]
                    if isinstance(errors, list) and any(isinstance(e, dict) and
                            (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                        if writing: raise ShopifyError("Shopify query budget is exhausted. Review retained operation evidence before retrying.")
                        bucket=(cost or {}).get("throttleStatus",{})
                        deficit=max(0,(cost or {}).get("requestedQueryCost",0)-bucket.get("currentlyAvailable",0))
                        rate=bucket.get("restoreRate",0)
                        raise ShopifyReadThrottled(max(.25,deficit/rate) if rate else .5)
                    raise ShopifyError("Shopify returned mutation errors. Reconcile retained evidence; no automatic retry." if writing else "Shopify could not complete the read query. Check token permissions or rate limits.")
                if not isinstance(data["data"], dict):
                    raise ShopifyError("Shopify returned incomplete mutation evidence. Reconcile before retrying." if writing else "Shopify returned incomplete read evidence. Retry later.")
                return data["data"]
        except requests.Timeout:
            self._note_request(query,version,"timeout")
            raise ShopifyError("Shopify mutation timed out. Reconcile retained evidence; do not blindly retry." if writing else "Shopify timed out. Retry the read-only connection test later.") from None
        except requests.RequestException:
            raise ShopifyError("Shopify mutation outcome is uncertain. Reconcile retained evidence; do not blindly retry." if writing else "Unable to reach Shopify securely. Check connectivity and the store domain.") from None
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError):
            raise ShopifyError("Shopify returned invalid mutation evidence. Reconcile retained evidence; do not blindly retry." if writing else "Shopify returned incomplete or invalid connection evidence. Nothing was saved.") from None
        finally:
            if own:
                session.close()

    def stage_images(self, configuration, images):
        return self._write(configuration, STAGE_QUERY, {"input": [
            {"filename": image["filename"], "mimeType": image["mime"], "resource": "PRODUCT_IMAGE",
             "httpMethod": "POST", "fileSize": str(image["size"])} for image in images]})

    def create_draft(self, configuration, product, media):
        return self._write(configuration, CREATE_QUERY, {"product": product, "media": media})

    def collection_candidates(self, config, title, handle):
        if not isinstance(title, str) or len(title) > 191 or not re.fullmatch(r"[a-z0-9-]{1,96}", handle):
            raise ShopifyError("Invalid local collection definition.")
        quoted = json.dumps(title)
        return _connection(self._read(config, COLLECTION_LOOKUP, {"query": f"title:{quoted} OR handle:{handle}"}).get("collections"))

    def collection_state(self, config, remote_id, product_id="gid://shopify/Product/0"):
        check = product_id != "gid://shopify/Product/0"
        row = self._read(config, COLLECTION_STATE, {"id": _gid(remote_id, "Collection"), "product": _gid(product_id, "Product"), "check": check}).get("collection")
        if isinstance(row, dict) and not check:
            row["hasProduct"] = False  # no remote product yet; do not query a fictitious identity
        return row

    def create_collection(self, config, data):
        return self._write(config, COLLECTION_CREATE, {"input": data})

    def join_collection(self, config, collection_id, product_id):
        return self._write(config, COLLECTION_JOIN, {"id": _gid(collection_id, "Collection"), "products": [_gid(product_id, "Product")]})

    def standard_category(self, config, value):
        if not isinstance(value, str) or not re.fullmatch(r"gid://shopify/TaxonomyCategory/[a-zA-Z0-9-]{1,100}", value):
            raise ShopifyError("Use a Shopify TaxonomyCategory GID, not a guessed name.")
        row = self._read(config, STANDARD_CATEGORY, {"id": value}).get("node")
        if not row or row.get("id") != value or row.get("isArchived") is not False or not row.get("fullName"):
            raise ShopifyError("Standard category is unavailable or archived in this API version.")
        return row

    def set_simple_variant(self, configuration, product_id, variant_id, sku, price):
        return self._write(configuration, PRICE_QUERY, {"id": product_id,
            "variants": [{"id": variant_id, "price": price, "inventoryItem": {"sku": sku}}]})

    def read_simple(self, configuration, product_id, namespace):
        return self._read(configuration, SIMPLE_QUERY,
            {"id": _gid(product_id, "Product"), "namespace": namespace}).get("product")

    def packed_capability(self, config):
        return self._transport(config, NATIVE_CAPABILITY, api_version="2027-01")

    def read_variable(self, config, product_id, namespace, *, native=False):
        product_id=_gid(product_id,"Product")
        after, seen, nodes, first, header = None, set(), [], None, None
        deadline=monotonic()+60
        for _ in range(4):
            if monotonic()>deadline:
                raise ShopifyError("Variable read deadline exceeded; partial pages are not comparison evidence.")
            payload=self._transport(config, VARIABLE_NATIVE_READ if native else VARIABLE_READ,
                {"id":product_id,"after":after,**({} if native else {"namespace":namespace})},
                **({"api_version":"2027-01"} if native else {}))
            if monotonic()>deadline:
                raise ShopifyError("Variable read deadline exceeded; partial pages are not comparison evidence.")
            if not isinstance(payload,dict) or "product" not in payload:
                raise ShopifyError("Variable product read is incomplete; absence is not established.")
            remote=payload["product"]
            if remote is None and first is None: return None
            if not isinstance(remote,dict) or remote.get("id")!=product_id:
                raise ShopifyError("Variable product identity disappeared/changed during pagination; incomplete read.")
            current={k:v for k,v in remote.items() if k!="variants"}
            if first is None: first,header=remote,current
            elif current!=header:
                raise ShopifyError("Product revision/options/media changed across variant pages; compare again, no write.")
            connection=remote.get("variants",{})
            if not isinstance(connection,dict):
                raise ShopifyError("Variable page is incomplete; no unseen variant is considered deleted.")
            page=connection.get("pageInfo",{})
            rows=connection.get("nodes")
            if not isinstance(page,dict) or not isinstance(rows,list) or type(page.get("hasNextPage")) is not bool or any(not isinstance(v,dict) for v in rows):
                raise ShopifyError("Variable page is incomplete; no unseen variant is considered deleted.")
            ids=[v.get("id") for v in rows]
            if any(not isinstance(i,str) or i in {n["id"] for n in nodes} for i in ids) or len(set(ids))!=len(ids):
                raise ShopifyError("Duplicate/incomplete variant page identities; no mapping is adopted.")
            nodes.extend(rows)
            if len(nodes)>32:
                raise ShopifyError("Remote product exceeds the existing 32-variant contract; no truncation or repair create.")
            if page["hasNextPage"] is False:
                first["variants"]={"nodes":nodes,"pageInfo":{"hasNextPage":False}}
                return first
            cursor=page.get("endCursor")
            if not rows or not isinstance(cursor,str) or not cursor or len(cursor)>1024 or cursor in seen:
                raise ShopifyError("Variant pagination stalled/missing cursor; partial evidence is unavailable.")
            seen.add(cursor); after=cursor
        raise ShopifyError("Variant page budget exhausted; no partial equality or duplicate create is permitted.")

    def create_variable(self, config, product, media):
        return self._transport(config, VARIABLE_CREATE, {"product": product, "media": media})

    def lifecycle_product(self, config, product_id):
        data = self._read(config, LIFECYCLE_READ, {"id": _gid(product_id, "Product")})
        if "product" not in data:
            raise ShopifyError("Incomplete product existence evidence; absence is not established.")
        return data["product"]

    def delete_product(self, config, product_id):
        return self._write(config, PRODUCT_DELETE, {"id": _gid(product_id, "Product")})

    def attribute_schema(self, config):
        return self._read(config, ATTRIBUTE_SCHEMA)

    def attribute_resource(self, config, kind, spec):
        if kind == "definition":
            return self._read(config,ATTRIBUTE_DEFINITION,{"type":spec["type"]})["metaobjectDefinitionByType"]
        if kind == "term":
            return self._read(config,ATTRIBUTE_TERM,{"type":spec["type"],"handle":spec["handle"]})["metaobjectByHandle"]
        keys=tuple(sorted(spec.get("capabilities", {})))
        query=ATTRIBUTE_FIELD_CAPABILITIES[keys] if keys else ATTRIBUTE_FIELD
        part=self._read(config,query,{"namespace":spec["namespace"],"key":spec["key"]})["metafieldDefinitions"]
        if part["pageInfo"]["hasNextPage"] or len(part["nodes"])>1:
            raise ShopifyError("Attribute definition lookup is ambiguous/incomplete.")
        return part["nodes"][0] if part["nodes"] else None

    def create_attribute_resource(self, config, kind, spec):
        query = {"definition":ATTRIBUTE_DEF_CREATE,"term":ATTRIBUTE_TERM_CREATE,"field":ATTRIBUTE_FIELD_CREATE}[kind]
        return self._write(config,query,{"metaobject" if kind=="term" else "definition":spec})

    def update_attribute_access(self, config, kind, remote_id, spec):
        if kind == "definition":
            return self._write(config,ATTRIBUTE_DEF_ACCESS,{"id":_gid(remote_id,"MetaobjectDefinition"),"definition":{"access":spec["access"]}})
        if kind == "field":
            patch={"namespace":spec["namespace"],"key":spec["key"],"ownerType":"PRODUCT"}
            patch.update({key:spec[key] for key in ("access","capabilities") if key in spec})
            return self._write(config,ATTRIBUTE_FIELD_ACCESS,{"definition":patch})
        raise ShopifyError("Only existing controlled definitions permit reviewed access updates.")

    def attribute_values(self, config, product_id, namespace):
        product=self._read(config,ATTRIBUTE_VALUES,{"id":_gid(product_id,"Product"),"namespace":namespace}).get("product")
        connection=product.get("metafields") if isinstance(product,dict) else None
        nodes=connection.get("nodes") if isinstance(connection,dict) else None
        if (not product or product.get("id")!=product_id or not isinstance(connection,dict)
                or connection.get("pageInfo",{}).get("hasNextPage") is not False
                or not isinstance(nodes,list) or len(nodes)>128
                or any(not isinstance(v,dict) or not {"id","key","type","value","compareDigest"}.issubset(v) for v in nodes)):
            raise ShopifyError("Attribute assignments are incomplete; no clearing/equality assumed.")
        if len({v["key"] for v in nodes})!=len(nodes):
            raise ShopifyError("Attribute assignment keys are ambiguous; no equality assumed.")
        return nodes

    def set_attributes(self, config, values):
        return self._write(config,ATTRIBUTE_SET,{"metafields":values})

    def attribute_entries(self, config, ids):
        if not ids or len(ids)>40 or len(set(ids))!=len(ids):
            raise ShopifyError("Inspect one bounded set of up to forty unique term references.")
        return self._read(config,ATTRIBUTE_ENTRIES,{"ids":[_gid(i,"Metaobject") for i in ids]})["nodes"]

    def navigation_eligibility(self, config, ids, publication):
        if not 1<=len(ids)<=25:
            raise ShopifyError("Check at most 25 retained product identities per navigation request.")
        return self._read(config,NAVIGATION_READ,{"ids":[_gid(i,"Product") for i in ids],"publication":_gid(publication,"Publication")})

    def read_packed_inventory(self, config, inventory_id):
        return self._transport(config, NATIVE_INVENTORY_READ, {"id": _gid(inventory_id, "InventoryItem")}, api_version="2027-01").get("inventoryItem")

    def create_variants(self, config, product_id, variants):
        return self._transport(config, VARIANTS_CREATE, {"id": _gid(product_id, "Product"), "variants": variants})

    def find_skus(self, config, skus):
        if not 1 <= len(skus) <= 33 or any(not isinstance(s,str) or not s.strip() or len(s)>64 for s in skus):
            raise ShopifyError("Bounded scanned SKUs are required for duplicate discovery.")
        query = " OR ".join("sku:" + json.dumps(s) for s in skus)
        return self._read(config, SKU_QUERY, {"query": query}).get("productVariants")

    def update_variants(self, config, product_id, variants):
        return self._transport(config, VARIANTS_UPDATE, {"id": _gid(product_id, "Product"), "variants": variants})

    def read_packed(self, config, product_id):
        return self._transport(config, NATIVE_READ, {"id": _gid(product_id, "Product")}, api_version="2027-01").get("product")

    def write_packed(self, config, inventory_id, dimensions):
        return self._transport(config, NATIVE_WRITE, {"id": _gid(inventory_id, "InventoryItem"),
            "input": {"measurement": {"packedDimensions": dimensions}}}, api_version="2027-01")

    def country_codes(self, configuration):
        values = (self._read(configuration, COUNTRIES_QUERY).get("__type") or {}).get("enumValues")
        if (not isinstance(values, list) or not 1 <= len(values) <= 300
                or any(not isinstance(v, dict) or not isinstance(v.get("name"), str)
                    or not re.fullmatch(r"[A-Z]{2}", v["name"]) for v in values)):
            raise ShopifyError("Supported origin codes could not be verified. Review again later.")
        return {v["name"] for v in values}

    def update_simple_product(self, configuration, product):
        return self._write(configuration, UPDATE_QUERY, {"product": product})

    def publications(self, config):
        after, seen, rows = None, set(), []
        for _ in range(3):
            connection = _connection(self._read(config, PUBLICATIONS_QUERY, {'after':after}).get('publications'))
            rows.extend(connection['nodes'])
            if len(rows)>40: raise ShopifyError('Channel discovery exceeds the forty-publication safety bound.')
            if not connection['pageInfo']['hasNextPage']: return rows
            after = connection['pageInfo'].get('endCursor')
            if not after or after in seen: break
            seen.add(after)
        raise ShopifyError('Channel discovery is incomplete; no unavailable channel is assumed unpublished.')

    def publication_state(self, config, product_id):
        return self._read(config, PUBLICATION_STATE, {'id':_gid(product_id,'Product')}).get('product')

    def set_publication(self, config, product_id, publication_id, published):
        return self._write(config, PUBLICATION_PUBLISH if published else PUBLICATION_UNPUBLISH,
            {'id':_gid(product_id,'Product'),'input':[{'publicationId':_gid(publication_id,'Publication')}]})

    def update_simple_variant(self, configuration, product_id, variant):
        return self._write(configuration, PRICE_QUERY, {"id": product_id, "variants": [variant]})

    def update_media_alt(self, configuration, files):
        return self._write(configuration, FILES_QUERY, {"files": files})

    def reorder_media(self, configuration, product_id, ids):
        return self._write(configuration, ORDER_QUERY,
            {"id": product_id, "moves": [{"id": media_id, "newPosition": str(i)} for i, media_id in enumerate(ids)]})

    def read_draft(self, configuration, product_id):
        return self._read(configuration, DRAFT_QUERY, {"id": _gid(product_id, "Product")}).get("product")

    def find_draft(self, configuration, handle):
        if not isinstance(handle, str) or len(handle) > 255 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", handle):
            raise ShopifyError("Invalid draft recovery handle.")
        return _connection(self._read(configuration, FIND_DRAFT_QUERY, {"query": "handle:" + handle}).get("products"))

    def find_sku(self, configuration, sku):
        if not isinstance(sku, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sku):
            raise ShopifyError("SKU is not safe for exact duplicate discovery. Review the local identity.")
        return _connection(self._read(configuration, SKU_QUERY, {"query": 'sku:"' + sku + '"'}).get("productVariants"))

    def upload_image(self, configuration, target, image, content):
        """Upload only to a validated staged target, without the Admin API token."""
        from app.shopify_capacity import before
        before(configuration, writing=True)
        config = configuration.validated()
        def safe_url(value):
            try:
                parsed = urlsplit(value)
                host = parsed.hostname or ""
                return (parsed.scheme == "https" and not parsed.username and not parsed.password
                    and parsed.port in (None, 443) and (host == config.domain or host == "cdn.shopify.com" or host == "storage.googleapis.com"
                    or host.endswith(".storage.googleapis.com") or host.endswith(".s3.amazonaws.com")))
            except (TypeError, ValueError):
                return False
        if not safe_url(target.get("url")) or not safe_url(target.get("resourceUrl")):
            raise ShopifyError("Shopify staged target is not an approved HTTPS upload destination.")
        parameters = target.get("parameters")
        if (not isinstance(parameters, list) or len(parameters) > 30
                or any(not isinstance(p, dict) or not isinstance(p.get("name"), str)
                       or not isinstance(p.get("value"), str) for p in parameters)):
            raise ShopifyError("Shopify staged upload parameters are incomplete.")
        session = requests.Session()
        session.trust_env = False
        try:
            with session.post(target["url"], data=[(p["name"], p["value"]) for p in parameters],
                    files={"file": (image["filename"], content, image["mime"])},
                    timeout=(5, 30), allow_redirects=False, stream=True) as response:
                if response.status_code not in (200, 201, 204):
                    raise ShopifyError("Staged image upload failed. No automatic retry was attempted.")
        except requests.RequestException:
            raise ShopifyError("Staged image upload outcome is uncertain. Review the operation; do not blindly retry.") from None
        finally:
            session.close()

    def test_connection(self, configuration, *, session=None):
        config = configuration.validated()
        data = self._read(config, QUERY, session=session)
        try:
            shop = data["shop"]
            scopes = data["currentAppInstallation"]["accessScopes"]
            # Shopify's original API domain and storefront primary domain may
            # differ. Accept only hosts returned for this authenticated Shop,
            # never redirects, DNS guesses or authentication alone.
            hosts = [shop["myshopifyDomain"]]
            if shop.get("primaryDomain") is not None:
                hosts.append(shop["primaryDomain"]["host"])
            domains = shop.get("domains", [])
            if not isinstance(domains, list) or len(domains) > 100:
                raise ShopifyError("Shopify returned invalid domain evidence. Nothing was saved.")
            hosts.extend(domain["host"] for domain in domains)
            if (any(not isinstance(host, str) for host in hosts)
                    or config.domain not in {host.lower() for host in hosts}
                    or not re.fullmatch(r"gid://shopify/Shop/\d+", shop["id"])):
                raise ShopifyError("Configured domain is not an authoritative domain of the authenticated Shopify store. Check Shopify Settings → Domains. Nothing was saved.")
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
