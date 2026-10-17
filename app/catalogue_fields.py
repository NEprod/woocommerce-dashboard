"""Local authored extensions; no platform IDs, writes or variation generation."""
from copy import deepcopy
import os
import re

from app.customisations import SCHEMA as CUSTOMISATION_SCHEMA, validate, merge


def validate_extensions(data, *, partial=False):
    """Validate only new keys; do not tighten historic scanner metadata rules."""
    if "country_of_origin" in data and not (
            isinstance(data["country_of_origin"], str)
            and re.fullmatch(r"[A-Z]{2}", data["country_of_origin"])):
        raise ValueError("country_of_origin must be an uppercase two-letter country code.")
    if "requires_shipping" in data and type(data["requires_shipping"]) is not bool:
        raise ValueError("requires_shipping must be true or false.")
    from app.packaging_contract import validate as validate_packaging, finished
    finished(data)
    if 'packaging' in data: validate_packaging(data['packaging'])
    for modifier in (data.get('variation_modifiers') or {}).values():
        if isinstance(modifier,dict) and 'packaging' in modifier: validate_packaging(modifier['packaging'])
        if isinstance(modifier,dict): finished(modifier)
    if "customisation" not in data:
        return
    validate(data['customisation'], partial=partial)


def merge_customisation(shared, override):
    return merge(shared, override)


def shipping_details(authored, product):
    validate_extensions(authored)
    from app.product_defaults import effective
    defaults, sources = effective(authored)
    from app.packaging_contract import finished_mm
    return {"weight_g": (authored.get('packaging') or {}).get('weight_g',authored.get("weight", product.weight)),
        "dimensions_mm": finished_mm(authored) or authored.get("dimensions", {
            "length": product.length, "width": product.width, "height": product.height}),
        "country_of_origin": defaults['country_of_origin'],
        "origin_source": sources['country_of_origin'],
        "requires_shipping": defaults['requires_shipping']}
