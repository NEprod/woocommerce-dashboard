import json
from pathlib import Path

from app.utils.json_utils import apply_variation_modifiers
from app.utils.scanner import build_variations
from app.utils.sku_manager import ScannedVariationMatcher


FIXTURES = Path(__file__).parent / "fixtures"


def test_cartesian_product_and_modifiers():
    data = json.loads(
        (FIXTURES / "catalogue" / "Variable Collection" / "product_info.json").read_text()
    )
    variations = build_variations(data)

    assert len(variations) == 4
    assert {tuple(sorted(v.items())) for v in variations} == {
        (("Colour", "Amber"), ("Size", "Small")),
        (("Colour", "Amber"), ("Size", "Large")),
        (("Colour", "Teal"), ("Size", "Small")),
        (("Colour", "Teal"), ("Size", "Large")),
    }

    modified = apply_variation_modifiers(
        data, {"Colour": "Teal", "Size": "Large"}, log=lambda *args, **kwargs: None
    )
    assert modified["price"] == "12.00"
    assert modified["weight"] == 45
    assert modified["dimensions"] == {"length": 70, "width": 70, "height": 5}


def test_scanned_variation_matcher_reuses_exact_sku_once():
    scanned = json.loads((FIXTURES / "state" / ".scanned").read_text())
    matcher = ScannedVariationMatcher(scanned, log=lambda *args, **kwargs: None)

    assert matcher.match({"Colour": "Amber"}) == "FIC-V-0042-1"
    assert matcher.match({"Colour": "Amber"}) is None
