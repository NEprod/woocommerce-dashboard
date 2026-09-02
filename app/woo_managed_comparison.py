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


def _text(value):
    value = unescape(str(value or "")).translate(str.maketrans({
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ",
    }))
    return _SPACE.sub(" ", value).strip()


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
    source = str(value or "")
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

    authored = str(authored or "")
    observed = str(observed or "")
    if _text(authored) == _text(observed):
        return True
    local = _local_accordions(authored)
    remote = _remote_accordions(observed)
    return bool(local is not None and remote is not None and local == remote)
