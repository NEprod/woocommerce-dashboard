"""Local authored extensions; no platform IDs, writes or variation generation."""
from copy import deepcopy
import os
import re

from jsonschema import Draft202012Validator


CUSTOMISATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "proof_policy": {"enum": ["none", "required", "optional"]},
        "fields": {"type": "object", "maxProperties": 30,
            "propertyNames": {"pattern": "^[a-z][a-z0-9_]{0,63}$"},
            "additionalProperties": {"type": "object", "additionalProperties": False,
                "properties": {
                    "type": {"enum": ["text", "number", "multiline", "photo", "choice", "checkbox"]},
                    "label": {"type": "string", "minLength": 1, "maxLength": 191},
                    "required": {"type": "boolean"}, "enabled": {"type": "boolean"},
                    "remove": {"type": "boolean"},
                    "choices": {"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 191}},
                    "max_length": {"type": "integer", "minimum": 1, "maximum": 10000},
                    "minimum": {"type": "number"}, "maximum": {"type": "number"}}}}}}


def validate_extensions(data, *, partial=False):
    """Validate only new keys; do not tighten historic scanner metadata rules."""
    if "country_of_origin" in data and not (
            isinstance(data["country_of_origin"], str)
            and re.fullmatch(r"[A-Z]{2}", data["country_of_origin"])):
        raise ValueError("country_of_origin must be an uppercase two-letter country code.")
    if "requires_shipping" in data and type(data["requires_shipping"]) is not bool:
        raise ValueError("requires_shipping must be true or false.")
    if "customisation" not in data:
        return
    value = data["customisation"]
    error = next(Draft202012Validator(CUSTOMISATION_SCHEMA).iter_errors(value), None)
    if error:
        path = ".".join(str(p) for p in error.path)
        raise ValueError(f"customisation.{path}: {error.message}")
    for key, field in value.get("fields", {}).items():
        if field.get("remove"):
            if set(field) != {"remove"}:
                raise ValueError(f"customisation.fields.{key}: remove must be the only property.")
            if not partial:
                raise ValueError(f"customisation.fields.{key}: remove is an override patch, not a resolved field.")
            continue
        if field.get("minimum", float('-inf')) > field.get("maximum", float('inf')):
            raise ValueError(f"customisation.fields.{key}: minimum exceeds maximum.")
        if not partial and field.get("enabled", True):
            if not field.get("type") or not field.get("label", "").strip():
                raise ValueError(f"customisation.fields.{key}: an enabled field needs type and label.")
            if field["type"] == "choice" and not field.get("choices"):
                raise ValueError(f"customisation.fields.{key}: a choice field needs choices.")


def merge_customisation(shared, override):
    validate_extensions({"customisation": shared}, partial=True)
    validate_extensions({"customisation": override}, partial=True)
    result = deepcopy(shared)
    if "proof_policy" in override:
        result["proof_policy"] = override["proof_policy"]
    fields = result.setdefault("fields", {})
    for key, patch in override.get("fields", {}).items():
        if patch.get("remove"):
            fields.pop(key, None)
        else:
            fields[key] = {**fields.get(key, {}), **deepcopy(patch)}
    return result


def shipping_details(authored, product):
    validate_extensions(authored)
    default = os.environ.get("DEFAULT_COUNTRY_OF_ORIGIN", "")
    if default:
        validate_extensions({"country_of_origin": default})
    return {"weight_g": authored.get("weight", product.weight),
        "dimensions_mm": authored.get("dimensions", {
            "length": product.length, "width": product.width, "height": product.height}),
        "country_of_origin": authored.get("country_of_origin", default or None),
        "origin_source": "authored" if "country_of_origin" in authored else "deployment_default" if default else "unset",
        "requires_shipping": authored.get("requires_shipping")}
