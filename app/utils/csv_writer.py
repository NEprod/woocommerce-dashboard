import csv
import os

"""
csv_writer.py

Handles the export of product data into WooCommerce-compatible CSV format.

Responsibilities:
- Writes full or partial CSV files depending on scan mode
- Generates row dictionaries for simple, variable, and variation products
- Supports up to 5 attributes per variation
"""

# WooCommerce-compatible CSV column headers
CSV_HEADERS = [
    "Type", "SKU", "Name", "Parent", "Weight (g)", "Length (mm)", "Width (mm)", "Height (mm)",
    "Sale price", "Regular price", "Date sale price starts", "Date sale price ends",
    "Categories", "Tags", "Shipping class", "Images",
    "Grouped products", "Upsells", "Cross-sells", "Short description", "Description", "Published", "Is featured?",
    "Tax status", "Tax class", "In stock?", "Stock", "Backorders allowed?","Sold individually?",
    "Allow customer reviews?", "Purchase note", "Download limit", "Visibility in catalog",
    "Download expiry days",  "External URL", "Button text", "Position", "Meta: _yoast_wpseo_title",
    "Meta: _yoast_wpseo_metadesc",
    "Attribute 1 name", "Attribute 1 value(s)", "Attribute 1 visible", "Attribute 1 global",
    "Attribute 2 name", "Attribute 2 value(s)", "Attribute 2 visible", "Attribute 2 global",
    "Attribute 3 name", "Attribute 3 value(s)", "Attribute 3 visible", "Attribute 3 global",
    "Attribute 4 name", "Attribute 4 value(s)", "Attribute 4 visible", "Attribute 4 global",
    "Attribute 5 name", "Attribute 5 value(s)", "Attribute 5 visible", "Attribute 5 global"
]

def write_csv(file_path, rows, append=False, update=False, log=print):
    """
    Writes or updates a CSV file at the given path using WooCommerce-compatible format.

    Args:
        file_path (str): Path to the output CSV file
        rows (list[dict]): List of product row dictionaries
        append (bool): If True, appends rows to existing file
        update (bool): If True, updates rows based on SKU match
        log (function): Logging function for output visibility (default: print)

    Returns:
        None
    """

    if not rows:
        log("⚠️ No rows provided for CSV export.", level="WARN")
        return

    existing_rows = []
    file_exists = os.path.exists(file_path)

    # Read existing data if appending or updating
    if file_exists and (append or update):
        with open(file_path, newline='', encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)

    # Update mode: overwrite matching rows by SKU
    if update:
        sku_index = {row["SKU"]: i for i, row in enumerate(existing_rows)}
        for row in rows:
            sku = row.get("SKU")
            if sku in sku_index:
                existing_rows[sku_index[sku]] = row  # Update matching row
            else:
                existing_rows.append(row)  # Add new row
        output_rows = existing_rows
    elif append:
        output_rows = existing_rows + rows
    else:
        output_rows = rows

    # Confirm each row only contains expected headers
    for row in output_rows:
        unexpected_keys = [k for k in row.keys() if k not in CSV_HEADERS]
        if unexpected_keys:
            log(f"⚠️ Unexpected keys in row with SKU {row.get('SKU')}: {unexpected_keys}", level="WARN")

    # Write the final CSV
    if update:
        log("🔁 CSV Update Mode: Matching existing SKUs and replacing rows.", level="INFO")
    elif append:
        log("➕ CSV Append Mode: Adding new rows to existing file.", level="INFO")
    else:
        log("⚠️ CSV Overwrite Mode: Replacing all data in file.", level="WARN")
    with open(file_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(output_rows)
        log(f"✅ CSV export complete: {file_path} ({len(output_rows)} rows)", level="INFO")

def build_common_fields(data, image_urls):
    """
    Returns a dictionary of shared WooCommerce fields across product types.

    Args:
        data (dict): Product metadata
        image_urls (list[str]): List of image URLs

    Returns:
        dict: Base product row dictionary
    """

    return {
        "SKU": data.get("sku", ""),
        "Name": data.get("title", ""),
        "Published": "1" if data.get("live", True) else "0",
        "Is featured?": "0",
        "Visibility in catalog": "visible",
        "Short description": data.get("short_description", ""),
        "Description": data.get("description", ""),
        "Tax status": "taxable",
        "Tax class": "",
        "In stock?": "1",
        "Stock": "",
        "Backorders allowed?": "0",
        "Sold individually?": "0",
        "Weight (g)": data.get("weight", ""),
        "Length (mm)": data.get("dimensions", {}).get("length", ""),
        "Width (mm)": data.get("dimensions", {}).get("width", ""),
        "Height (mm)": data.get("dimensions", {}).get("height", ""),
        "Allow customer reviews?": "1",
        "Purchase note": "",
        "Sale price": data.get("sale_price", ""),
        "Regular price": data.get("price", "0.00"),
        "Date sale price starts": data.get("sale_start_date", ""),
        "Date sale price ends": data.get("sale_end_date", ""),
        "Categories": ", ".join(data.get("categories", [])),
        "Tags": ", ".join(data.get("tags", [])),
        "Shipping class": "",
        "Images": ", ".join(image_urls),
        "Download limit": "",
        "Download expiry days": "",
        "Grouped products": "",
        "Upsells": ", ".join(data.get("upsells", [])),
        "Cross-sells": ", ".join(data.get("crosssells", [])),
        "External URL": "",
        "Button text": "",
        "Position": "",
        "Meta: _yoast_wpseo_title": data.get("meta_title", ""),
        "Meta: _yoast_wpseo_metadesc": data.get("meta_description", "")
    }

def build_simple_product(data, image_urls):
    """
    Builds a row for a simple product.

    Args:
        data (dict): Product details
        image_urls (list[str]): Image paths or URLs

    Returns:
        dict: CSV-ready row
    """
    row = build_common_fields(data, image_urls)
    row["Type"] = "simple"
    return row

def build_variable_parent(data, image_urls):
    """
    Builds a row for a variable product parent.

    Args:
        data (dict): Product metadata
        image_urls (list[str]): Image paths or URLs

    Returns:
        dict: CSV row for parent product
    """

    row = build_common_fields(data, image_urls)
    row["Type"] = "variable"

    attributes = data.get("attributes", {})
    for i, (attr_name, values) in enumerate(attributes.items(), start=1):
        if i > 5:
            break  # WooCommerce supports max 5 attributes in CSV
        row[f"Attribute {i} name"] = attr_name
        row[f"Attribute {i} value(s)"] = ", ".join(values)
        row[f"Attribute {i} visible"] = "1"
        row[f"Attribute {i} global"] = "1"
        row["Position"] = "0"

    return row

def build_variation_row(base_data, variation_data, image_urls, suffix_num, override_sku=None):
    """
    Builds a variation row from the base product and variation metadata.

    Args:
        base_data (dict): Parent product info
        variation_data (dict): Attributes and overrides
        image_urls (list[str]): Image list
        suffix_num (int): Variation number for SKU suffix
        override_sku (str, optional): Use this SKU instead of auto-generated

    Returns:
        dict: CSV row for a variation product
    """

    row = build_common_fields(base_data, image_urls)
    row["Type"] = "variation"
    row["SKU"] = override_sku or f"{base_data['sku']}-{suffix_num}"
    row["Parent"] = base_data.get("sku", "")

    # Apply modifiers if present
    modifiers = variation_data.get("modifiers", {})
    if modifiers:
        if "price" in modifiers:
            row["Regular price"] = modifiers["price"]
        if "weight" in modifiers:
            row["Weight (g)"] = modifiers["weight"]
        if "dimensions" in modifiers:
            dims = modifiers["dimensions"]
            row["Length (mm)"] = dims.get("length", "")
            row["Width (mm)"] = dims.get("width", "")
            row["Height (mm)"] = dims.get("height", "")

    # Add attribute columns (up to 5)
    for i, (attr_name, value) in enumerate(variation_data.get("attributes", {}).items(), start=1):
        row[f"Attribute {i} name"] = attr_name
        row[f"Attribute {i} value(s)"] = value
        row[f"Attribute {i} global"] = "1"

    return row