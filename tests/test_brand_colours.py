"""Brand refresh guardrails, without changing operational/status semantics."""
from pathlib import Path
import re

import pytest

CSS = (Path(__file__).resolve().parents[1] / "app/static/assets/css/custom.css").read_text()
TOKENS = dict(re.findall(r"(--[\w-]+):\s*(#[\da-fA-F]{6});", CSS))


def contrast(a, b):
    def luminance(value):
        channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    x, y = sorted((luminance(a), luminance(b)))
    return (y + .05) / (x + .05)


@pytest.mark.parametrize("token,value", {
    "--color-canvas": "#F8F3E7",
    "--color-brand-forest": "#1F3D2E",
    "--color-brand-text": "#2E2E2E",
    "--color-brand-brass": "#B08D57",
    "--color-brand-gold": "#D4B574",
    "--color-surface-primary": "#FFFFFF",
}.items())
def test_brand_palette_keeps_white_panels(token, value):
    assert TOKENS[token] == value


@pytest.mark.parametrize("name,values", {
    "lime": ("#9BDC32", "#EAF7D3", "#365F08"),
    "teal": ("#299DA8", "#E0F3F4", None),
    "warning": ("#E89A25", "#FFF1D8", "#754600"),
    "error": ("#C43F50", "#FCE8EB", "#8C2534"),
    "success": ("#43A652", "#E5F5E8", "#246B30"),
    "info": ("#347DBB", "#E7F2FB", "#205A89"),
}.items())
def test_semantic_palette_is_unchanged(name, values):
    for suffix, value in zip(("", "-soft", "-ink"), values):
        if value is not None:
            assert TOKENS[f"--color-{name}{suffix}"] == value


@pytest.mark.parametrize("foreground,background", [
    ("--color-text-inverse", "--color-brand-forest"),
    ("--color-text-inverse", "--color-brand-forest-hover"),
    ("--color-canvas", "--color-brand-forest"),
    ("--color-brand-text", "--color-canvas"),
    ("--color-brand-text", "--color-brand-gold"),
    ("--color-brand-gold", "--color-brand-forest"),
    ("--color-brand-muted", "--color-canvas"),
    ("--color-brand-forest", "--color-surface-primary"),
    ("--color-text-muted", "--color-surface-primary"),
])
def test_brand_text_and_placeholder_contrast(foreground, background):
    assert contrast(TOKENS[foreground], TOKENS[background]) >= 4.5


def test_brass_is_decorative_not_small_text_on_cream():
    assert contrast(TOKENS["--color-brand-brass"], TOKENS["--color-canvas"]) < 4.5
    assert not re.search(r"(?<![-\w])color:\s*var\(--color-brand-brass\)", CSS)


def test_shared_navy_tokens_remain_only_for_existing_semantic_mark():
    # Changing this shared ink would recolour the existing readiness checkmark.
    consumers = [line for line in CSS.splitlines() if "var(--color-surface-dark)" in line]
    assert len(consumers) == 1
    assert consumers[0].startswith(".readiness-mark")
    assert TOKENS["--color-surface-dark"] == "#10262D"


def test_status_badges_do_not_consume_decorative_brand_tokens():
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if re.search(r"\.(?:catalogue-pill|status-pill|status-badge|operation-status|alert)(?:[. :,{]|$)", selector):
            assert "var(--color-brand-" not in body
