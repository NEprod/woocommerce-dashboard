"""Presentation helpers for future WooCommerce publishing intent."""

from __future__ import annotations


def projected_publishing_intent(value):
    """Describe the scanner-projected publication boolean without implying remote state."""

    if value is True:
        return {
            "state": "published",
            "label": "Published",
            "compact_label": "Published intent",
            "value": True,
            "value_text": "true",
        }
    if value is False:
        return {
            "state": "draft",
            "label": "Draft",
            "compact_label": "Draft intent",
            "value": False,
            "value_text": "false",
        }
    return {
        "state": "unresolved",
        "label": "Not projected",
        "compact_label": "Intent not projected",
        "value": None,
        "value_text": "not projected",
    }


def resolved_publishing_intent(shared, override, resolved):
    """Describe resolved ``live`` value, owning layer, and inheritance state."""

    value = resolved.get("live", True)
    result = projected_publishing_intent(value is not False)
    if "live" in override:
        result.update(
            source="Product override",
            inheritance="Overridden",
        )
    elif "live" in shared:
        result.update(
            source="Collection metadata",
            inheritance="Inherited",
        )
    else:
        result.update(
            source="Scanner default",
            inheritance="Default",
        )
    return result
