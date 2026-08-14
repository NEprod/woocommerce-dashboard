"""Small same-directory atomic file replacement helpers."""

import json
import os
import tempfile


def _sync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_text(path, text):
    destination = os.fspath(path)
    directory = os.path.dirname(destination) or "."
    basename = os.path.basename(destination)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{basename}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        _sync_directory(directory)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path, payload):
    atomic_write_text(path, json.dumps(payload, indent=2))
