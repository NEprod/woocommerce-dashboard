import os
from .json_utils import (
    load_json,
    merge_product_json,
    validate_json,
    apply_variation_modifiers,
)
from .sku_manager import (
    generate_sku,
    ScannedVariationMatcher,
    get_next_variation_counter,
    set_variation_counter,
)
from .file_markers import (
    load_pending_scanned,
    load_scanned,
    should_rescan,
    write_scanned,
)
from .image_tools import process_images, get_image_csv_urls
from .csv_writer import build_simple_product, build_variable_parent, build_variation_row

"""
scanner.py

Handles scanning of product folders based on collection type and scan mode.
Manages SKU generation, file validation, and prepares data for CSV export.
Supports Simple, Single Variable, and Variable Collection formats.
"""


def default_log(msg, level="INFO"):
    print(msg)


def scan_collection(
    base_path,
    url_prefix,
    image_output_folder,
    force_update=False,
    update_csv=False,
    log=default_log,
    defer_markers=False,
    operation_id=None,
):
    """
    Main entry point to scan a collection folder.

    Args:
        base_path (str): Path to the collection folder (e.g., /products/keyrings)
        url_prefix (str): Prefix to apply to image URLs
        image_output_folder (str): Relative folder name where images are expected
        force_update (bool): If True, reprocess all folders regardless of .scanned state
        update_csv (bool): If True, reuses existing SKUs from .scanned files
        log (function): Logger function to capture status updates

    Returns:
        list: All product rows ready for CSV export
    """
    reset_flag = force_update  # Used to ensure SKU reset happens only once

    # Load the collection's shared JSON data
    collection_json_path = os.path.join(base_path, "product_info.json")
    log("")
    log(f"📂 Scanning collection: {base_path}", level="INFO")
    log(f"📄 Looking for JSON at: {collection_json_path}", level="INFO")

    try:
        json_raw = load_json(collection_json_path)
        shared_data = validate_json(json_raw, is_collection=True)
    except Exception as e:
        log(
            f"❌ Failed to load or validate JSON at {collection_json_path}: {str(e)}",
            level="ERROR",
        )
        return []

    # Use either title from JSON or folder name
    title = shared_data.get("title") or os.path.basename(base_path)
    log(
        f"📦 Loaded collection: {title} — Type: {shared_data.get('collection_type')}",
        level="INFO",
    )

    collection_type = shared_data["collection_type"]

    all_rows = []

    # Loop through each item in the collection folder
    for item in sorted(os.listdir(base_path)):
        item_path = os.path.join(base_path, item)

        # Skip non-folders and hidden/system folders
        if not os.path.isdir(item_path) or item.startswith("."):
            continue

        # Handle Simple Product collections
        if collection_type == "Simple":
            log(f"🔍 Scanning simple product folder: {item}", level="INFO")
            rows = scan_simple_product(
                item_path,
                shared_data,
                url_prefix,
                image_output_folder,
                force_update,
                update_csv,
                log,
                reset_flag=reset_flag,
                defer_markers=defer_markers,
                operation_id=operation_id,
            )
            reset_flag = False  # Only reset SKU index once
            all_rows.extend(rows)

        # Handle Variable Collection (e.g. Pug, Beagle, etc.)
        elif collection_type == "Variable Collection":
            log(f"🔍 Scanning variable product folder: {item}", level="INFO")
            rows = scan_variable_product(
                item_path,
                shared_data,
                url_prefix,
                image_output_folder,
                force_update,
                update_csv,
                log,
                reset_flag=reset_flag,
                defer_markers=defer_markers,
                operation_id=operation_id,
            )
            reset_flag = False  # Only reset SKU index once
            all_rows.extend(rows)

        # Handle Single Variable collections — process once
        elif collection_type == "Single Variable":
            log("🔍 Scanning single variable collection", level="INFO")
            rows = scan_single_variable(
                base_path,
                shared_data,
                url_prefix,
                image_output_folder,
                force_update,
                update_csv,
                log,
                reset_flag=reset_flag,
                defer_markers=defer_markers,
                operation_id=operation_id,
            )
            reset_flag = False  # Only reset SKU index once
            all_rows.extend(rows)
            break  # Only scan the base folder once

    return all_rows


def scan_simple_product(
    folder,
    shared_data,
    url_prefix,
    image_output_folder,
    force_update,
    update_csv,
    log,
    reset_flag,
    defer_markers=False,
    operation_id=None,
):
    """
    Scans a single simple product folder and generates a CSV-ready row.

    Args:
        folder (str): Path to the individual simple product folder
        shared_data (dict): Shared JSON data for the entire collection
        url_prefix (str): Base URL prefix for product image paths
        image_output_folder (str): Folder name for image grouping
        force_update (bool): If True, rescans even if .scanned file is present
        update_csv (bool): If True, reuses SKU from .scanned file if found
        log (function): Function to output logs (connected to log box or terminal)
        reset_flag (bool): If True, resets SKU index for this product

    Returns:
        list[dict]: List containing one row dictionary to write to the CSV
    """

    # Skip folder if it's already scanned and not in force mode
    if not should_rescan(folder, force_update, log=log):
        log(f"⏭️ Skipping already scanned folder: {folder}", level="INFO")
        return []

    # Load override product_info.json (if it exists), and merge with collection-level shared_data
    try:
        override_data = load_json(os.path.join(folder, "product_info.json"))
        merged = merge_product_json(shared_data, override_data)
    except Exception as e:
        log(f"❌ Failed to load JSON for {folder}: {str(e)}", level="ERROR")
        return []

    folder_name = os.path.basename(folder)

    # If no specific title override, build a fallback using folder name and collection title
    if "title" not in override_data:
        shared_title = shared_data.get("title")
        if shared_title:
            merged["title"] = f"{folder_name} - {shared_title}"
            log(
                f"ℹ️ No override title found. Using fallback title: '{merged['title']}'",
                level="INFO",
            )
        else:
            merged["title"] = folder_name
            log(
                f"ℹ️ No override or shared title found. Using folder name as title: '{folder_name}'",
                level="INFO",
            )

    # Validate required fields and normalize format
    merged = validate_json(merged)
    merged["source_folder"] = folder

    # Load existing .scanned file if update mode is active
    pending = load_pending_scanned(folder, log=log)
    scanned = pending.get("marker", {}) if pending else {}
    if not scanned and update_csv:
        scanned = load_scanned(folder, log=log)
    reuse_identity = bool(pending) or update_csv

    # Reuse existing SKU if found, else generate a new one
    if reuse_identity:
        log(
            f"🔁 Update mode is ON — using scanned SKU: {scanned.get('sku')}",
            level="INFO",
        )
    sku = (
        scanned.get("sku")
        if scanned.get("sku") and reuse_identity
        else generate_sku(
            shared_data["sku_prefix"],
            os.path.dirname(folder),
            reset_index=reset_flag,
            log=log,
        )
    )
    merged["sku"] = sku
    log(f"✅ Assigned SKU for product: {sku}", level="INFO")

    # Process and map image files to URL paths
    log(f"🖼️ Processing images in: {folder}", level="INFO")
    image_names = process_images(folder, image_output_folder, log=log)
    image_urls = get_image_csv_urls(image_names, url_prefix)

    # Save .scanned marker to record this product's processing
    write_scanned(
        folder,
        {
            "sku": sku,
            "title": merged.get("title") or os.path.basename(folder),
            "images_used": image_names,
        },
        log=log,
        defer=defer_markers,
        operation_id=operation_id,
    )

    log(
        f"📝 .scanned file updated for {merged.get('title') or os.path.basename(folder)}",
        level="INFO",
    )

    # Build the product row and return as a single-item list
    return [build_simple_product(merged, image_urls)]


def scan_variable_product(
    folder,
    shared_data,
    url_prefix,
    image_output_folder,
    force_update,
    update_csv,
    log,
    reset_flag,
    defer_markers=False,
    operation_id=None,
):
    """
    Scans a single variable product folder (e.g., 'Pug', 'Beagle') and builds all variation rows.

    Args:
        folder (str): Path to the variable product's folder
        shared_data (dict): Shared collection-level JSON
        url_prefix (str): Base URL for image links
        image_output_folder (str): Image folder name to be used in URLs
        force_update (bool): Whether to rescan even if .scanned exists
        update_csv (bool): Whether to reuse existing SKU from .scanned
        log (function): Logging output function
        reset_flag (bool): If True, resets the SKU index for this product

    Returns:
        list[dict]: A list of CSV-ready rows, one per variation
    """

    # Skip scanning if the product has already been scanned and force is not enabled
    if not should_rescan(folder, force_update, log=log):
        log(f"⏭️ Skipping scanned variable product: {folder}", level="INFO")
        return []

    # Load override product_info.json (if it exists), and merge with collection-level shared_data
    try:
        override_data = load_json(os.path.join(folder, "product_info.json"))
        merged = merge_product_json(shared_data, override_data)
    except Exception as e:
        log(f"❌ Failed to load JSON for {folder}: {str(e)}", level="ERROR")
        return []

    folder_name = os.path.basename(folder)

    # If no specific title override, build a fallback using folder name and collection title
    if "title" not in override_data:
        shared_title = shared_data.get("title")
        if shared_title:
            merged["title"] = f"{folder_name} - {shared_title}"
            log(
                f"ℹ️ No override title found. Using fallback title: '{merged['title']}'",
                level="INFO",
            )
        else:
            merged["title"] = folder_name
            log(
                f"ℹ️ No override or shared title found. Using folder name as title: '{folder_name}'",
                level="INFO",
            )

    # Validate and normalize the combined data
    merged = validate_json(merged)
    merged["source_folder"] = folder

    # Load existing .scanned file if updating
    pending = load_pending_scanned(folder, log=log)
    scanned = pending.get("marker", {}) if pending else {}
    if not scanned and update_csv:
        scanned = load_scanned(folder, log=log)
    reuse_identity = bool(pending) or update_csv

    matcher = ScannedVariationMatcher(scanned, log=log)

    # Either reuse existing SKU or generate a new one
    if reuse_identity:
        log(
            f"🔁 Update mode is ON — using scanned SKU: {scanned.get('sku')}",
            level="INFO",
        )
    sku = (
        scanned.get("sku")
        if scanned.get("sku") and reuse_identity
        else generate_sku(
            shared_data["sku_prefix"],
            os.path.dirname(folder),
            reset_index=reset_flag,
            log=log,
        )
    )
    if not reuse_identity:
        set_variation_counter(folder, 0, log=log)  # Start counter at 1 for variations
        log("🔢 Manually set initial variation counter to 1", level="INFO")

    merged["sku"] = sku
    log(f"✅ Assigned SKU for parent product: {sku}", level="INFO")

    # Process folder images and build the base image URL list
    log(f"🖼️ Processing parent images in: {folder}", level="INFO")
    parent_image_names = process_images(folder, image_output_folder, log=log)
    image_urls = get_image_csv_urls(parent_image_names, url_prefix)

    all_rows = [build_variable_parent(merged, image_urls)]

    # Build all variations from the merged data
    variations = build_variations(merged)
    resolved_variations = []

    log(
        f"🔧 Building {len(variations)} variations for product: {merged.get('title')}",
        level="INFO",
    )

    if not variations:
        log("⚠️ No variations generated — check attribute definitions", level="WARN")

    # Create a CSV-ready row for each variation
    for i, v_attrs in enumerate(variations):
        mod = apply_variation_modifiers(merged, v_attrs, log=log)
        v_sku = matcher.match(v_attrs) if reuse_identity else None
        if not v_sku:
            next_num = get_next_variation_counter(folder, log=log)
            v_sku = f"{sku}-{next_num}"
            log(f"🆕 New SKU assigned: {v_sku} for {v_attrs}", level="INFO")
        log(
            f"🔄 Assigned variation SKU: {v_sku} for attributes: {v_attrs}",
            level="INFO",
        )
        row = build_variation_row(
            merged,
            {"attributes": v_attrs, "modifiers": mod},
            image_urls,
            i + 1,
            override_sku=v_sku,
        )
        all_rows.append(row)
        resolved_variations.append({"attributes": v_attrs, "sku": v_sku})

    # Write .scanned marker with metadata
    write_scanned(
        folder,
        {
            "sku": sku,
            "title": merged.get("title") or os.path.basename(folder),
            "images_used": parent_image_names,
            "variation_count": len(variations),
            "variations": resolved_variations,
        },
        log=log,
        defer=defer_markers,
        operation_id=operation_id,
    )

    log(
        f"📝 .scanned file updated for {merged.get('title') or os.path.basename(folder)}",
        level="INFO",
    )

    return all_rows


def scan_single_variable(
    base_folder,
    shared_data,
    url_prefix,
    image_output_folder,
    force_update,
    update_csv,
    log,
    reset_flag,
    defer_markers=False,
    operation_id=None,
):
    """
    Scans a single folder that represents a variable product with all its variations inside.

    This is used when the top-level folder is the product, and variations are represented internally via attributes.

    Args:
        base_folder (str): Path to the product folder containing all variation definitions
        shared_data (dict): Shared JSON data for the entire collection
        url_prefix (str): Base URL to prepend to image paths
        image_output_folder (str): Subfolder name used in image URLs
        force_update (bool): If True, force rescanning even if .scanned exists
        update_csv (bool): If True, reuse SKU from existing .scanned file
        log (function): Logger function for status output
        reset_flag (bool): Whether to reset SKU counter for this run

    Returns:
        list[dict]: List of CSV-ready rows for each variation
    """

    # Skip this product if already scanned and force mode is off
    if not should_rescan(base_folder, force_update, log=log):
        log(f"⏭️ Skipping scanned folder: {base_folder}", level="INFO")
        return []

    # Normalize and validate all data fields
    merged = validate_json(shared_data)
    merged["source_folder"] = base_folder

    # Load existing scan data if in update mode
    pending = load_pending_scanned(base_folder, log=log)
    scanned = pending.get("marker", {}) if pending else {}
    if not scanned and update_csv:
        scanned = load_scanned(base_folder, log=log)
    reuse_identity = bool(pending) or update_csv

    matcher = ScannedVariationMatcher(scanned, log=log)

    # Either reuse scanned SKU or generate a new one
    if reuse_identity:
        log(
            f"🔁 Update mode is ON — using scanned SKU: {scanned.get('sku')}",
            level="INFO",
        )
    sku = (
        scanned.get("sku")
        if scanned.get("sku") and reuse_identity
        else generate_sku(
            shared_data["sku_prefix"], base_folder, reset_index=reset_flag, log=log
        )
    )
    if not reuse_identity:
        set_variation_counter(
            base_folder, 0, log=log
        )  # Start counter at 1 for variations
        log("🔢 Manually set initial variation counter to 1", level="INFO")

    merged["sku"] = sku
    log(f"✅ Assigned SKU for single variable product: {sku}", level="INFO")

    # Handle image processing and build final image URLs
    image_attrs = merged.get("image_attributes", [])

    # Attribute map defines the style/image subfolder structure
    attr_map = merged.get("attributes", {})
    all_rows = []

    folder_name = os.path.basename(base_folder)
    shared_title = merged.get("title")

    # Construct a fallback title if not defined explicitly
    if shared_title and folder_name:
        merged["title"] = f"{folder_name} - {shared_title}"
    elif not shared_title and folder_name:
        merged["title"] = folder_name

    parent_title = merged.get("title") or folder_name

    # image fallback if no images in parent folder adds images from style to parent
    parent_folder = os.path.join(base_folder, "parent")
    if os.path.exists(parent_folder):
        log("🖼️ Using 'parent/' folder for parent images", level="INFO")
        parent_image_names = process_images(parent_folder, image_output_folder, log=log)
    else:
        fallback_style = attr_map[image_attrs[0]][0]
        parent_folder = os.path.join(base_folder, fallback_style)
        log(f"🖼️ Using '{fallback_style}' for parent images", level="WARN")
        parent_image_names = process_images(parent_folder, image_output_folder, log=log)

    parent_image_urls = get_image_csv_urls(parent_image_names, url_prefix)
    all_rows.append(build_variable_parent(merged, parent_image_urls))

    # Generate all variation rows
    variations = build_variations(merged)
    resolved_variations = []

    log(
        f"🔧 Building {len(variations)} variations for single product: {merged.get('title')}",
        level="INFO",
    )

    if not variations:
        log("⚠️ No variations generated — check attribute definitions", level="WARN")

    # fallback to style if variation images are missing
    for i, v_attrs in enumerate(variations):
        log(f"🔎 Resolving image path for {v_attrs} → {base_folder}", level="WARN")
        base_path = os.path.join(base_folder, v_attrs[image_attrs[0]])
        style_images = process_images(base_path, image_output_folder, log=log)

        for attr in image_attrs[1:]:
            if attr in v_attrs:
                sub_path = os.path.join(base_path, v_attrs[attr])
                if os.path.exists(sub_path):
                    log(f"   ↳ Adding images from: {sub_path}")
                    extra_images = process_images(
                        sub_path, image_output_folder, log=log
                    )
                    style_images.extend(extra_images)

        image_urls = get_image_csv_urls(style_images, url_prefix)
        mod = apply_variation_modifiers(merged, v_attrs, log=log)
        v_sku = matcher.match(v_attrs) if reuse_identity else None
        if not v_sku:
            next_num = get_next_variation_counter(base_folder, log=log)
            v_sku = f"{sku}-{next_num}"
            log(f"🆕 New SKU assigned: {v_sku} for {v_attrs}", level="INFO")
        log(
            f"🔄 Assigned variation SKU: {v_sku} for attributes: {v_attrs}",
            level="INFO",
        )
        row = build_variation_row(
            merged,
            {"attributes": v_attrs, "modifiers": mod, "parent_title": parent_title},
            image_urls,
            i + 1,
            override_sku=v_sku,
        )
        all_rows.append(row)
        resolved_variations.append({"attributes": v_attrs, "sku": v_sku})

    # Save .scanned data with variation count
    write_scanned(
        base_folder,
        {
            "sku": sku,
            "title": parent_title,
            "images_used": parent_image_names,
            "variation_count": len(variations),
            "variations": resolved_variations,
        },
        log=log,
        defer=defer_markers,
        operation_id=operation_id,
    )

    log(
        f"📝 .scanned file updated for {merged.get('title') or os.path.basename(base_folder)}",
        level="INFO",
    )

    return all_rows


def build_variations(product_data):
    """
    Builds a list of all possible variation combinations based on product attribute data.

    Args:
        product_data (dict): A product dictionary that must include 'attributes' (dict[str, list[str]])

    Returns:
        list[dict]: List of all attribute combinations, each as its own variation dict
    """

    from itertools import product

    # Extract attribute dictionary (e.g., {'Style': ['A', 'B'], 'Color': ['Red', 'Blue']})
    attrs = product_data.get("attributes", {})
    if not attrs:
        return []

    # Separate attribute names (keys) and their possible values
    keys = list(attrs.keys())
    values = list(attrs.values())

    # Use Cartesian product to compute all possible combinations
    combos = list(product(*values))

    # Zip each combination back to the attribute names to form full variation dicts
    return [dict(zip(keys, combo)) for combo in combos]
