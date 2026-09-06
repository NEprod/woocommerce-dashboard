"""Deployment readiness and first-run bookkeeping; no scanner interpretation."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from urllib.parse import urlsplit

from flask import current_app
from app.models import Settings, CatalogueOperation
from app.taxonomy_registry import load_configured_registry
from app.taxonomy_workspace import counts
from app.image_preparation import intake_readiness
from app.utils.atomic_files import atomic_write_json
from app import db


FIELDS = {"product_folder": "Catalogue", "output_folder": "Output", "url_prefix": "Public image URL prefix"}


def owned(name):
    return current_app.config.get(name.upper()) is not None


def valid_url(value):
    try:
        parsed = urlsplit(value or "")
        return bool(len(value or "") <= 512 and parsed.scheme in {"http", "https"} and parsed.hostname and
                    not parsed.username and not parsed.password and not parsed.query and
                    not parsed.fragment and not any(c.isspace() or ord(c) < 32 for c in value) and
                    parsed.port != 0)
    except (ValueError, TypeError):
        return False


def directory_state(value):
    if not value:
        return "Missing"
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
        return "Invalid"
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        return "Invalid"
    try:
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            return "Invalid"
        if not info.st_mode & 0o444 or not os.access(path, os.R_OK | os.X_OK):
            return "Inaccessible"
        if not info.st_mode & 0o222 or not os.access(path, os.W_OK):
            return "Not writable"
        return "Ready"
    except FileNotFoundError:
        return "Missing"
    except OSError:
        return "Inaccessible"


def readiness():
    settings = Settings.query.first() or Settings()
    checks = []
    for name, label in FIELDS.items():
        value = getattr(settings, name) or ""
        check_state = ("Configured" if valid_url(value) else "Invalid" if value else "Missing") if name == "url_prefix" else directory_state(value)
        # Only conventional container paths are shown. Non-container paths are
        # deliberately described without echoing host filesystem locations.
        display = (value if valid_url(value) else "Not configured safely") if name == "url_prefix" else value if value in {"/catalogue", "/output"} else "Configured directory" if value else "Not configured"
        checks.append(dict(key=name, label=label, state=check_state, display=display, owned=owned(name)))
    taxonomy = load_configured_registry()
    intake = intake_readiness()
    from app.woocommerce_connection import build_woocommerce_workspace
    woo = build_woocommerce_workspace()
    setup = state()
    state_valid = setup is None or setup.get("status") != "invalid"
    return dict(checks=checks, state_valid=state_valid, configured=all(c["state"] in {"Ready", "Configured"} for c in checks),
                taxonomy=taxonomy, counts=counts(taxonomy.snapshot.data) if taxonomy.available else None,
                intake=intake, woo_configured=woo["configuration"]["configured"], woo_state=woo["health"]["state"],
                ready=state_valid and all(c["state"] in {"Ready", "Configured"} for c in checks) and taxonomy.available)


def configuration_digest():
    settings = Settings.query.first() or Settings()
    values = [getattr(settings, key) for key in FIELDS]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _path():
    return Path(current_app.instance_path) / "onboarding.json"


def state():
    """Absent means a legacy installation; corrupt state never means complete."""
    try:
        fd = os.open(_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        return {"status": "invalid"}
    try:
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return {"status": "invalid"}
            value = json.loads(handle.read(4097))
        if value.get("version") != 1 or value.get("status") not in {"pending", "complete"} or not isinstance(value.get("token"), str):
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError, OSError):
        return {"status": "invalid"}


def pending():
    value = state()
    return value is not None and value.get("status") != "complete"


def begin():
    if _path().is_symlink():
        raise ValueError("Setup state is unsafe; check application data.")
    atomic_write_json(_path(), {"version": 1, "status": "pending", "token": uuid.uuid4().hex})


def scan_scope():
    value = state()
    if not value or value.get("status") != "pending":
        raise ValueError("Setup state is unavailable; check application data.")
    return {"initial_setup": value["token"], "setup_configuration": configuration_digest()}


def belongs(operation):
    value = state()
    if not value or value.get("status") != "pending" or operation is None:
        return False
    try:
        scope = json.loads(operation.scope or "{}")
        return (scope.get("initial_setup") == value["token"] and
                scope.get("setup_configuration") == configuration_digest() and
                operation.operation_type in {"append", "full", "reconstruction"})
    except (ValueError, AttributeError):
        return False


def can_complete(operation):
    return bool(belongs(operation) and operation.status in {"succeeded", "partial"} and
                not operation.products_failed and not operation.error and operation.recovery_state in {None, "none"})


def complete(operation_id):
    operation = db.session.get(CatalogueOperation, operation_id)
    if not can_complete(operation) or not readiness()["ready"]:
        raise ValueError("Initial scan has not completed safely for the current configuration. Review the operation or retry.")
    value = state()
    value.update(status="complete", operation_id=operation.id)
    if _path().is_symlink():
        raise ValueError("Setup state is unsafe; check application data.")
    atomic_write_json(_path(), value)
