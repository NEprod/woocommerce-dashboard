from app.utils.scan_runner import image_ownership_summary


def test_image_ownership_summary_counts_unique_parent_and_variation_records():
    rows = [
        {"Type": "variable", "SKU": "PARENT", "Images": "https://uploads.invalid/parent-one.webp, https://uploads.invalid/gallery.webp"},
        {"Type": "variation", "SKU": "VAR-1", "Parent": "PARENT", "Images": "https://uploads.invalid/variation-one.webp, https://uploads.invalid/variation-two.webp"},
        {"Type": "variation", "SKU": "VAR-2", "Parent": "PARENT", "Images": "https://uploads.invalid/variation-two.webp, https://uploads.invalid/variation-three.webp"},
    ]
    assert image_ownership_summary(rows) == {
        "parent_images": 2,
        "variation_images": 3,
        "total_images": 5,
        "output_images_copied": 5,
    }


def test_image_ownership_summary_does_not_count_parent_fallback_twice():
    rows = [
        {"Type": "variable", "SKU": "PARENT", "Images": "https://uploads.invalid/shared.webp"},
        {"Type": "variation", "SKU": "VAR-1", "Parent": "PARENT", "Images": "https://uploads.invalid/shared.webp"},
    ]
    assert image_ownership_summary(rows) == {
        "parent_images": 1,
        "variation_images": 0,
        "total_images": 1,
        "output_images_copied": 1,
    }
