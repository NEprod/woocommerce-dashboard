"""Registry-only editing/import. Catalogue assignments and Woo are not consumers."""
from contextlib import contextmanager
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid

from flask import current_app
from app import taxonomy_registry as registry


class RegistryEditError(ValueError):
    """Bounded, user-safe diagnostics only."""


def decode(raw):
    if len(raw) > registry.MAX_BYTES:
        raise RegistryEditError("Document exceeds the 1 MiB limit.")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=registry._object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise RegistryEditError("Invalid JSON, duplicate object keys or unsupported values.") from None


def validate(data):
    try:
        registry._validate(data)
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        if len(raw) > registry.MAX_BYTES:
            raise RegistryEditError("Registry exceeds the 1 MiB limit.")
        return raw
    except registry._Invalid as error:
        raise RegistryEditError(error.issue.message) from None


def counts(data):
    return {**{kind: len(data[kind]) for kind in registry.KINDS},
            "terms": sum(len(a["terms"]) for a in data["attributes"])}


def category_paths(data):
    rows = {r["key"]: r for r in data["categories"]}
    def path(row):
        return path(rows[row["parent"]]) + " > " + row["name"] if row["parent"] else row["name"]
    return {key: path(row) for key, row in rows.items()}


def change_summary(before, after):
    def flatten(data):
        values = {}
        for kind in registry.KINDS:
            for row in data[kind]:
                values[(kind, row["key"])] = row
        return values
    old, new = flatten(before), flatten(after)
    return {"added": len(new.keys() - old.keys()), "removed": len(old.keys() - new.keys()),
            "changed": sum(old[k] != new[k] for k in old.keys() & new.keys())}


def empty_registry():
    return {"schema_version": 1, **{kind: [] for kind in registry.KINDS}}


def _slug(name):
    # Slugs are a proposed serialization, never a silent merge of vocabulary.
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value or len(value) > 88:
        raise RegistryEditError("A source label requires a reviewed manual slug.")
    return value


def bootstrap(seed_raw, csv_raw):
    """Explicit uploaded seed conversion; never reads a host reference path."""
    seed = decode(seed_raw)
    if not isinstance(seed, dict) or seed.get("registry_type") != "tlc_taxonomy_seed" or type(seed.get("schema_version")) is not int or seed["schema_version"] != 1:
        raise RegistryEditError("Expected TLC taxonomy seed version 1.")
    if len(csv_raw) > registry.MAX_BYTES:
        raise RegistryEditError("Category CSV exceeds the 1 MiB limit.")
    try:
        reader = csv.DictReader(io.StringIO(csv_raw.decode("utf-8-sig")))
        expected = ["name", "parent", "slug", "category_path", "level", "sort_order"]
        if reader.fieldnames != expected:
            raise ValueError()
        csv_rows = list(reader)
        source = seed["categories"]
        if not isinstance(source, list) or len(source) > registry.MAX_DEFINITIONS:
            raise ValueError()
        def normalized(row):
            return (row["name"], row["parent"] or None, row["slug"], row["category_path"],
                    int(row["level"]), int(row["sort_order"]))
        left, right = [normalized(r) for r in source], [normalized(r) for r in csv_rows]
        if len(set(left)) != len(left) or sorted(left, key=repr) != sorted(right, key=repr):
            raise RegistryEditError("Seed categories do not exactly match the supplied CSV.")
        result = empty_registry()
        names = {r["name"]: "cat-" + r["slug"] for r in source}
        if len(names) != len(source):
            raise RegistryEditError("Ambiguous category parent names require manual review.")
        for row in source:
            result["categories"].append({"key": names[row["name"]], "name": row["name"],
                "slug": row["slug"], "parent": names[row["parent"]] if row["parent"] else None,
                "order": int(row["sort_order"]), "state": "draft"})
        for attribute in seed["navigation_attributes"]:
            slug = _slug(attribute["name"])
            if type(attribute["navigation"]) is not bool or not isinstance(attribute["terms"], list):
                raise ValueError()
            result["attributes"].append({"key": "attr-" + slug, "name": attribute["name"],
                "slug": slug, "navigation": attribute["navigation"], "visible_default": False,
                "state": "draft", "terms": [{"key": "term-" + _slug(term), "name": term,
                    "slug": _slug(term), "state": "draft", "order": i}
                    for i, term in enumerate(attribute["terms"])]})
        validate(result)
        paths = category_paths(result)
        for row in source:
            path = paths[names[row["name"]]]
            if path != row["category_path"] or path.count(" > ") != int(row["level"]):
                raise RegistryEditError("Source category path or hierarchy level is inconsistent.")
        return result
    except (KeyError, TypeError, ValueError, UnicodeError, csv.Error) as error:
        if isinstance(error, RegistryEditError):
            raise
        raise RegistryEditError("Seed/CSV structure is invalid or needs semantic review.") from None


def edit_definition(data, kind, key, attribute, form):
    """One guided edit; keys cannot be renamed. No product references are read."""
    if kind not in (*registry.KINDS, "terms"):
        raise RegistryEditError("Unknown definition type.")
    if kind == "terms":
        parent = next((a for a in data["attributes"] if a["key"] == attribute), None)
        if parent is None:
            raise RegistryEditError("Attribute no longer exists.")
        rows = parent["terms"]
    else:
        rows = data[kind]
    old = next((r for r in rows if r["key"] == key), None) if key else None
    if key and old is None:
        raise RegistryEditError("Definition no longer exists.")
    if form.get("action") == "remove":
        if old is None:
            raise RegistryEditError("Select an existing definition to remove.")
        rows.remove(old)
    else:
        row = {"key": key or form.get("key", ""), "name": form.get("name", ""),
               "slug": form.get("slug", ""), "state": form.get("state", "draft"),
               "aliases": form.get("aliases", "").splitlines()}
        if form.get("order", ""):
            try:
                row["order"] = int(form["order"])
            except ValueError:
                raise RegistryEditError("Order must be a non-negative integer.") from None
        if kind == "categories":
            row["parent"] = form.get("parent") or None
        if kind == "attributes":
            row.update(navigation=form.get("navigation") == "yes",
                       visible_default=form.get("visible_default") == "yes",
                       terms=old["terms"] if old else [])
        if old is None:
            rows.append(row)
        else:
            rows[rows.index(old)] = row
    validate(data)
    return data


@contextmanager
def opened_root():
    """Validate configured separation, then pin every component without symlinks."""
    status = registry.load_configured_registry()
    if status.status not in {"ready", "registry_missing", "read_only"}:
        raise RegistryEditError("Registry storage is unavailable or invalid; resolve readiness first.")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(current_app.config["TAXONOMY_ROOT"]).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd, name="registry.json"):
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(file_fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > registry.MAX_BYTES:
            raise RegistryEditError("Registry storage is not a bounded regular file.")
        raw = handle.read(registry.MAX_BYTES + 1)
        if len(raw) > registry.MAX_BYTES:
            raise RegistryEditError("Registry exceeds the read bound.")
        return raw


def _revision(fd, raw):
    info = os.fstat(fd)
    # Includes mount/directory identity and byte revision, stricter than semantic digest.
    root = str(current_app.config["TAXONOMY_ROOT"])
    return hashlib.sha256(f"{root}:{info.st_dev}:{info.st_ino}:".encode() + (raw if raw is not None else b"MISSING")).hexdigest()


def current_source():
    with opened_root() as fd:
        raw = _read(fd)
        data = decode(raw) if raw is not None else empty_registry()
        validate(data)
        return data, _revision(fd, raw), raw is None


def _write_new(fd, name, raw, mode=0o600):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if _read(fd, name) != raw:
        raise RegistryEditError("Registry backup/staging verification failed; no save permitted.")


def save_reviewed(data, expected_revision, *, bootstrap_only=False):
    """Same-directory atomic save, verified backup and guarded rollback.

    Shared operation lease is held by the caller. Directory flock also serializes
    registry writers across processes. External writers must coordinate separately.
    """
    raw = validate(data)
    staged = ".registry-stage-" + uuid.uuid4().hex
    with opened_root() as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = _read(fd)
        if _revision(fd, before) != expected_revision or (bootstrap_only and before is not None):
            raise RegistryEditError("Registry changed since review. Review the current source again.")
        if before == raw:
            raise RegistryEditError("Proposed registry matches the current source; nothing to save.")
        if not os.fstat(fd).st_mode & 0o222 or (before is not None and not os.stat("registry.json", dir_fd=fd, follow_symlinks=False).st_mode & 0o222):
            raise RegistryEditError("Registry storage is read-only.")
        backup = None
        try:
            if before is not None:
                backup = ".registry-backup-" + uuid.uuid4().hex + ".json"
                _write_new(fd, backup, before)
                validate(decode(_read(fd, backup)))
                os.fsync(fd)
            _write_new(fd, staged, raw)
            validate(decode(_read(fd, staged)))
            with opened_root() as current_fd:
                if (os.fstat(current_fd).st_dev, os.fstat(current_fd).st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                    raise RegistryEditError("Taxonomy mount changed during save. Review again.")
            if _read(fd) != before:
                raise RegistryEditError("Registry changed during save. Review again.")
            if before is None:
                # No-clobber installation: another writer cannot win then be overwritten.
                os.link(staged, "registry.json", src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(staged, dir_fd=fd)
            else:
                os.replace(staged, "registry.json", src_dir_fd=fd, dst_dir_fd=fd)
            try:
                observed = _read(fd)
                validate(decode(observed))
                if observed != raw:
                    raise RegistryEditError("Written registry did not verify.")
                os.fsync(fd)
            except Exception:
                # Never overwrite a concurrent external change during recovery.
                if _read(fd) == raw:
                    if backup:
                        os.replace(backup, "registry.json", src_dir_fd=fd, dst_dir_fd=fd)
                    else:
                        os.unlink("registry.json", dir_fd=fd)
                    os.fsync(fd)
                raise RegistryEditError("Post-write verification failed. Inspect registry and retained backup before retrying.") from None
            # Only our exact-pattern, regular, valid backups are retention candidates.
            # Failed verification never prunes recovery evidence.
            try:
                candidates = []
                with os.scandir(fd) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 2000:
                            break
                        if re.fullmatch(r"\.registry-backup-[0-9a-f]{32}\.json", entry.name) and entry.is_file(follow_symlinks=False):
                            validate(decode(_read(fd, entry.name)))
                            candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name))
                for _, name in sorted(candidates, reverse=True)[10:]:
                    os.unlink(name, dir_fd=fd)
            except (OSError, RegistryEditError):
                current_app.logger.warning("Registry saved; backup retention requires local review.")
            return backup
        finally:
            try:
                os.unlink(staged, dir_fd=fd)
            except FileNotFoundError:
                pass
