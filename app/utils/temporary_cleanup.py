"""Narrow cleanup for stale application-created metadata temporaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


STALE_METADATA_TEMPORARY_AGE = timedelta(hours=24)


def _valid_metadata(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except (OSError, ValueError, TypeError):
        return False


def cleanup_metadata_temporaries(target, *, operation_active, now=None) -> int:
    """Clean only known stale editor temporaries beside a valid destination."""

    if operation_active() or not _valid_metadata(Path(target)):
        return 0
    target = Path(target)
    current = now or datetime.now(UTC)
    candidates = [target.with_name(f"{target.name}.tmp")]
    candidates.extend(target.parent.glob(f".{target.name}.*.tmp"))
    removed = 0
    for path in candidates:
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if current - modified <= STALE_METADATA_TEMPORARY_AGE:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
