# app/utils/scan_runner.py
import os
import time
import threading
from flask import url_for
from datetime import datetime
from queue import Queue, Empty

from app.models import Settings
from app.utils.scanner import scan_collection
from app.utils.discord import (  # UPDATED import
    notify_scan_started,
    notify_scan_completed,
    notify_scan_failed,
)
from app.utils.ingest import ingest_rows_to_db

# In-memory progress store
_runs = {}


def make_logger(run_id):
    q = _runs[run_id]["queue"]

    def log(msg, level="INFO"):
        prefix = {"INFO": "[ℹ️]", "WARN": "[⚠️]", "ERROR": "[❌]"}.get(
            level.upper(), "[ℹ️]"
        )
        line = f"{time.strftime('%H:%M:%S')} {prefix} {msg}"
        q.put(line)

    return log


def start_scan(app, run_id, scan_mode="append"):
    _runs[run_id] = {
        "total": 0,
        "done": 0,
        "status": "running",
        "queue": Queue(),
        "summary": {
            "new_rows": 0,
            "folders": 0,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        },
    }
    thread = threading.Thread(
        target=_scan_thread, args=(app, run_id, scan_mode), daemon=True
    )
    thread.start()


def _scan_thread(app, run_id, scan_mode):
    with app.app_context():
        start_ts = datetime.utcnow()
        try:
            log = make_logger(run_id)

            settings = Settings.query.first()
            if not settings:
                _runs[run_id]["status"] = "error"
                log("No settings found. Please complete setup first.", level="ERROR")
                return

            scan_folder = settings.product_folder or ""
            image_folder = settings.output_folder or ""
            url_prefix = settings.url_prefix or ""

            if not (scan_folder and image_folder and url_prefix):
                _runs[run_id]["status"] = "error"
                log("Missing scan settings (folders or URL prefix).", level="ERROR")
                return

            force = scan_mode == "full"
            update = scan_mode == "update"

            folders = [
                f
                for f in sorted(os.listdir(scan_folder))
                if os.path.isdir(os.path.join(scan_folder, f)) and not f.startswith("_")
            ]
            _runs[run_id]["total"] = len(folders)
            _runs[run_id]["summary"]["folders"] = len(folders)

            # 🔔 Discord: started
            try:
                notify_scan_started(scan_mode, len(folders))
            except Exception as e:
                log(f"⚠️ Discord start notify failed: {e}", level="WARN")

            log(f"Scan mode: {scan_mode}")
            log(f"Using product folder: {scan_folder}")
            log(f"Using image output folder: {image_folder}")
            log(f"Using URL prefix: {url_prefix}")
            log(f"Found {len(folders)} folders to process.")
            log("🔍 Starting scan...")

            all_rows = []
            for idx, name in enumerate(folders, start=1):
                folder_path = os.path.join(scan_folder, name)
                log(f"📂 Scanning: {folder_path}")
                try:
                    rows = scan_collection(
                        folder_path,
                        url_prefix,
                        image_folder,
                        force_update=force,
                        update_csv=update,
                        log=log,
                    )
                    all_rows.extend(rows)
                    log(f"✅ {name} → {len(rows)} rows processed.")
                except Exception as e:
                    log(f"❌ Error in {name}: {e}", level="ERROR")
                _runs[run_id]["done"] = idx

            _runs[run_id]["summary"]["new_rows"] = len(all_rows)
            _runs[run_id]["summary"]["finished_at"] = datetime.utcnow().isoformat()

            # Ingest → DB
            summary = ingest_rows_to_db(all_rows, log=log)
            _runs[run_id]["summary"].update(summary)
            log(f"🗄️ DB summary: {summary}")
            log(f"📦 Total rows prepared: {len(all_rows)}")
            log("✅ Scan complete.")

            # 🔔 Discord: completed
            elapsed = datetime.utcnow() - start_ts
            mins, secs = divmod(int(elapsed.total_seconds()), 60)
            elapsed_text = f"{mins:02d}:{secs:02d}"
            try:
                notify_scan_completed(scan_mode, _runs[run_id]["summary"], elapsed_text)
            except Exception as e:
                log(f"⚠️ Discord complete notify failed: {e}", level="WARN")

            _runs[run_id]["status"] = "done"

        except Exception as e:
            make_logger(run_id)(f"❌ Critical error: {e}", level="ERROR")
            _runs[run_id]["status"] = "error"
            # 🔔 Discord: failed
            try:
                notify_scan_failed(scan_mode, str(e))
            except Exception:
                pass


def stream_lines(run_id, timeout=15):
    q = _runs[run_id]["queue"]
    while True:
        try:
            line = q.get(timeout=timeout)
            yield f"data: {line}\n\n"
        except Empty:
            yield ":\n\n"
            if _runs[run_id]["status"] in ("done", "error") and q.empty():
                break


def get_progress(run_id):
    return {
        "total": _runs[run_id]["total"],
        "done": _runs[run_id]["done"],
        "status": _runs[run_id]["status"],
        "summary": _runs[run_id]["summary"],
    }
