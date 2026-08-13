from app.utils.json_utils import merge_product_json, validate_json


def test_override_scalars_and_merge_lists_without_mutating_inputs():
    shared = {
        "sku_prefix": "FIC-",
        "title": "Shared Title",
        "price": "10.00",
        "tags": ["shared", "common"],
    }
    override = {"title": "Override Title", "price": "12.00", "tags": ["extra"]}

    merged = merge_product_json(shared, override)

    assert merged["title"] == "Override Title - Shared Title"
    assert merged["price"] == "12.00"
    assert set(merged["tags"]) == {"shared", "common", "extra"}
    assert shared["price"] == "10.00"


def test_validation_requires_collection_contract_and_normalizes_dimensions():
    data = validate_json(
        {"collection_type": "Simple", "sku_prefix": "FIC-", "dimensions": {"length": 1}},
        is_collection=True,
    )

    assert data["title"] is None
    assert data["dimensions"] == {"length": 1, "width": "", "height": ""}
