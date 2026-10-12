"""Approved WD assets and the shared responsive brand component."""
from pathlib import Path
import xml.etree.ElementTree as ET

from flask import Flask, render_template

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "app/static/assets/img"
SVG = "{http://www.w3.org/2000/svg}"


def test_vector_variants_share_mark_and_theme_palette():
    full = ET.parse(IMG / "woocommerce-dashboard-logo.svg").getroot()
    icon = ET.parse(IMG / "woocommerce-dashboard-icon.svg").getroot()
    css = (ROOT / "app/static/assets/css/custom.css").read_text()
    for document in (full, icon):
        assert document.find(SVG + "title") is not None
        assert not document.findall(".//" + SVG + "image")
        assert not document.findall(".//" + SVG + "linearGradient")
        for node in document.iter():
            if node.get("fill"):
                assert node.get("fill") in css
    for tag in ("path", "rect"):
        assert [n.attrib for n in full.iter(SVG + tag)] == [
            n.attrib for n in icon.iter(SVG + tag)]
    assert [n.text for n in full.iter(SVG + "text")] == ["WooCommerce", "Dashboard"]


def test_shared_brand_has_reserved_dimensions_and_accessible_variants():
    app = Flask(__name__, template_folder=str(ROOT / "app/templates"))
    with app.test_request_context():
        html = render_template("includes/brand.html")  # Macro definition is side-effect free.
        assert "<img" not in html
        from flask import render_template_string
        html = render_template_string(
            "{% from 'includes/brand.html' import brand %}{{ brand(responsive=true) }}")
    assert 'brand-wordmark">TLCDashboard' in html
    assert 'aria-label="TLCDashboard">TLC' in html
    assert "WooCommerce Dashboard" not in html
    assert "brand-monogram" in html


def test_collapsed_and_tablet_brand_keep_wordmark_out_of_rail():
    css = (ROOT / "app/static/assets/css/custom.css").read_text()
    assert ".is-sidebar-collapsed .sidebar-brand .brand-full { display: none; }" in css
    assert ".is-sidebar-collapsed .sidebar-brand .brand-icon { display: block; }" in css
    tablet = css.split("@media (min-width: 768px) and (max-width: 1199.98px)")[1]
    assert ".sidebar-brand .brand-full { display: none; }" in tablet
    assert ".sidebar-brand .brand-icon { display: block; }" in tablet
