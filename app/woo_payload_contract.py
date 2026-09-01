"""Canonical WooCommerce payload scalar contracts shared by preview and publish."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re


DIMENSION_FIELDS = ("length", "width", "height")
_DECIMAL_TEXT = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")


class WooDimensionContractError(ValueError):
    """A dimension cannot be represented by the bounded Woo payload contract."""


def canonical_woo_dimension(value):
    """Return one Woo dimension as a canonical non-scientific JSON string."""

    if value is None:
        return ""
    if isinstance(value, bool):
        raise WooDimensionContractError("Boolean values are not valid WooCommerce dimensions.")
    if isinstance(value, str):
        source = value.strip()
        if source == "":
            return ""
        if not _DECIMAL_TEXT.fullmatch(source):
            raise WooDimensionContractError("WooCommerce dimensions must be plain decimal values.")
    elif isinstance(value, (int, float, Decimal)):
        source = str(value)
    else:
        raise WooDimensionContractError("WooCommerce dimensions must be numeric values or numeric strings.")
    try:
        number = Decimal(source)
    except (InvalidOperation, ValueError) as error:
        raise WooDimensionContractError("WooCommerce dimensions must be plain decimal values.") from error
    if not number.is_finite():
        raise WooDimensionContractError("WooCommerce dimensions must be finite decimal values.")
    if number == 0:
        return "0"
    result = format(number, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


def canonical_woo_dimensions(dimensions):
    """Return the complete Woo dimensions object with canonical string values."""

    if dimensions is None:
        dimensions = {}
    if not isinstance(dimensions, dict):
        raise WooDimensionContractError("WooCommerce dimensions must be an object.")
    return {
        field: canonical_woo_dimension(dimensions.get(field))
        for field in DIMENSION_FIELDS
    }


def assert_woo_dimension_payload(payload):
    """Refuse outgoing numeric or non-canonical Woo dimension fields."""

    if not isinstance(payload, dict) or "dimensions" not in payload:
        return
    dimensions = payload["dimensions"]
    if not isinstance(dimensions, dict):
        raise WooDimensionContractError("Outgoing WooCommerce dimensions must be an object.")
    for field in DIMENSION_FIELDS:
        if field not in dimensions:
            continue
        value = dimensions[field]
        if not isinstance(value, str):
            raise WooDimensionContractError(
                f"Outgoing WooCommerce dimensions.{field} must be a JSON string."
            )
        if canonical_woo_dimension(value) != value:
            raise WooDimensionContractError(
                f"Outgoing WooCommerce dimensions.{field} is not canonical."
            )
