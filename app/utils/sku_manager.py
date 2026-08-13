import os
import json

"""
sku_manager.py

Handles all SKU-related logic for the product scanning tool.

Responsibilities:
- Generating unique SKUs with optional counters
- Loading and saving SKU index files (sku_index.json)
- Managing variation SKUs for reuse (e.g., update mode)
- Providing utilities to preserve SKU consistency across scans
"""

SKU_INDEX_FILE = "sku_index.json"

def default_log(msg, level="INFO"):
    print(msg)

def load_sku_index(base_path, log=default_log):
    """
    Loads the SKU index JSON from the given base path.

    If the index does not exist, returns a default dictionary with "counter" set to 0.

    Args:
        base_path (str): Path to the folder containing 'sku_index.json'

    Returns:
        dict: Dictionary containing SKU index data (e.g., {'counter': int, ...})
    """
    path = os.path.join(base_path, "sku_index.json")

    if os.path.exists(path):
        log(f"📥 Loaded SKU index from: {path}", level="INFO")
        with open(path, "r") as f:
            return json.load(f)
    log(f"🆕 No existing SKU index found — creating new index at: {path}", level="INFO")
    return {}

def save_sku_index(index, base_path, log=default_log):
    """
    Saves the SKU index dictionary as 'sku_index.json' in the given folder.

    Args:
        index (dict): The updated SKU index to save
        base_path (str): Path to the folder where 'sku_index.json' should be written

    Returns:
        None
    """
    path = os.path.join(base_path, "sku_index.json")

    try:
        with open(path, "w") as f:
            json.dump(index, f, indent=2)
        log(f"💾 Saved SKU index to: {path}", level="INFO")
    except Exception as e:
        log(f"❌ Failed to write SKU index: {e}", level="ERROR")

def generate_sku(prefix, base_path, reset_index=False, log=default_log):
    """
    Generates a new SKU string using a prefix and a running index stored in 'sku_index.json'.

    Loads or initializes the SKU index file, optionally resets the counter,
    then saves the incremented value and returns the formatted SKU.

    Args:
        prefix (str): SKU prefix (e.g., 'DOG-', 'PUG-')
        base_path (str): Directory where 'sku_index.json' is stored
        reset_index (bool): If True, resets the counter to 1 for this SKU generation

    Returns:
        str: Formatted SKU (e.g., 'DOG-0001')
    """
    index = load_sku_index(base_path, log=log)

    # Determine starting counter
    counter = 1 if reset_index else index.get("counter", 0) + 1
    index["counter"] = counter

    save_sku_index(index, base_path, log=log)

    return f"{prefix}{counter:04d}"

def update_sku_index_for_variations(base_path, sku, variations, log=default_log):
    """
    Saves a list of variation SKUs and their attributes into 'sku_index.json'.

    This function helps preserve and reuse SKUs across re-scans when 'update_csv' is enabled.

    Args:
        base_path (str): Path to the product folder
        sku (str): Parent product SKU
        variations (list[dict]): List of variation dicts, each with 'attributes'

    Returns:
        None
    """
    index = load_sku_index(base_path, log=log)
    log(f"📦 Updating SKU index with {len(variations)} variations for parent SKU: {sku}", level="INFO")

    index["variations"] = [
        {"attributes": v["attributes"], "sku": f"{sku}-{i+1}"}
        for i, v in enumerate(variations)
    ]
    index["variation_count"] = len(variations)
    save_sku_index(index, base_path, log=log)
    log(f"✅ Variation SKU map saved to: {base_path}", level="INFO")

def lookup_variation_sku(base_path, variation_attrs, log=default_log):
    """
    Looks up an existing variation SKU in 'sku_index.json' based on matching attributes.

    This is used to preserve variation SKUs between scans when 'update_csv' is enabled.

    Args:
        base_path (str): Path to the folder containing 'sku_index.json'
        variation_attrs (dict): Dictionary of attribute names and values for the variation

    Returns:
        str or None: The matched SKU if found, otherwise None
    """
    log(f"🔍 Looking up variation SKU: {variation_attrs}")

    index = load_sku_index(base_path, log=log)
    for entry in index.get("variations", []):
        if entry["attributes"] == variation_attrs:
            log(f"✅ Match found — using existing SKU: {entry['sku']}", level="INFO")
            return entry["sku"]
    log(f"❌ No matching SKU found for attributes: {variation_attrs}", level="ERROR")  # No match found
    return None

def get_next_variation_counter(base_path, log=default_log):
    """
    Retrieves and increments the next variation counter for a product.
    
    Args:
        base_path (str): Path to the product folder.
        log (function): Logger function.
        reset (bool): Whether to reset the counter to 0 before incrementing.

    Returns:
        int: The next variation counter value (1-based).
    """

    index = load_sku_index(base_path, log=log)

    # Increment and store
    current = index.get("variation_counter", 0)
    next_val = current + 1
    index["variation_counter"] = next_val

    # Save and log
    save_sku_index(index, base_path, log=log)
    log(f"🔢 Current variation counter: {current}, Next: {next_val}", level="INFO")

    return next_val

class ScannedVariationMatcher:
    """
    Safely matches current variation attributes to previous .scanned entries,
    supporting exact match, smart partial match (for added attributes),
    and avoiding duplicate SKU assignments.
    """

    def __init__(self, scanned_data, log=default_log):
        self.scanned_variations = scanned_data.get("variations", [])
        self.used_skus = set()
        self.log = log

    def match(self, current_attrs):
        """
        Finds a matching SKU from scanned data.

        1. Exact match: All keys and values identical
        2. Partial match: Scanned entry is a subset of current (handles added attributes)
           - Prevents matching if current is *less specific* than the scanned version

        Args:
            current_attrs (dict): Current variation's full attribute set

        Returns:
            str or None: Reused SKU, or None if unmatched
        """
        # Exact match
        for entry in self.scanned_variations:
            if entry["attributes"] == current_attrs and entry["sku"] not in self.used_skus:
                self.used_skus.add(entry["sku"])
                self.log(f"🎯 Exact match SKU: {entry['sku']} for {current_attrs}", level="INFO")
                return entry["sku"]

        # Safe partial match: scanned keys ⊆ current keys
        for entry in self.scanned_variations:
            scanned_attrs = entry["attributes"]
            if all(scanned_attrs.get(k) == current_attrs.get(k) for k in scanned_attrs):
                if entry["sku"] not in self.used_skus:
                    self.used_skus.add(entry["sku"])
                    self.log(f"🔁 Fallback match SKU: {entry['sku']} for {current_attrs}", level="INFO")
                    return entry["sku"]
                
        # No match found
        self.log(f"❌ No SKU matched for attributes: {current_attrs}", level="WARN")
        return None
    
def set_variation_counter(folder, value ,log=default_log):
    sku_index = load_sku_index(folder, log=log)
    sku_index["variation_counter"] = value
    save_sku_index(sku_index, folder, log=log)