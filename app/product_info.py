"""Machine-readable product_info.json contract and editor validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


RESOURCE_ROOT = Path(__file__).parent / "resources" / "product_info"
SCHEMA_ROOT = RESOURCE_ROOT / "schemas"
EXAMPLE_ROOT = RESOURCE_ROOT / "examples"
TEMPLATE_ROOT = RESOURCE_ROOT / "templates"
SHOPIFY_EDITOR_FIELDS = {
    "vendor": {"type": "string", "maxLength": 255},
    "handle": {"type": "string", "maxLength": 255, "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
    "shopify_product_type": {"type": "string", "maxLength": 255},
}

SCHEMA_NAMES = ("collection", "override")
EXAMPLE_NAMES = (
    "simple",
    "variable-collection",
    "single-variable",
    "override",
    "variation-modifiers",
    "complete",
)
TEMPLATE_NAMES = (
    "minimal-collection",
    "minimal-override",
    "complete",
    "simple",
    "variable-collection",
    "single-variable",
)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def load_schema(name):
    if name not in SCHEMA_NAMES:
        raise KeyError(name)
    from app.catalogue_fields import CUSTOMISATION_SCHEMA
    schema = _load_json(SCHEMA_ROOT / f"{name}.schema.json")
    schema["properties"]["customisation"] = CUSTOMISATION_SCHEMA
    schema["properties"].update(SHOPIFY_EDITOR_FIELDS)
    from app.organisation import ADDITIONS_SCHEMA, KEY
    schema['properties']['taxonomy_additions'] = ADDITIONS_SCHEMA
    schema['properties']['shopify_product_type_key'] = KEY
    from app.packaging_contract import PACKAGING_SCHEMA, FINISHED_SCHEMA
    schema['properties']['packaging'] = PACKAGING_SCHEMA
    schema['properties']['finished_dimensions'] = FINISHED_SCHEMA
    # Modifier editors use the same shipping contract as the product root.
    # Previously the closed modifier schema rejected their otherwise valid saves.
    schema['$defs']['variationModifier']['properties'].update(
        packaging=PACKAGING_SCHEMA, finished_dimensions=FINISHED_SCHEMA)
    for key in ('sale_start_date','sale_end_date'):
        schema['properties'][key] = {'type':'string','anyOf':[{'format':'date'},{'format':'date-time'}, {'pattern':r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$'}]}
    schema['properties']['shopify_status'] = {'enum':['DRAFT','ACTIVE','ARCHIVED']}
    schema['properties']['shopify_channels'] = {'type':'object','maxProperties':20,
        'propertyNames':{'pattern':r'^[a-z0-9][a-z0-9_-]{0,95}$'},'additionalProperties':{'type':'boolean'}}
    return schema


def load_example(name):
    if name not in EXAMPLE_NAMES:
        raise KeyError(name)
    return _load_json(EXAMPLE_ROOT / f"{name}.json")


def load_template(name):
    if name not in TEMPLATE_NAMES:
        raise KeyError(name)
    return _load_json(TEMPLATE_ROOT / f"{name}.json")


FIELD_INVENTORY = tuple(_load_json(RESOURCE_ROOT / "field_inventory.json"))
FIELD_BY_KEY = {field["key"]: field for field in FIELD_INVENTORY}
CANONICAL_FIELDS = frozenset(
    field["key"]
    for field in FIELD_INVENTORY
    if field["classification"] == "canonical and active"
)


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]

    @property
    def valid(self):
        return not self.errors

    def to_dict(self):
        return {
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def _path(parts):
    value = "$"
    for part in parts:
        if isinstance(part, int):
            value += f"[{part}]"
        else:
            value += f".{part}"
    return value


def _schema_errors(data, kind):
    validator = Draft202012Validator(
        load_schema(kind), format_checker=FormatChecker()
    )
    issues = []
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
        path = list(error.absolute_path)
        if error.validator == "required":
            missing = error.message.split("'")[1]
            path.append(missing)
        issues.append(
            ValidationIssue(
                _path(path),
                "schema_error",
                error.message,
            )
        )
    return issues


def _warnings(data, kind):
    warnings = []
    for key in data:
        field = FIELD_BY_KEY.get(key)
        if not field:
            if key in SHOPIFY_EDITOR_FIELDS or key in {'shopify_status','shopify_channels','packaging','finished_dimensions','taxonomy_additions','shopify_product_type_key'}:
                continue
            warnings.append(
                ValidationIssue(
                    f"$.{key}",
                    "unknown_field",
                    "Unknown fields are preserved but are not part of the current contract.",
                )
            )
            continue
        classification = field["classification"]
        if classification == "accepted alias":
            warnings.append(
                ValidationIssue(
                    f"$.{key}",
                    "accepted_alias",
                    field["implementation_status"],
                )
            )
        elif classification == "supported but currently ignored":
            warnings.append(
                ValidationIssue(
                    f"$.{key}", "currently_ignored", field["implementation_status"]
                )
            )
        elif classification == "editor-only":
            warnings.append(
                ValidationIssue(
                    f"$.{key}", "editor_only", field["implementation_status"]
                )
            )
        elif classification in {
            "Woo CSV-only",
            "deprecated/legacy",
            "planned but not operational",
        }:
            warnings.append(
                ValidationIssue(
                    f"$.{key}", "currently_ignored", field["implementation_status"]
                )
            )
        if kind == "override" and not field["override_allowed"]:
            warnings.append(
                ValidationIssue(
                    f"$.{key}",
                    "shared_only_field",
                    "This field belongs in collection metadata and is not a normal override field.",
                )
            )

    collection_type = data.get("collection_type")
    if collection_type and collection_type not in {
        "Simple",
        "Variable Collection",
        "Single Variable",
    }:
        warnings.append(
            ValidationIssue(
                "$.collection_type",
                "unknown_collection_type",
                "The scanner currently accepts this value but emits no rows for an unknown type.",
            )
        )

    attributes = data.get("attributes")
    if isinstance(attributes, dict) and len(attributes) > 5:
        warnings.append(
            ValidationIssue(
                "$.attributes",
                "woo_attribute_limit",
                "Woo-style rows emit only the first five attributes.",
            )
        )
    modifiers = data.get("variation_modifiers")
    if isinstance(modifiers, dict) and any(
        isinstance(value, dict) and "sale_price" in value
        for value in modifiers.values()
    ):
        warnings.append(
            ValidationIssue(
                "$.variation_modifiers",
                "modifier_sale_price_not_emitted",
                "The current variation row builder does not emit modifier sale_price.",
            )
        )
    return warnings


def validate_product_info(data, kind):
    """Validate editor input without changing scanner-wide loading behavior."""

    if kind not in SCHEMA_NAMES:
        raise ValueError(f"Unsupported metadata kind: {kind}")
    if not isinstance(data, dict):
        return ValidationResult(
            (
                ValidationIssue(
                    "$", "non_object_root", "product_info.json must contain an object."
                ),
            ),
            (),
        )
    errors = tuple(_schema_errors(data, kind))
    if not errors and 'shopify_status' in data and 'live' in data and data['live'] != (data['shopify_status']=='ACTIVE'):
        errors += (ValidationIssue('$.shopify_status','conflicting_publication_intent','Shopify status and legacy live must agree within this authored layer.'),)
    if not errors:
        from app.sale_pricing import schedule
        try: schedule(data, partial=kind == 'override')
        except ValueError as exc:
            errors += (ValidationIssue('$.sale_start_date','invalid_sale_schedule',str(exc)),)
    if not errors:
        from app.catalogue_fields import validate_extensions
        try:
            validate_extensions(data, partial=kind == "override")
        except ValueError as error:
            errors += (ValidationIssue("$.customisation", "invalid_catalogue_extension", str(error)),)
    if "variation_attributes" in data and not errors and (kind == "collection" or "attributes" in data):
        from app.taxonomy_assignments import variation_drivers
        try:
            drivers=variation_drivers(data)
            if any(not data.get("attributes",{}).get(name) for name in drivers):
                raise ValueError("Variation-driving attributes require nonempty values; only informational assignments can be explicitly cleared.")
        except ValueError as error:
            errors += (ValidationIssue("$.variation_attributes", "invalid_variation_drivers", str(error)),)
    warnings = tuple(_warnings(data, kind))
    return ValidationResult(errors, warnings)
