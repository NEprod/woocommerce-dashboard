import json
import os
import sqlite3
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import create_app, db
from app.models import CatalogueOperation, CatalogueOperationItem
from app.utils.backup_retention import (
    cleanup_backup_temporaries,
    cleanup_restore_temporary,
    create_metadata_backup,
    mark_backup_recovery_required,
    prune_database_backups,
    prune_metadata_backups,
)
from app.utils.operation_control import prune_operation_history
from app.utils.redaction import redact_diagnostic
from app.utils.scan_runner import (
    BoundedLogQueue,
    _prune_completed_runs,
    _runs,
    _runs_lock,
)
from app.utils.temporary_cleanup import cleanup_metadata_temporaries
from config import Config


ROOT = Path(__file__).resolve().parents[1]


def _sqlite(path: Path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS fixture (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()


def _set_age(path: Path, now: datetime, days: int):
    timestamp = (now - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


@pytest.fixture
def hardening_app(tmp_path, monkeypatch):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir(parents=True)
    monkeypatch.setattr(
        Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{database}"
    )
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    yield app, database
    with app.app_context():
        db.session.remove()


def test_production_rejects_missing_and_placeholder_secret_keys(monkeypatch):
    from app.security import validate_secret_key

    for value in (None, "", "dev-secret", "changeme", "replace-with-a-secret"):
        if value is None:
            monkeypatch.delenv("SECRET_KEY", raising=False)
        else:
            monkeypatch.setenv("SECRET_KEY", value)
        with pytest.raises(RuntimeError, match="SECRET_KEY") as error:
            validate_secret_key()
        if value:
            assert value not in str(error.value)


def test_explicit_test_secret_is_accepted_and_not_persisted(
    hardening_app, monkeypatch
):
    _app, database = hardening_app
    secret = os.environ["SECRET_KEY"]
    assert database.is_file()
    for path in database.parent.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_entrypoint_rejects_missing_secret_without_echoing_values():
    environment = os.environ.copy()
    environment.pop("SECRET_KEY", None)
    result = subprocess.run(
        ["sh", str(ROOT / "docker" / "entrypoint.sh"), "true"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 64
    assert "SECRET_KEY is required" in result.stderr
    assert "pytest-only" not in result.stderr


def test_redaction_covers_headers_credentials_webhooks_and_sensitive_paths(tmp_path):
    catalogue = tmp_path / "host" / "catalogue"
    output = tmp_path / "host" / "output"
    instance = tmp_path / "host" / "instance"
    home = tmp_path / "home" / "person"
    text = (
        "Authorization: Bearer bearer-secret\n"
        "Proxy-Authorization=Basic proxy-secret\n"
        "Cookie: session=session-secret; preference=ordinary\n"
        "Set-Cookie: auth=cookie-secret\n"
        "{'Authorization': 'Basic structured-secret', 'password': 'dict-private'} "
        "consumer_key=ck_private consumer_secret=cs_private api_key=api-private "
        "password=hunter2 token=token-private "
        "https://discord.com/api/webhooks/123/private "
        f"{catalogue}/Collection/Product {output}/image.jpg "
        f"{instance}/site.db {home}/notes.txt /Users/alice/private.txt"
    )
    redacted = redact_diagnostic(
        text,
        paths={
            "<catalogue>": catalogue,
            "<output>": output,
            "<instance>": instance,
            "<home>": home,
        },
    )
    for secret in (
        "bearer-secret",
        "proxy-secret",
        "session-secret",
        "cookie-secret",
        "structured-secret",
        "dict-private",
        "ck_private",
        "cs_private",
        "api-private",
        "hunter2",
        "token-private",
        "/api/webhooks/123/private",
        str(tmp_path),
        "/Users/alice",
    ):
        assert secret not in redacted
    for label in ("<catalogue>", "<output>", "<instance>", "<home>"):
        assert label in redacted
    assert "Collection/Product" in redacted


def test_bounded_log_queue_keeps_newest_lines_and_marks_truncation():
    queue = BoundedLogQueue(max_lines=5, max_bytes=150)
    for index in range(12):
        queue.put(f"line-{index}-" + ("x" * 20))

    lines = []
    while not queue.empty():
        lines.append(queue.get_nowait())
    assert len(lines) <= 5
    assert sum(len(line.encode()) for line in lines) <= 150
    assert any("truncated" in line.lower() for line in lines)
    assert any("line-11" in line for line in lines)


def test_completed_memory_runs_are_bounded_and_protected_runs_survive():
    with _runs_lock:
        _runs.clear()
        for index in range(25):
            _runs[f"completed-{index:02d}"] = {
                "status": "done",
                "sequence": index,
                "operation_id": f"operation-{index:02d}",
                "recovery_state": "none",
            }
        _runs["active"] = {
            "status": "running",
            "sequence": -2,
            "operation_id": "active-operation",
            "recovery_state": "none",
        }
        _runs["recovery"] = {
            "status": "error",
            "sequence": -1,
            "operation_id": "recovery-operation",
            "recovery_state": "marker_recovery_required",
        }

    removed = _prune_completed_runs()
    assert removed == 5
    assert "active" in _runs
    assert "recovery" in _runs
    assert sum(key.startswith("completed-") for key in _runs) == 20
    assert "completed-00" not in _runs
    assert "completed-24" in _runs
    with _runs_lock:
        _runs.clear()


def test_persistent_operation_retention_obeys_count_age_and_protection(
    hardening_app,
):
    app, _database = hardening_app
    now = datetime.now(UTC).replace(tzinfo=None)
    with app.app_context():
        for index in range(1005):
            operation = CatalogueOperation(
                id=f"success-{index:04d}",
                operation_type="append",
                status="succeeded",
                recovery_state="none",
                started_at=now - timedelta(days=200, seconds=1005 - index),
                finished_at=now - timedelta(days=200, seconds=1005 - index),
            )
            operation.items.append(
                CatalogueOperationItem(status="succeeded", source_path="Collection/P")
            )
            db.session.add(operation)
        rows = (
            ("recent-success", "succeeded", "none", 10),
            ("old-failed", "failed", "none", 400),
            ("newer-failed", "failed", "none", 10),
            ("recent-failed", "failed", "none", 364),
            ("recovery-required", "failed", "marker_recovery_required", 800),
            ("running", "running", "none", 800),
        )
        for identifier, status, recovery, age in rows:
            db.session.add(
                CatalogueOperation(
                    id=identifier,
                    operation_type="product_update",
                    status=status,
                    recovery_state=recovery,
                    started_at=now - timedelta(days=age),
                    finished_at=None if status == "running" else now - timedelta(days=age),
                )
            )
        db.session.commit()

        result = prune_operation_history(now=now)

        assert result["deleted"] == 7
        assert db.session.get(CatalogueOperation, "success-0000") is None
        assert CatalogueOperationItem.query.count() == 999
        for protected in (
            "recent-success",
            "newer-failed",
            "recent-failed",
            "recovery-required",
            "running",
        ):
            assert db.session.get(CatalogueOperation, protected)
        assert db.session.get(CatalogueOperation, "old-failed") is None


def test_reconstruction_backup_retention_keeps_count_age_and_recovery(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    now = datetime.now(UTC)
    paths = []
    for index in range(13):
        path = root / (
            f"site.reconstruction-0004_lifecycle-to-reconstruction."
            f"20240101T0000{index:02d}.000000Z.{index:012x}.sqlite3"
        )
        _sqlite(path)
        _set_age(path, now, 60 + index)
        paths.append(path)
    mark_backup_recovery_required(paths[-1], now=now - timedelta(days=60))

    result = prune_database_backups(root, purpose="reconstruction", now=now)

    assert result["deleted"] == 2
    assert len(list(root.glob("*.sqlite3"))) == 11
    assert paths[-1].exists()


def test_metadata_backup_names_and_retention_are_per_source(tmp_path):
    target = tmp_path / "product_info.json"
    target.write_text('{"name": "fixture"}', encoding="utf-8")
    first = create_metadata_backup(target)
    second = create_metadata_backup(target)
    assert first != second
    assert first.is_file() and second.is_file()

    now = datetime.now(UTC)
    for index in range(12):
        backup = tmp_path / f"product_info.json.bak.20240101T000000.{index:06d}Z.{index:08x}"
        backup.write_text('{"name": "old"}', encoding="utf-8")
        _set_age(backup, now, 120 + index)
    result = prune_metadata_backups(target, now=now)
    assert result["deleted"] == 4
    assert len(list(tmp_path.glob("product_info.json.bak.*"))) == 10


def test_migration_retention_keeps_transition_points_and_caps_unprotected(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    now = datetime.now(UTC)
    for index in range(22):
        path = root / (
            f"site.migration-0003-to-0004.20240101T0000{index:02d}."
            f"000000Z.{index:012x}.sqlite3"
        )
        _sqlite(path)
        _set_age(path, now, 50 + index)
    protected = root / (
        "site.migration-unversioned-to-0004.20230101T000000."
        "000000Z.ffffffffffff.sqlite3"
    )
    _sqlite(protected)
    _set_age(protected, now, 800)

    result = prune_database_backups(root, purpose="migration", now=now)
    assert result["deleted"] == 3
    assert len(list(root.glob("*.sqlite3"))) == 20
    assert protected.exists()


def test_stale_temporary_cleanup_is_narrow_and_requires_valid_destination(tmp_path):
    target = tmp_path / "product_info.json"
    target.write_text('{"name": "valid"}', encoding="utf-8")
    fixed = tmp_path / "product_info.json.tmp"
    atomic = tmp_path / ".product_info.json.deadbeef.tmp"
    marker = tmp_path / ".scanned.pending"
    update = tmp_path / ".update"
    image = tmp_path / "source.jpg"
    for path in (fixed, atomic, marker, update, image):
        path.write_text("temporary", encoding="utf-8")
        _set_age(path, datetime.now(UTC), 2)

    assert cleanup_metadata_temporaries(target, operation_active=lambda: True) == 0
    assert fixed.exists() and atomic.exists()
    assert cleanup_metadata_temporaries(target, operation_active=lambda: False) == 2
    assert not fixed.exists() and not atomic.exists()
    assert marker.exists() and update.exists() and image.exists()


def test_backup_temp_cleanup_requires_valid_final_backup(tmp_path):
    final = tmp_path / (
        "site.reconstruction-0004-to-reconstruction."
        "20240101T000000.000000Z.aaaaaaaaaaaa.sqlite3"
    )
    _sqlite(final)
    temporary = Path(f"{final}.tmp")
    temporary.write_text("partial", encoding="utf-8")
    _set_age(temporary, datetime.now(UTC), 2)
    orphan = tmp_path / "site.reconstruction-orphan.sqlite3.tmp"
    orphan.write_text("partial", encoding="utf-8")
    _set_age(orphan, datetime.now(UTC), 2)

    assert cleanup_backup_temporaries(tmp_path) == 1
    assert not temporary.exists()
    assert orphan.exists()


def test_backup_directory_and_database_backup_modes_are_secure(tmp_path):
    from app.database import backup_database

    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    _sqlite(database)
    backup_root = database.parent / "backups"
    backup_root.mkdir(mode=0o755)

    backup = backup_database(
        database,
        source_revision="0004_lifecycle",
        target_revision="reconstruction",
        purpose="reconstruction",
    )
    assert stat.S_IMODE(backup_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_stale_restore_temp_requires_verified_live_database(tmp_path):
    database = tmp_path / "site.db"
    _sqlite(database)
    temporary = database.with_suffix(".db.restore.tmp")
    temporary.write_text("partial", encoding="utf-8")
    _set_age(temporary, datetime.now(UTC), 2)
    assert cleanup_restore_temporary(database) == 1
    assert not temporary.exists()


def test_operation_retention_failure_does_not_fail_successful_completion(
    hardening_app, monkeypatch
):
    from app.utils import operation_control

    app, _database = hardening_app
    with app.app_context():
        lease = operation_control.acquire_catalogue_operation("append")
        monkeypatch.setattr(
            operation_control,
            "prune_operation_history",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("token=private")),
        )
        operation_control.finish_catalogue_operation(lease.id, status="succeeded")
        assert db.session.get(CatalogueOperation, lease.id).status == "succeeded"


def test_compose_defines_bounded_stdout_stderr_rotation():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "driver: local" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert 'compress: "true"' in compose


def test_docker_image_excludes_confirmed_unused_static_sources():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for excluded in (
        "tests",
        "docs",
        "app/static/assets/scss",
        "app/static/assets/package.json",
        "app/static/assets/gulpfile.js",
        "app/static/assets/js/volt.js",
        "app/static/assets/img/flags",
        "app/static/assets/img/team",
        "app/static/assets/img/pages",
        "app/static/assets/vendor/chartist",
        "app/static/assets/vendor/sweetalert2",
    ):
        assert excluded in dockerignore

    runtime_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in (ROOT / "app" / "templates", ROOT / "app" / "static" / "assets" / "css")
        for path in root.rglob("*")
        if path.is_file()
    )
    for removed_reference in (
        "assets/js/volt.js",
        "assets/img/flags/",
        "assets/img/team/",
        "assets/img/pages/",
        "assets/vendor/chartist/",
        "assets/vendor/sweetalert2/",
    ):
        assert removed_reference not in runtime_text
