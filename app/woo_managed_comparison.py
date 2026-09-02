"""Conservative semantic comparison for Woo-managed rich text.

Catalogue strings remain authoritative.  This module never renders or executes
shortcodes; it only recognises the one deterministic ``cg_accordion`` wrapper
that the application currently authors and compares its structure with the
HTML returned by WordPress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import re


_ACCORDION = re.compile(
    r"\[cg_accordion\s+title\s*=\s*(['\"])(.*?)\1\](.*?)\[/cg_accordion\]",
    re.IGNORECASE | re.DOTALL,
)
_SPACE = re.compile(r"\s+")
_TRANSPARENT = {"html", "body", "p"}
_MEANINGFUL = {"a", "br", "em", "li", "ol", "strong", "ul"}
_ESCAPED_DELIMITERS = re.compile(r"\\([<>])")


def _text(value):
    value = unescape(str(value or "")).translate(str.maketrans({
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ",
    }))
    return _SPACE.sub(" ", value).strip()


def _comparison_source(value):
    """Normalise representation-only source escaping for comparison.

    Catalogue content remains exactly as authored.  Some historical authored
    JSON escaped literal HTML delimiters, while WordPress stores the same
    shortcode body with ordinary delimiters.  This is intentionally confined
    to the comparison input; neither side is written back or rendered.
    """

    return _ESCAPED_DELIMITERS.sub(r"\1", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")


def managed_title_equal(authored, observed):
    """Compare plain product titles without treating HTML entity encoding as drift.

    This deliberately does not remove markup, punctuation, or words.  It only
    decodes the representation Woo may use for the same title text.
    """

    return unescape(str(authored or "")) == unescape(str(observed or ""))


def managed_taxonomy_membership_equal(expected, observed):
    """Compare category/tag membership by unique verified numeric IDs.

    Woo does not guarantee category or tag response ordering.  Duplicates or
    malformed identity rows remain unequal rather than being silently hidden.
    """

    if not isinstance(expected, list) or not isinstance(observed, list):
        return expected == observed

    def identities(rows):
        values = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int) or row["id"] <= 0:
                return None
            values.append(row["id"])
        return values if len(values) == len(set(values)) else None

    left, right = identities(expected), identities(observed)
    return left is not None and right is not None and set(left) == set(right)


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(str(tag).casefold(), {str(k).casefold(): str(v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in {"br", "img", "hr", "meta", "link", "input"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == str(tag).casefold():
            self.stack.pop()

    def handle_endtag(self, tag):
        wanted = str(tag).casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == wanted:
                del self.stack[index:]
                return

    def handle_data(self, data):
        value = _text(data)
        if value:
            self.stack[-1].children.append(value)


def _parse(value):
    parser = _TreeParser()
    parser.feed(str(value or ""))
    parser.close()
    return parser.root


def _serialise_child(child):
    if isinstance(child, str):
        return ("text", child)
    tag = {"b": "strong", "i": "em"}.get(child.tag, child.tag)
    children = tuple(item for item in (_serialise_child(row) for row in child.children) if item is not None)
    classes = {part.casefold() for part in child.attrs.get("class", "").split()}
    if tag in _TRANSPARENT or (tag == "div" and "cg-accordion-item" in classes):
        return ("transparent", children)
    if tag not in _MEANINGFUL:
        return ("transparent", children)
    attrs = (("href", unescape(child.attrs.get("href", "")).strip()),) if tag == "a" else ()
    return (tag, attrs, children)


def _flatten_transparent(items):
    result = []
    for item in items:
        if item and item[0] == "transparent":
            result.extend(_flatten_transparent(item[1]))
        elif item is not None:
            result.append(item)
    return tuple(result)


def _fragment(value):
    root = _parse(value)
    return _flatten_transparent(tuple(_serialise_child(child) for child in root.children))


def _node_text(node):
    values = []
    for child in node.children:
        if isinstance(child, str):
            values.append(child)
        else:
            values.append(_node_text(child))
    return _text(" ".join(values))


def _local_accordions(value):
    source = _comparison_source(value)
    matches = list(_ACCORDION.finditer(source))
    if not matches:
        return None
    remainder = _ACCORDION.sub("", source)
    if remainder.strip():
        return None
    return tuple((_text(match.group(2)), _fragment(match.group(3))) for match in matches)


def _remote_accordions(value):
    root = _parse(value)
    details = []

    def visit(node):
        if node.tag == "details":
            summary = next((child for child in node.children if isinstance(child, _Node) and child.tag == "summary"), None)
            if summary is None:
                return
            body = [child for child in node.children if child is not summary]
            serialised = _flatten_transparent(tuple(_serialise_child(child) for child in body))
            details.append((_node_text(summary), serialised))
            return
        for child in node.children:
            if isinstance(child, _Node):
                visit(child)

    visit(root)
    return tuple(details) if details else None


def managed_rich_text_equal(authored, observed):
    """Compare authored text with raw or known shortcode-rendered Woo text."""

    authored = _comparison_source(authored)
    observed = _comparison_source(observed)
    if _text(authored) == _text(observed):
        return True
    local = _local_accordions(authored)
    if local is None:
        return False
    # ``context=edit`` can return raw shortcode source.  Raw still needs the
    # same structural comparison as rendered HTML, because WordPress may have
    # normalised entities or delimiter escaping without changing meaning.
    remote_raw = _local_accordions(observed)
    if remote_raw is not None:
        return local == remote_raw
    remote_rendered = _remote_accordions(observed)
    return bool(remote_rendered is not None and local == remote_rendered)


def _attribute_slug(value):
    slug = "-".join(str(value or "").strip().casefold().replace("_", "-").split())
    return slug[3:] if slug.startswith("pa-") else slug


def _attribute_options(value):
    return frozenset(_text(item) for item in value or [] if _text(item))


def managed_parent_attributes_equal(expected, observed, *, known_attribute_ids=None):
    """Compare the authored Variable-parent attribute contract safely.

    Woo decorates verified global attributes with IDs, ``pa_`` slugs and a
    default position.  Those are transport representation, not authored
    state.  A local attribute is accepted only when it has a verified Woo ID
    (in the payload or supplied taxonomy map); unknown taxonomy identity stays
    strict rather than being guessed from display text.
    """

    if not isinstance(expected, list) or not isinstance(observed, list):
        return expected == observed
    identifiers = {
        _attribute_slug(name): value
        for name, value in (known_attribute_ids or {}).items()
        if isinstance(value, int) and value > 0
    }
    if len(expected) != len(observed):
        return False
    unmatched = [row for row in observed if isinstance(row, dict)]
    if len(unmatched) != len(observed):
        return False
    for local in expected:
        if not isinstance(local, dict):
            return False
        expected_id = local.get("id") or identifiers.get(_attribute_slug(local.get("name")))
        if not isinstance(expected_id, int) or expected_id <= 0:
            return False
        candidates = [
            remote for remote in unmatched
            if remote.get("id") == expected_id
            and _attribute_slug(remote.get("name") or remote.get("slug")) == _attribute_slug(local.get("name"))
        ]
        if len(candidates) != 1:
            return False
        remote = candidates[0]
        if (
            _attribute_options(local.get("options")) != _attribute_options(remote.get("options"))
            or bool(local.get("variation")) != bool(remote.get("variation"))
            or bool(local.get("visible")) != bool(remote.get("visible"))
        ):
            return False
        unmatched.remove(remote)
    return not unmatched
