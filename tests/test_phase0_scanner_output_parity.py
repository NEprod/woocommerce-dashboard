import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image


FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"
URL_PREFIX = "https://invalid.example/parity/"

# Frozen from commit d757db7f8631554d0f554e0c7997d7f679b2a9fa using
# _scenario_outputs below. Categories and Tags are canonicalized because Phase 0
# intentionally constructs merged lists through a set and does not promise order.
PHASE0_SCENARIO_SHA256 = {
    "intentional_full": "837ae63bb57e88f83c8e35a967036eecc55c5ebb2420af07869fc01395268bf5",
    "product_update": "830ed8c5470eb6e14a81c4c216c9b1644f817d8f3ee85afd877ba67ff841a053",
    "shared_override": "ae4a2c0c35038ca41ad2f9c10ecb03d0a328f08cf7e526055327a812424d8873",
    "simple_append": "ae4a2c0c35038ca41ad2f9c10ecb03d0a328f08cf7e526055327a812424d8873",
    "single_variable_append": "aaa22b1e4c3237241ccdeb05675c19debcb99b1985b7e62c76263aa80cce4552",
    "variable_append": "837ae63bb57e88f83c8e35a967036eecc55c5ebb2420af07869fc01395268bf5",
}


def _quiet_log(*_args, **_kwargs):
    return None


def _image(path, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3, 5), colour).save(path)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _prepare_collection(root, name):
    collection = root / name
    shutil.copytree(FIXTURES / name, collection)
    shared_path = collection / "product_info.json"
    shared = json.loads(shared_path.read_text(encoding="utf-8"))
    shared.update(
        {
            "sale_price": "7.75",
            "sale_start_date": "2026-02-03",
            "sale_end_date": "2026-03-04",
            "short_description": "Fictional parity short description.",
            "description": "Fictional parity long description.",
        }
    )
    if name != "Simple Collection":
        shared["variation_modifiers"].update(
            {
                next(iter(shared["variation_modifiers"])): {
                    "price": "21.50",
                    "sale_price": "6.25",
                    "weight": 91,
                    "dimensions": {"length": 81, "width": 71, "height": 6},
                }
            }
        )
    _write_json(shared_path, shared)

    if name == "Simple Collection":
        product = collection / "Blue Token"
        override_path = product / "product_info.json"
        override = json.loads(override_path.read_text(encoding="utf-8"))
        override.update(
            {
                "title": "Blue Override",
                "price": "9.25",
                "description": "Product override description.",
            }
        )
        _write_json(override_path, override)
        _image(product / "simple-parity.png", "#336699")
    elif name == "Variable Collection":
        product = collection / "Badge One"
        override_path = product / "product_info.json"
        override = json.loads(override_path.read_text(encoding="utf-8"))
        override.update({"title": "Badge Override", "meta_title": "Parity badge"})
        _write_json(override_path, override)
        _image(product / "variable-parity.png", "#996633")
    else:
        _image(collection / "parent" / "single-parent.png", "#663399")
        for theme in ("Moon", "Sun"):
            _image(collection / theme / "theme.png", "#224466")
            for size in ("Mini", "Maxi"):
                _image(
                    collection / theme / size / f"{theme.lower()}-{size.lower()}.png",
                    "#446622",
                )
    return collection


def _scan(collection, output, **kwargs):
    from app.utils.scanner import scan_collection

    output.mkdir(parents=True, exist_ok=True)
    return scan_collection(
        str(collection),
        URL_PREFIX,
        str(output),
        log=_quiet_log,
        **kwargs,
    )


def _scenario_outputs(tmp_path):
    scenarios = {}

    simple = _prepare_collection(tmp_path / "simple", "Simple Collection")
    scenarios["simple_append"] = _scan(simple, tmp_path / "simple-output")

    variable = _prepare_collection(tmp_path / "variable", "Variable Collection")
    scenarios["variable_append"] = _scan(variable, tmp_path / "variable-output")

    single = _prepare_collection(tmp_path / "single", "Single Variable")
    scenarios["single_variable_append"] = _scan(single, tmp_path / "single-output")

    shared = _prepare_collection(tmp_path / "shared", "Simple Collection")
    scenarios["shared_override"] = _scan(shared, tmp_path / "shared-output")

    update = _prepare_collection(tmp_path / "update", "Variable Collection")
    _scan(update, tmp_path / "update-output")
    product = update / "Badge One"
    override_path = product / "product_info.json"
    override = json.loads(override_path.read_text(encoding="utf-8"))
    override.update({"title": "Updated Badge Override", "price": "14.25"})
    _write_json(override_path, override)
    (product / ".update").write_text("fixture", encoding="utf-8")
    scenarios["product_update"] = _scan(
        update,
        tmp_path / "update-output",
        update_csv=True,
    )

    full = _prepare_collection(tmp_path / "full", "Variable Collection")
    _scan(full, tmp_path / "full-output")
    _write_json(full / "sku_index.json", {"counter": 77})
    _write_json(full / "Badge One" / "sku_index.json", {"variation_counter": 88})
    scenarios["intentional_full"] = _scan(
        full,
        tmp_path / "full-output",
        force_update=True,
    )
    return scenarios


def _canonical_rows(rows):
    normalized = []
    for source in rows:
        row = dict(source)
        for field in ("Categories", "Tags"):
            if row.get(field):
                row[field] = ", ".join(sorted(row[field].split(", ")))
        normalized.append(row)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _scenario_hashes(tmp_path):
    return {
        name: hashlib.sha256(_canonical_rows(rows).encode("utf-8")).hexdigest()
        for name, rows in _scenario_outputs(tmp_path).items()
    }


def test_complete_scanner_rows_match_frozen_phase0_outputs(tmp_path):
    scenarios = _scenario_outputs(tmp_path)
    hashes = {
        name: hashlib.sha256(_canonical_rows(rows).encode("utf-8")).hexdigest()
        for name, rows in scenarios.items()
    }
    assert hashes == PHASE0_SCENARIO_SHA256

    simple = scenarios["simple_append"][0]
    assert simple["Type"] == "simple"
    assert simple["SKU"] == "FIC-S-0001"
    assert simple["Name"] == "Blue Override - Fictional Desk Token"
    assert simple["Regular price"] == "9.25"
    assert simple["Sale price"] == "7.75"
    assert simple["Date sale price starts"] == "2026-02-03"
    assert simple["Date sale price ends"] == "2026-03-04"
    assert simple["Weight (g)"] == 25
    assert (simple["Length (mm)"], simple["Width (mm)"], simple["Height (mm)"]) == (
        40,
        40,
        3,
    )
    assert simple["Short description"] == "Fictional parity short description."
    assert simple["Description"] == "Product override description."
    assert simple["Images"] == f"{URL_PREFIX}simple-parity.webp"
    assert set(simple["Tags"].split(", ")) == {"baseline", "shared", "override"}

    variable = scenarios["variable_append"]
    parent = next(row for row in variable if row["Type"] == "variable")
    variations = [row for row in variable if row["Type"] == "variation"]
    assert parent["SKU"] == "FIC-V-0001"
    assert len(variations) == 4
    assert {row["SKU"] for row in variations} == {
        "FIC-V-0001-1",
        "FIC-V-0001-2",
        "FIC-V-0001-3",
        "FIC-V-0001-4",
    }
    assert {row["Parent"] for row in variations} == {parent["SKU"]}
    large = [row for row in variations if row["Attribute 2 value(s)"] == "Large"]
    assert len(large) == 2
    for row in large:
        assert row["Regular price"] == "21.50"
        # Protected Phase 0 discrepancy: modifier sale_price is not emitted.
        assert row["Sale price"] == "7.75"
        assert row["Weight (g)"] == 91
        assert (row["Length (mm)"], row["Width (mm)"], row["Height (mm)"]) == (
            81,
            71,
            6,
        )

    single = scenarios["single_variable_append"]
    assert [row["Type"] for row in single].count("variable") == 1
    assert [row["Type"] for row in single].count("variation") == 4
    assert {row["Parent"] for row in single if row["Type"] == "variation"} == {
        "FIC-O-0001"
    }
    assert all(row["Images"] for row in single)

    assert [row["SKU"] for row in scenarios["product_update"]] == [
        row["SKU"] for row in variable
    ]
    assert [row.get("Parent") for row in scenarios["product_update"]] == [
        row.get("Parent") for row in variable
    ]
    assert [row["SKU"] for row in scenarios["intentional_full"]] == [
        row["SKU"] for row in variable
    ]
    assert scenarios["shared_override"][0] == scenarios["simple_append"][0]
