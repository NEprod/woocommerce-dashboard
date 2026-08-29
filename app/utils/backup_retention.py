"""Conservative retention for application-created persistent backups."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


RECONSTRUCTION_COUNT = 10
RECONSTRUCTION_AGE = timedelta(days=30)
RECOVERY_REQUIRED_AGE = timedelta(days=90)
METADATA_COUNT = 10
METADATA_AGE = timedelta(days=90)
MIGRATION_COUNT = 20
STALE_TEMPORARY_AGE = timedelta(hours=24)

_DATABASE_BACKUP = re.compile(
    r"^(?P<stem>.+)\.(?P<purpose>migration|reconstruction)-"
    r"(?P<transition>.+)\.(?P<timestamp>\d{8}T\d{6}\.\d{6}Z)\."
    r"(?P<unique>[0-9a-f]+)\.sqlite3$"
)
_LOGGER = logging.getLogger(__name__)


def _now(value=None):
    return value or datetime.now(UTC)


def _mtime(path: Path):
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def _sqlite_valid(path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError):
        return False


def _json_valid(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except (OSError, ValueError, TypeError):
        return False


def _recovery_marker(path: Path) -> Path:
    return Path(f"{path}.recovery-required")


def mark_backup_recovery_required(path: Path, *, now=None) -> Path:
    """Attach bounded, content-free recovery protection to a verified backup."""

    backup = Path(path)
    if not _sqlite_valid(backup):
        raise ValueError("Cannot protect an invalid database backup")
    marker = _recovery_marker(backup)
    marked_at = _now(now)
    marker.write_text(marked_at.isoformat(), encoding="ascii")
    marker.chmod(0o600)
    os.utime(marker, (marked_at.timestamp(), marked_at.timestamp()))
    return marker


def _delete_backup(path: Path):
    path.unlink()
    _recovery_marker(path).unlink(missing_ok=True)


def prune_database_backups(root: Path, *, purpose: str, now=None):
    """Prune only verified backups outside the approved protection policy."""

    root = Path(root)
    current = _now(now)
    parsed = []
    for path in root.glob(f"*.{purpose}-*.sqlite3"):
        match = _DATABASE_BACKUP.match(path.name)
        if match and match.group("purpose") == purpose and _sqlite_valid(path):
            parsed.append((path, match, _mtime(path)))
    parsed.sort(key=lambda item: (item[2], item[0].name), reverse=True)
    protected = set()
    if parsed:
        protected.add(parsed[0][0])

    if purpose == "reconstruction":
        protected.update(item[0] for item in parsed[:RECONSTRUCTION_COUNT])
        for path, _match, modified in parsed:
            if current - modified <= RECONSTRUCTION_AGE:
                protected.add(path)
            marker = _recovery_marker(path)
            if marker.exists() and current - _mtime(marker) <= RECOVERY_REQUIRED_AGE:
                protected.add(path)
    elif purpose == "migration":
        newest_by_transition = {}
        for path, match, _modified in parsed:
            newest_by_transition.setdefault(match.group("transition"), path)
        protected.update(newest_by_transition.values())
        for path, _match, _modified in parsed:
            marker = _recovery_marker(path)
            if marker.exists() and current - _mtime(marker) <= RECOVERY_REQUIRED_AGE:
                protected.add(path)
        for path, _match, _modified in parsed:
            if len(protected) >= MIGRATION_COUNT:
                break
            protected.add(path)
    else:
        raise ValueError(f"Unsupported backup purpose: {purpose}")

    deleted = 0
    failed = 0
    for path, _match, _modified in reversed(parsed):
        if path in protected:
            continue
        try:
            _delete_backup(path)
            deleted += 1
        except OSError:
            failed += 1
    return {"deleted": deleted, "failed": failed, "retained": len(parsed) - deleted}


def create_metadata_backup(target: Path) -> Path:
    """Create a unique validated metadata backup, then apply per-file retention."""

    target = Path(target)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = target.with_name(
        f"{target.name}.bak.{timestamp}.{uuid.uuid4().hex[:12]}"
    )
    try:
        with target.open("rb") as source, destination.open("xb") as backup:
            backup.write(source.read())
            backup.flush()
            os.fsync(backup.fileno())
        if not _json_valid(destination):
            raise ValueError("Metadata backup validation failed")
        destination.chmod(0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    try:
        result = prune_metadata_backups(target)
        if result["failed"]:
            _LOGGER.warning(
                "Metadata backup retention could not remove %s expired file(s)",
                result["failed"],
            )
    except Exception as error:
        _LOGGER.warning(
            "Metadata backup retention cleanup failed: %s",
            type(error).__name__,
        )
    return destination


def prune_metadata_backups(target: Path, *, now=None):
    target = Path(target)
    current = _now(now)
    backups = [
        path
        for path in target.parent.glob(f"{target.name}.bak.*")
        if path.is_file() and _json_valid(path)
    ]
    backups.sort(key=lambda path: (_mtime(path), path.name), reverse=True)
    protected = set(backups[:METADATA_COUNT])
    protected.update(
        path for path in backups if current - _mtime(path) <= METADATA_AGE
    )
    if backups:
        protected.add(backups[0])
    deleted = 0
    failed = 0
    for path in reversed(backups):
        if path in protected:
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            failed += 1
    return {"deleted": deleted, "failed": failed, "retained": len(backups) - deleted}


def cleanup_backup_temporaries(root: Path, *, now=None) -> int:
    """Remove only stale backup temporaries whose verified destination exists."""

    current = _now(now)
    removed = 0
    for temporary in Path(root).glob("*.sqlite3.tmp"):
        destination = Path(str(temporary)[: -len(".tmp")])
        try:
            stale = current - _mtime(temporary) > STALE_TEMPORARY_AGE
        except OSError:
            continue
        if stale and _sqlite_valid(destination):
            try:
                temporary.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def cleanup_restore_temporary(database_path: Path, *, now=None) -> int:
    """Remove one stale restore temporary only beside a verified live database."""

    database_path = Path(database_path)
    temporary = database_path.with_suffix(database_path.suffix + ".restore.tmp")
    if not temporary.is_file() or not _sqlite_valid(database_path):
        return 0
    current = _now(now)
    if current - _mtime(temporary) <= STALE_TEMPORARY_AGE:
        return 0
    try:
        temporary.unlink()
        return 1
    except OSError:
        return 0
