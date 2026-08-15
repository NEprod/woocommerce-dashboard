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
    return _load_json(SCHEMA_ROOT / f"{name}.schema.json")


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
    if kind == "override":
        for key in ("categories", "tags"):
            if key in data:
                warnings.append(
                    ValidationIssue(
                        f"$.{key}",
                        "nondeterministic_list_order",
                        "Shared and override lists deduplicate through a set; output order may vary.",
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
    warnings = tuple(_warnings(data, kind))
    return ValidationResult(errors, warnings)
