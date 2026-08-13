import os
import json
from datetime import datetime

"""
file_markers.py

Manages .scanned and .update file logic for each product folder.

Responsibilities:
- Deciding whether a folder should be rescanned (based on force or .update)
- Loading scan history from .scanned
- Writing .scanned after a successful scan
- Automatically cleaning up .update files after processing
"""

SCANNED_FILE = ".scanned"
UPDATE_FILE = ".update"

def should_rescan(folder, force_update=False, log=print):
    """
    Determines whether a folder should be rescanned based on:
    - Forced scan mode
    - Missing .scanned file
    - Presence of .update override file

    Args:
        folder (str): Path to the product folder
        force_update (bool): If True, always triggers a rescan
        log (function): Logger output function (default: print)

    Returns:
        bool: True if the folder should be rescanned, otherwise False
    """

    scanned_path = os.path.join(folder, SCANNED_FILE)
    update_path = os.path.join(folder, UPDATE_FILE)

    if force_update:
        log(f"🔁 Forcing rescan of folder: {folder}", level="INFO")
        return True

    if not os.path.exists(scanned_path):
        log(f"🔄 No .scanned file found — scanning folder: {folder}", level="INFO")
        return True

    if os.path.exists(update_path):
        log(f"🛠️ Detected .update file — rescanning folder: {folder}", level="INFO")
        return True

    log(f"⏭️ Skipping folder (already scanned): {folder}", level="INFO")
    return False

def write_scanned(folder, data, log=print):
    """
    Writes a .scanned file containing the result of a successful scan.

    Adds a scan date to the data payload, saves it to disk, and removes
    any existing .update file to prevent future forced rescans.

    Args:
        folder (str): Path to the product folder
        data (dict): Scan summary data (SKU, title, images, etc.)
        log (function): Logger function for UI or console feedback (default: print)

    Returns:
        None
    """

    scanned_path = os.path.join(folder, SCANNED_FILE)
    update_path = os.path.join(folder, UPDATE_FILE)

    # Add scan date timestamp
    data["scan_date"] = datetime.now().isoformat()

    try:
        with open(scanned_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log(f"📝 .scanned file written to: {scanned_path}", level="INFO")
    except Exception as e:
        log(f"❌ Failed to write .scanned file in {folder}: {e}", level="ERROR")
        return

    # ✅ Auto-remove .update file if it exists — scan completed successfully
    if os.path.exists(update_path):
        try:
            os.remove(update_path)
            log(f"🗑️ Removed .update file after successful scan: {update_path}", level="INFO")
        except Exception as e:
            log(f"⚠️ Could not delete .update file in {folder}: {e}", level="WARN")

def load_scanned(folder, log=print):
    """
    Loads the .scanned file from the specified folder if it exists.

    Args:
        folder (str): Path to the product folder
        log (function): Logger output function (default: print)

    Returns:
        dict: Parsed scan data from .scanned, or an empty dict if not found or invalid
    """
    path = os.path.join(folder, SCANNED_FILE)

    if not os.path.exists(path):
        log(f"⚠️ No .scanned file found in: {folder}", level="WARN")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            log(f"📖 Loaded .scanned file from: {path}", level="INFO")
            return data
    except Exception as e:
        log(f"❌ Failed to read or parse .scanned file in {folder}: {e}", level="ERROR")
        return {}