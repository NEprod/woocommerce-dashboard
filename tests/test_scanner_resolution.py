import json
import shutil
from pathlib import Path

from PIL import Image

from app.utils.file_markers import should_rescan
from app.utils.scanner import scan_collection


FIXTURES = Path(__file__).parent / "fixtures"


def _copy_collection(tmp_path, name):
    target = tmp_path / "catalogue" / name
    shutil.copytree(FIXTURES / "catalogue" / name, target)
    return target


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 3), "#336699").save(path)


def test_simple_scan_resolves_shared_override_and_row_shape(tmp_path, quiet_log):
    collection = _copy_collection(tmp_path, "Simple Collection")
    product = collection / "Blue Token"
    output = tmp_path / "output"
    output.mkdir()
    _image(product / "fictional-blue.png")

    rows = scan_collection(collection, "https://invalid.example/assets/", output, log=quiet_log)

    assert len(rows) == 1
    assert rows[0]["Type"] == "simple"
    assert rows[0]["SKU"] == "FIC-S-0001"
    assert rows[0]["Name"] == "Blue Token - Fictional Desk Token"
    assert rows[0]["Regular price"] == "9.25"
    assert set(rows[0]["Tags"].split(", ")) == {"baseline", "shared", "override"}
    assert rows[0]["Images"] == "https://invalid.example/assets/fictional-blue.webp"
    assert (product / ".scanned").exists()
    assert not (FIXTURES / "catalogue" / "Simple Collection" / "Blue Token" / ".scanned").exists()


def test_variable_and_single_variable_resolve_all_variations(tmp_path, quiet_log):
    output = tmp_path / "output"
    output.mkdir()

    variable = _copy_collection(tmp_path, "Variable Collection")
    product = variable / "Badge One"
    _image(product / "badge.png")
    variable_rows = scan_collection(variable, "https://invalid.example/assets/", output, log=quiet_log)
    assert [r["Type"] for r in variable_rows].count("variable") == 1
    assert [r["Type"] for r in variable_rows].count("variation") == 4

    single = _copy_collection(tmp_path, "Single Variable")
    _image(single / "parent" / "orbit-parent.png")
    for theme in ("Moon", "Sun"):
        _image(single / theme / "theme.png")
        for size in ("Mini", "Maxi"):
            _image(single / theme / size / f"{theme.lower()}-{size.lower()}.png")
    single_rows = scan_collection(single, "https://invalid.example/assets/", output, log=quiet_log)
    assert [r["Type"] for r in single_rows].count("variable") == 1
    assert [r["Type"] for r in single_rows].count("variation") == 4


def test_marker_selection_contract(tmp_path, quiet_log):
    folder = tmp_path / "product"
    folder.mkdir()
    assert should_rescan(folder, log=quiet_log)
    (folder / ".scanned").write_text("{}")
    assert not should_rescan(folder, log=quiet_log)
    (folder / ".update").write_text("fixture")
    assert should_rescan(folder, log=quiet_log)
    assert should_rescan(folder, force_update=True, log=quiet_log)
