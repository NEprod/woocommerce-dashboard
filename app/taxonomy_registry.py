"""Opt-in, read-only local taxonomy snapshots. No scanner or Woo integration."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Mapping
import unicodedata

from jsonschema import Draft202012Validator


MAX_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 30000
MAX_DEFINITIONS = 2000
KINDS = ("categories", "storefront_collections", "attributes", "tags")
SCHEMA_PATH = Path(__file__).parent / "resources/taxonomy/registry.schema.json"


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class RegistrySnapshot:
    data: Mapping
    digest: str
    schema_version: int = 1


@dataclass(frozen=True)
class RegistryResult:
    status: str
    issues: tuple[RegistryIssue, ...] = ()
    snapshot: RegistrySnapshot | None = None

    @property
    def available(self):
        return self.snapshot is not None


class _Invalid(ValueError):
    def __init__(self, code, path, message):
        self.issue = RegistryIssue(code, path, message)


def _fail(code, path, message):
    raise _Invalid(code, path, message)


def _normal(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key", "$", "Duplicate JSON object key.")
        result[key] = value
    return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _validate(data):
    pending, nodes = [(data, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            _fail("structure_limit", "$", "Registry structure exceeds supported bounds.")
        if isinstance(value, dict):
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
    if not isinstance(data, dict):
        _fail("schema", "$", "Registry must be an object.")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        _fail("unsupported_version", "$.schema_version", "Expected registry schema version 1.")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    error = next(Draft202012Validator(schema).iter_errors(data), None)
    if error:
        # Never echo an untrusted value, key, URL or underlying exception.
        path = "$." + str(error.path[0]) if error.path and error.path[0] in KINDS else "$"
        _fail("schema", path, "Definition does not satisfy the registry schema.")
    total = sum(len(data[kind]) for kind in KINDS) + sum(len(a["terms"]) for a in data["attributes"])
    if total > MAX_DEFINITIONS:
        _fail("definition_limit", "$", "Registry exceeds 2000 definitions including terms.")
    keys = set()
    for kind in KINDS:
        for row in data[kind]:
            if row["key"] in keys:
                _fail("duplicate_key", "$." + kind, "Top-level stable keys must be unique across kinds.")
            keys.add(row["key"])
    categories = {row["key"]: row for row in data["categories"]}
    paths, depths = {}, {}
    for key in categories:
        trail, cursor = [], key
        while cursor is not None and cursor not in paths:
            if cursor not in categories:
                _fail("invalid_parent", "$.categories", "Category parent must reference a category key.")
            if cursor in trail:
                _fail("category_cycle", "$.categories", "Category hierarchy contains a cycle.")
            trail.append(cursor)
            if len(trail) > MAX_DEPTH:
                _fail("hierarchy_limit", "$.categories", "Category hierarchy exceeds 32 levels.")
            cursor = categories[cursor]["parent"]
        prefix = paths.get(cursor, "")
        for member in reversed(trail):
            parent = categories[member]["parent"]
            depths[member] = depths.get(parent, 0) + 1
            if depths[member] > MAX_DEPTH:
                _fail("hierarchy_limit", "$.categories", "Category hierarchy exceeds 32 levels.")
            prefix = prefix + " > " + categories[member]["name"] if prefix else categories[member]["name"]
            paths[member] = prefix
    for kind in KINDS:
        _validate_scope(data[kind], "$." + kind, paths if kind == "categories" else None)
    for i, attribute in enumerate(data["attributes"]):
        _validate_scope(attribute["terms"], f"$.attributes[{i}].terms")


def _validate_scope(rows, path, category_paths=None):
    keys, slugs, labels = set(), set(), set()
    for row in rows:
        if row["key"] in keys:
            _fail("duplicate_key", path, "Stable key repeated within its scope.")
        keys.add(row["key"])
        if row["slug"] in slugs:
            _fail("duplicate_slug", path, "Slug repeated within its taxonomy scope.")
        slugs.add(row["slug"])
        if not _normal(row["name"]):
            _fail("empty_label", path, "Display names must contain visible text.")
        if category_paths is not None and ">" in row["name"]:
            _fail("category_separator", path, "Category names cannot contain the hierarchy separator.")
        name = category_paths[row["key"]] if category_paths is not None else row["name"]
        for label in [name, *row.get("aliases", [])]:
            normalized = _normal(label)
            if not normalized:
                _fail("empty_label", path, "Aliases must contain visible text.")
            if normalized in labels:
                _fail("normalization_collision", path, "Name or alias has an ambiguous normalized identity.")
            labels.add(normalized)


def _result(status, code, message):
    return RegistryResult(status, (RegistryIssue(code, "registry.json", message),))


def load_registry(root, *, excluded_roots=()):
    """Load only registry.json below a trusted configured root; never create files.

    No caller-supplied filename or URL is accepted. Directory-descriptor walking
    rejects symlinks at every component and pins reads to the opened directory.
    """
    if root is None or str(root).strip() == "":
        return _result("not_configured", "not_configured", "Taxonomy root is not configured.")
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        return _result("invalid_root", "unsafe_root", "Use a separate absolute taxonomy directory without traversal.")
    directory = file_fd = None
    reading_file = False
    try:
        for other in ("/catalogue", "/output", "/app/instance", "/intake", *excluded_roots):
            if not other:
                continue
            blocked = Path(other).resolve()
            if path == blocked or path in blocked.parents or blocked in path.parents:
                return _result("invalid_root", "root_overlap", "Taxonomy storage must be separate from other authored/runtime roots.")
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        reading_file = True
        file_fd = os.open("registry.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            return _result("invalid", "not_regular_file", "Registry must be a regular file.")
        if before.st_size > MAX_BYTES:
            return _result("invalid", "size_limit", "Registry exceeds the 1 MiB limit.")
        with os.fdopen(file_fd, "rb") as handle:
            file_fd = None
            raw = handle.read(MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        if len(raw) > MAX_BYTES:
            return _result("invalid", "size_limit", "Registry exceeds the 1 MiB limit.")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            return _result("inaccessible", "changed_during_read", "Registry changed while being read; load again.")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=lambda _: _fail("malformed_json", "$", "Non-finite JSON numbers are not supported."))
        _validate(data)
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot = RegistrySnapshot(_freeze(data), digest)
        # Access is observational only; this slice never tests writability by writing.
        writable = (bool(before.st_mode & 0o222) and bool(os.fstat(directory).st_mode & 0o222)
                    and os.access(path, os.W_OK) and os.access(path / "registry.json", os.W_OK))
        return RegistryResult("ready" if writable else "read_only", snapshot=snapshot)
    except _Invalid as error:
        return RegistryResult("invalid", (error.issue,))
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        return _result("invalid", "malformed_json", "Registry is not valid bounded UTF-8 JSON.")
    except OSError as error:
        if error.errno == errno.ENOENT:
            return _result("registry_missing" if reading_file else "root_missing", "missing", "Registry file is missing." if reading_file else "Taxonomy root is missing.")
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            return _result("invalid_root" if not reading_file else "invalid", "unsafe_path", "Registry path contains a symlink or non-directory component.")
        return _result("inaccessible", "inaccessible", "Registry storage could not be read.")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory is not None:
            os.close(directory)


def load_configured_registry():
    """Explicit application entry point; intentionally not called at startup."""
    from flask import current_app
    from app.models import Settings

    settings = Settings.query.first()
    return load_registry(current_app.config.get("TAXONOMY_ROOT"), excluded_roots=(
        current_app.instance_path, current_app.config.get("INTAKE_ROOT"),
        settings.product_folder if settings else None,
        settings.output_folder if settings else None,
    ))
