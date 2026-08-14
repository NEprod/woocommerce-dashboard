import os
import json
from datetime import datetime

from .atomic_files import atomic_write_json, atomic_write_text

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
PENDING_FILE = ".scanned.pending"
PENDING_VERSION = 1

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
    pending_path = os.path.join(folder, PENDING_FILE)

    if force_update:
        log(f"🔁 Forcing rescan of folder: {folder}", level="INFO")
        return True

    if os.path.exists(pending_path):
        log(f"🔄 Pending scan recovery found — scanning folder: {folder}", level="INFO")
        return True

    if not os.path.exists(scanned_path):
        log(f"🔄 No .scanned file found — scanning folder: {folder}", level="INFO")
        return True

    if os.path.exists(update_path):
        log(f"🛠️ Detected .update file — rescanning folder: {folder}", level="INFO")
        return True

    log(f"⏭️ Skipping folder (already scanned): {folder}", level="INFO")
    return False

def write_scanned(
    folder,
    data,
    log=print,
    *,
    defer=False,
    operation_id=None,
):
    """
    Writes a .scanned file containing the result of a successful scan.

    Adds a scan date to the data payload, saves it to disk, and removes
    any existing .update file to prevent future forced rescans.

    Args:
        folder (str): Path to the product folder
        data (dict): Scan summary data (SKU, title, images, etc.)
        log (function): Logger function for UI or console feedback (default: print)

    Returns:
        dict: The final marker payload, including scan_date.
    """

    scanned_path = os.path.join(folder, SCANNED_FILE)
    pending_path = os.path.join(folder, PENDING_FILE)
    update_path = os.path.join(folder, UPDATE_FILE)

    # Add scan date timestamp
    marker = dict(data)
    marker["scan_date"] = datetime.now().isoformat()

    try:
        if defer:
            atomic_write_json(
                pending_path,
                {
                    "version": PENDING_VERSION,
                    "operation_id": operation_id,
                    "state": "pending_database",
                    "marker": marker,
                },
            )
            log(f"📝 Pending .scanned intent written to: {pending_path}", level="INFO")
            return marker
        atomic_write_json(scanned_path, marker)
        log(f"📝 .scanned file written to: {scanned_path}", level="INFO")
    except Exception as e:
        log(f"❌ Failed to write .scanned file in {folder}: {e}", level="ERROR")
        raise

    # ✅ Auto-remove .update file if it exists — scan completed successfully
    if os.path.exists(update_path):
        try:
            os.remove(update_path)
            log(f"🗑️ Removed .update file after successful scan: {update_path}", level="INFO")
        except Exception as e:
            log(f"⚠️ Could not delete .update file in {folder}: {e}", level="WARN")
    if os.path.exists(pending_path):
        os.remove(pending_path)
    return marker


def load_pending_scanned(folder, log=print):
    path = os.path.join(folder, PENDING_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            pending = json.load(handle)
        marker = pending.get("marker")
        if (
            pending.get("version") != PENDING_VERSION
            or not isinstance(marker, dict)
            or not marker.get("sku")
        ):
            raise ValueError("unsupported or incomplete pending marker")
        return pending
    except Exception as error:
        log(f"❌ Failed to read pending marker in {folder}: {error}", level="ERROR")
        return {}


def iter_pending_scanned(root):
    if not root or not os.path.isdir(root):
        return []
    pending = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("_"))
        if PENDING_FILE in filenames:
            pending.append(directory)
    return sorted(pending)


def set_pending_state(folder, state, log=print):
    pending = load_pending_scanned(folder, log=log)
    if not pending:
        raise ValueError(f"No valid pending marker in {folder}")
    pending["state"] = state
    atomic_write_json(os.path.join(folder, PENDING_FILE), pending)
    return pending


def ensure_update(folder):
    path = os.path.join(folder, UPDATE_FILE)
    if not os.path.exists(path):
        atomic_write_text(path, "")
    return path


def apply_pending_scanned(folder, log=print, failure_injector=None):
    pending = load_pending_scanned(folder, log=log)
    if not pending:
        raise ValueError(f"No valid pending marker in {folder}")
    sku = pending["marker"]["sku"]
    if failure_injector:
        failure_injector("marker_replace", sku)
    atomic_write_json(os.path.join(folder, SCANNED_FILE), pending["marker"])

    update_path = os.path.join(folder, UPDATE_FILE)
    if os.path.exists(update_path):
        if failure_injector:
            failure_injector("update_remove", sku)
        os.remove(update_path)
        log(f"🗑️ Removed .update file after committed scan: {update_path}", level="INFO")
    return pending


def clear_pending_scanned(folder, failure_injector=None):
    path = os.path.join(folder, PENDING_FILE)
    if not os.path.exists(path):
        return
    pending = load_pending_scanned(folder, log=lambda *args, **kwargs: None)
    sku = pending.get("marker", {}).get("sku")
    if failure_injector:
        failure_injector("pending_clear", sku)
    os.remove(path)

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
