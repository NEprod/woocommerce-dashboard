import json
import os

"""
json_utils.py

Handles JSON-based utilities for scanning, merging, editing, and modifying product data.

Responsibilities:
- Loading and validating product_info.json files
- Merging shared and override product metadata
- Supporting variation modifier lookup by attribute keys
- Generating consistent lookup keys for attributes
- Formatting and saving editor-friendly JSON
"""

# =========================
# GENERAL JSON UTILITIES
# =========================

def is_valid_json(raw_text):
    """
    Checks if a string contains valid JSON.

    Args:
        raw_text (str): Raw text to validate

    Returns:
        bool: True if valid JSON, False otherwise
    """
    try:
        json.loads(raw_text)
        return True
    except json.JSONDecodeError:
        return False

def format_json_for_saving(data):
    """
    Formats a dictionary as pretty-printed JSON string.
    Placeholder for future normalization.

    Args:
        data (dict): JSON-compatible dictionary

    Returns:
        dict: Cleaned data ready to write as JSON
    """
    return data

def load_json(path):
    """
    Loads a JSON file from disk if it exists.

    Args:
        path (str): Full path to the JSON file

    Returns:
        dict: Parsed JSON contents or empty dict if file does not exist
    """

    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    """
    Writes a dictionary to a JSON file.

    Args:
        path (str): Path to output file
        data (dict): JSON-compatible dictionary
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# =========================
# SCAN + CSV SUPPORT
# =========================

def merge_product_json(shared, override, path=None):
    """
    Merges shared (collection-level) and override (product-level) product_info.json data.

    Lists like tags and categories are additive.
    Scalars like price/weight are overwritten.
    Title logic prioritizes: override - shared → folder - shared → shared → folder

    Args:
        shared (dict): The collection-level shared JSON data
        override (dict): The product-level override JSON data
        path (str, optional): Folder name for fallback title

    Returns:
        dict: Fully merged JSON with resolved title and combined data
    """

    result = dict(shared)

    for key, value in override.items():
        if isinstance(value, list) and isinstance(shared.get(key), list):
            result[key] = list(set(shared[key] + value))
        elif key == "title":
            continue  # Title handled separately
        else:
            result[key] = value

    shared_title = shared.get("title")
    override_title = override.get("title")
    folder_name = os.path.basename(path) if path else ""

    if override_title and shared_title:
        result["title"] = f"{override_title} - {shared_title}"
    elif override_title:
        result["title"] = override_title
    elif shared_title and folder_name:
        result["title"] = f"{folder_name} - {shared_title}"
    elif shared_title:
        result["title"] = shared_title
    elif folder_name:
        result["title"] = folder_name

    return result

def validate_json(data, is_collection=False):
    """
    Validates and normalizes a product_info.json dictionary.

    - Ensures required fields exist
    - Normalizes dimensions structure
    - Raises exceptions for critical issues

    Args:
        data (dict): Loaded product JSON data
        is_collection (bool): If True, requires 'collection_type'

    Returns:
        dict: Validated and normalized product JSON

    Raises:
        ValueError: If critical fields are missing or invalid
    """

    if not isinstance(data, dict):
        raise ValueError("Invalid product_info.json: not a JSON object")

    if is_collection and "collection_type" not in data:
        raise ValueError("Missing required field: collection_type")

    if "sku_prefix" not in data:
        raise ValueError("Missing required field: sku_prefix")

    if "title" not in data:
        data["title"] = None

    if "dimensions" in data:
        dims = data["dimensions"]
        if isinstance(dims, dict):
            dims.setdefault("length", "")
            dims.setdefault("width", "")
            dims.setdefault("height", "")
        else:
            data["dimensions"] = {"length": "", "width": "", "height": ""}

    return data

def json_key(attr_dict):
    """
    Generates a consistent key string from a dictionary of variation attributes.

    Attributes are sorted alphabetically and joined using pipe format.

    Example:
        {"Size": "A3", "Style": "Hero C"} → "Size=A3|Style=Hero C"

    Args:
        attr_dict (dict): Attribute dictionary

    Returns:
        str: Consistent lookup key
    """

    return "|".join(f"{k}={attr_dict[k]}" for k in sorted(attr_dict))

def apply_variation_modifiers(base_data, variation_attrs, log=print):
    """
    Applies price, weight, and dimension modifiers based on variation attribute matches.

    Uses an exact match first, then falls back to best partial match (longest key hit).

    Args:
        base_data (dict): Product data with optional 'variation_modifiers'
        variation_attrs (dict): Current variation's attributes
        log (function): Logging function (default: print)

    Returns:
        dict: Modified values for price, weight, dimensions, and sale_price
    """

    modifiers = base_data.get("variation_modifiers", {})
    result = {
        "price": base_data.get("price"),
        "sale_price": base_data.get("sale_price"),
        "weight": base_data.get("weight"),
        "dimensions": base_data.get("dimensions", {})
    }

    # 1. Try exact match first
    full_key = json_key(variation_attrs)
    if full_key in modifiers:
        mod = modifiers[full_key]
        log(f"🎯 Exact match for {variation_attrs} → using modifier '{full_key}'", level="INFO")
    else:
        # 2. Try best partial match
        mod = None
        best_key = None
        for key, value in modifiers.items():
            key_parts = dict(part.split("=") for part in key.split("|"))
            if all(variation_attrs.get(k) == v for k, v in key_parts.items()):
                if mod is None or len(key_parts) > len(best_key.split("|")):
                    mod = value
                    best_key = key
        if mod and best_key:
            log(f"🔁 Fallback match for {variation_attrs} → using modifier '{best_key}'", level="INFO")

    # 3. Apply modifiers
    if mod:
        if "price" in mod:
            result["price"] = mod["price"]
        if "sale_price" in mod:
            result["sale_price"] = mod["sale_price"]
        if "weight" in mod:
            result["weight"] = mod["weight"]
        if "dimensions" in mod:
            result["dimensions"] = mod["dimensions"]

    return result