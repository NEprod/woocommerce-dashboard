"""Small read-only platform boundary; existing Woo publish services stay authoritative."""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from app.commerce_config import active_platform
from app.shopify_connection import ShopifyAdapter
from app.taxonomy_assignments import product_document


class WooCommerceAdapter:
    platform = "woocommerce"
    can_publish = True

    def configuration(self):
        from app.woocommerce_connection import effective_configuration
        return effective_configuration().safe_summary()


def adapter():
    return ShopifyAdapter() if active_platform() == "shopify" else WooCommerceAdapter()


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, Decimal):
        return str(value)
    return value


@dataclass(frozen=True)
class CatalogueSnapshot:
    local_id: int
    stable_identity: str
    sku: str
    product_type: str
    data: object


def catalogue_snapshot(product):
    """No remote reads, ORM writes, scanning, SKU allocation or authored writes."""
    from app import db
    with db.session.no_autoflush:
        authored = product_document(product)
        from app.catalogue_fields import shipping_details
        from app.taxonomy_assignments import resolve
        data = {
            "title": product.title, "description": product.description,
            "short_description": product.short_description,
            "price": product.regular_price, "sale_price": product.sale_price,
            "shipping": shipping_details(authored, product),
            "seo": {"title": authored.get("meta_title", product.meta_title),
                    "description": authored.get("meta_description", product.meta_description),
                    "handle": authored.get("handle")},
            "vendor": authored.get("vendor"),
            "storefront_collections": authored.get("storefront_collections", []),
            "customisation": authored.get("customisation", {}),
            "taxonomy": resolve(authored),
            "attributes": [{"name": a.name, "values": a.values} for a in product.attributes],
            "images": [{"url": i.url, "position": i.position, "alt": i.alt_text} for i in product.images],
            "categories": [c.name for c in product.categories], "tags": [t.name for t in product.tags],
            "authored": authored,
            "variations": [{"local_id": v.id, "stable_identity": v.source_identity,
                "sku": v.sku, "status": v.catalogue_status, "price": v.regular_price,
                "sale_price": v.sale_price,
                "weight_g": v.weight,
                "dimensions_mm": {"length": v.length, "width": v.width, "height": v.height},
                "attributes": [{"name": a.name, "value": a.value} for a in v.attributes],
                "images": [{"url": i.url, "position": i.position} for i in v.images]}
                for v in sorted(product.variations, key=lambda v: v.id)],
        }
        return CatalogueSnapshot(product.id, product.source_relpath or f"product:{product.id}",
                                 product.sku, product.product_type, _freeze(data))
