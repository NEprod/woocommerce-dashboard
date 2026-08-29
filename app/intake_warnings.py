"""Bounded, presentation-safe Catalogue Intake warning summaries."""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import PurePosixPath


MAX_FINDINGS = 50
MAX_GROUPS = 12
MAX_AFFECTED = 3
MAX_TEXT = 300


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def blocking_count(summary, *, row=None):
    """Return the explicit blocking/failure count for navigation decisions."""

    summary = summary if isinstance(summary, dict) else {}
    values = [
        summary.get("blocking_errors"),
        summary.get("errors"),
        summary.get("failures"),
        summary.get("failed_images"),
    ]
    if row is not None:
        values.extend((row.products_failed, int(bool(row.error))))
    return max((_count(value) for value in values), default=0)


def _text(value, fallback="Warning"):
    rendered = re.sub(r"\s+", " ", str(value or fallback)).strip()
    return rendered[:MAX_TEXT] or fallback


def _category(code):
    code = str(code or "warning").casefold()
    if code.startswith("image_fallback") or "image" in code:
        return "Image fallback"
    if "publish" in code or code == "live" or code.startswith("live_"):
        return "Publishing intent"
    if "meta_description" in code or "meta_title" in code or "seo" in code:
        return "SEO"
    if "cleanup" in code or "staging" in code or "promotion" in code:
        return "Operation cleanup"
    if "attribute" in code:
        return "Attributes"
    return _text(code.replace("_", " ").title(), "General warning")


def _guidance(category):
    if category == "SEO":
        return "Optional.", "Add the optional SEO metadata if it is useful before publishing."
    if category == "Publishing intent":
        return "Safe to continue.", "Confirm the intended future WooCommerce publish or draft state."
    if category == "Image fallback":
        return "Safe to continue.", "Review the affected image ownership and fallback before handoff if needed."
    if category == "Operation cleanup":
        return "Safe to continue.", "Review the retained diagnostic if operational cleanup is required."
    return "Safe to continue.", "Review this warning when convenient."


def _affected(value):
    value = _text(value, "")
    if not value or value == "$" or value.startswith("/"):
        return None
    if value.startswith("$."):
        return value
    parts = PurePosixPath(value).parts
    if not parts or ".." in parts:
        return None
    rendered = PurePosixPath(*parts).as_posix()
    return rendered + "/" if value.endswith("/") else rendered


def warning_groups(findings, *, total=0):
    """Group structured warnings without producing unbounded operation payloads."""

    grouped = OrderedDict()
    for finding in list(findings or ())[:MAX_FINDINGS]:
        if not isinstance(finding, dict) or finding.get("state") == "blocking":
            continue
        category = _category(finding.get("code"))
        group = grouped.setdefault(
            category,
            {
                "category": category,
                "count": 0,
                "explanation": _text(finding.get("message")),
                "affected": [],
            },
        )
        group["count"] += 1
        affected = _affected(finding.get("path"))
        if affected and affected not in group["affected"] and len(group["affected"]) < MAX_AFFECTED:
            group["affected"].append(affected)

    rendered = []
    for group in list(grouped.values())[:MAX_GROUPS]:
        continuation, recommendation = _guidance(group["category"])
        group["continuation"] = continuation
        group["recommendation"] = recommendation
        group["more_count"] = max(0, group["count"] - len(group["affected"]))
        rendered.append(group)

    known = sum(group["count"] for group in rendered)
    remaining = max(0, _count(total) - known)
    if remaining and len(rendered) < MAX_GROUPS:
        rendered.append(
            {
                "category": "Additional warnings",
                "count": remaining,
                "explanation": "Additional bounded operation warnings were recorded without item-level detail.",
                "affected": [],
                "more_count": 0,
                "continuation": "Safe to continue.",
                "recommendation": "Review the operation log if more context is required.",
            }
        )
    return rendered


def bounded_warning_findings(findings):
    """Retain concise generated warning context without storing authored payloads."""

    retained = []
    for finding in list(findings or ())[:MAX_FINDINGS]:
        if not isinstance(finding, dict) or finding.get("state") == "blocking":
            continue
        item = {
            "state": "warning",
            "code": _text(finding.get("code"), "warning")[:80],
            "message": _text(finding.get("message")),
        }
        affected = _affected(finding.get("path"))
        if affected:
            item["path"] = affected
        retained.append(item)
    return retained


def warning_presentation(summary, *, status=None):
    """Normalise current and older persisted warning payloads for the UI."""

    summary = summary if isinstance(summary, dict) else {}
    count = max(_count(summary.get("warnings")), int(status == "partial"))
    findings = summary.get("warning_findings")
    groups = warning_groups(findings, total=count) if isinstance(findings, list) else []
    if not groups and isinstance(summary.get("warning_summary"), list):
        for item in summary["warning_summary"][:MAX_GROUPS]:
            if not isinstance(item, dict):
                continue
            category = _text(item.get("category"), "General warning")
            continuation, recommendation = _guidance(category)
            groups.append(
                {
                    "category": category,
                    "count": _count(item.get("count")) or 1,
                    "explanation": _text((item.get("samples") or [category])[0]),
                    "affected": [],
                    "more_count": 0,
                    "continuation": continuation,
                    "recommendation": recommendation,
                }
            )
    if not groups and count:
        groups = warning_groups([], total=count)
    return {
        "count": count,
        "groups": groups,
        "warning_count": count,
        "warning_groups": groups,
    }
