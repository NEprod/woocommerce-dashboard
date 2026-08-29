"""Read-only, intake-confined previews for pre-catalogue image preparation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from flask import current_app
from itsdangerous import BadData, URLSafeSerializer
from PIL import Image, UnidentifiedImageError

from app.utils.catalogue_paths import is_reserved_directory_name
from app.utils.image_resolution import resolve_single_variable_image_layout


INTAKE_CONTAINER_ROOT = Path("/intake")
PREPARED_DIRECTORY = "Prepared"
INTAKE_STAGING_DIRECTORY = ".catalogue-intake-staging"
SUPPORTED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_PREVIEW_FILES = 5000
MAX_METADATA_BYTES = 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)


def _ordered(value):
    return value.casefold(), value


def configured_intake_root() -> Path:
    """Return the fixed production root or an explicit test-only override."""

    return Path(current_app.config.get("INTAKE_ROOT", str(INTAKE_CONTAINER_ROOT)))


def _mountinfo_contains(path: Path) -> bool:
    """Detect a Linux mountpoint without creating or resolving a fallback path."""

    try:
        target = str(path)
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) > 4:
                mounted = (
                    fields[4]
                    .replace("\\040", " ")
                    .replace("\\011", "\t")
                    .replace("\\012", "\n")
                    .replace("\\134", "\\")
                )
                if mounted == target:
                    return True
    except OSError:
        pass
    return os.path.ismount(path)


def intake_readiness(root=None, *, mounted=None, access_check=os.access):
    root = Path(root) if root is not None else configured_intake_root()
    if not root.exists():
        return _readiness("unavailable", False, False, "Catalogue Intake is not mounted")
    try:
        root_stat = root.lstat()
    except OSError:
        return _readiness("invalid", False, False, "Catalogue Intake is unavailable")
    if stat.S_ISLNK(root_stat.st_mode):
        return _readiness("unsafe", False, False, "Catalogue Intake mount is unsafe")
    if not stat.S_ISDIR(root_stat.st_mode):
        return _readiness("invalid", False, False, "Catalogue Intake mount is invalid")
    if mounted is None:
        if "INTAKE_TEST_MOUNTED" in current_app.config:
            mounted = bool(current_app.config["INTAKE_TEST_MOUNTED"])
        else:
            mounted = _mountinfo_contains(root)
    if not mounted:
        return _readiness("unavailable", False, False, "Catalogue Intake is not mounted")
    readable = bool(access_check(root, os.R_OK))
    writable = bool(access_check(root, os.W_OK)) if readable else False
    if not readable:
        return _readiness("invalid", False, False, "Catalogue Intake is not readable")
    if not writable:
        return _readiness("read_only", True, False, "Catalogue Intake is mounted but read-only")
    return _readiness("writable", True, True, "Catalogue Intake is available")


def _readiness(state, readable, writable, message):
    return {
        "state": state,
        "mounted": state not in {"unavailable", "invalid", "unsafe"},
        "readable": readable,
        "writable": writable,
        "message": message,
        "label": {
            "writable": "Mounted and writable",
            "read_only": "Mounted but read-only",
            "unavailable": "Unavailable",
            "invalid": "Invalid",
            "unsafe": "Unsafe",
        }[state],
    }


def _decode_path(value) -> str:
    value = "" if value is None else str(value)
    for _index in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def _portable_parts(value) -> tuple[str, ...]:
    value = _decode_path(value)
    if "\\" in value:
        raise ValueError("Invalid intake-relative path")
    if _CONTROL.search(value) or value.startswith("/") or _DRIVE.match(value):
        raise ValueError("Invalid intake-relative path")
    if value in {"", "."}:
        return ()
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Invalid intake-relative path")
    return path.parts


def _within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _safe_root(root: Path) -> Path:
    try:
        if stat.S_ISLNK(root.lstat().st_mode):
            raise ValueError("Unsafe intake root")
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ValueError("Catalogue Intake is unavailable") from error
    if not resolved.is_dir():
        raise ValueError("Catalogue Intake is invalid")
    return resolved


def resolve_intake_folder(root, relative="") -> tuple[Path, str]:
    root = _safe_root(Path(root))
    parts = _portable_parts(relative)
    current = root
    for part in parts:
        candidate = current / part
        try:
            candidate_stat = candidate.lstat()
        except OSError as error:
            raise ValueError("Intake folder is unavailable") from error
        if stat.S_ISLNK(candidate_stat.st_mode) or not stat.S_ISDIR(candidate_stat.st_mode):
            raise ValueError("Unsafe intake folder")
        current = candidate
    resolved = current.resolve(strict=True)
    if not _within(resolved, root):
        raise ValueError("Unsafe intake folder")
    return resolved, PurePosixPath(*parts).as_posix() if parts else ""


@lru_cache(maxsize=8192)
def _verified_image(path_value: str, modified_ns: int, size: int) -> bool:
    del modified_ns, size
    try:
        with Image.open(path_value) as image:
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError):
        return False
    return True


def _classify_entry(path: Path):
    name = path.name
    try:
        entry_stat = path.lstat()
    except OSError:
        return "unreadable", None
    mode = entry_stat.st_mode
    if stat.S_ISLNK(mode):
        return "unsafe", entry_stat
    if stat.S_ISDIR(mode):
        return ("hidden" if name.startswith(".") else "directory"), entry_stat
    if not stat.S_ISREG(mode) or entry_stat.st_nlink > 1:
        return "unsafe", entry_stat
    if name.startswith("."):
        return "hidden", entry_stat
    if not os.access(path, os.R_OK):
        return "unreadable", entry_stat
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return "unsupported", entry_stat
    try:
        before = path.stat()
        valid = _verified_image(str(path), before.st_mtime_ns, before.st_size)
        after = path.stat()
    except OSError:
        return "unreadable", entry_stat
    if (before.st_mtime_ns, before.st_size, before.st_ino) != (
        after.st_mtime_ns,
        after.st_size,
        after.st_ino,
    ):
        return "changed", after
    return ("image" if valid else "corrupt"), after


def _issue(name, category, message, *, code=None, state="warning"):
    return {
        "name": name,
        "category": category,
        "message": message,
        "code": code or category,
        "state": state,
    }


def browse_intake(root, relative=""):
    folder, canonical = resolve_intake_folder(root, relative)
    directories = []
    images = []
    issues = []
    counts = {
        "supported_images": 0,
        "child_directories": 0,
        "unsupported_entries": 0,
        "hidden_system": 0,
        "corrupt_images": 0,
        "unsafe_entries": 0,
        "unreadable_entries": 0,
    }
    try:
        entries = sorted(folder.iterdir(), key=lambda item: _ordered(item.name))
    except OSError as error:
        raise ValueError("Intake folder cannot be read") from error
    for entry in entries:
        if entry.name == INTAKE_STAGING_DIRECTORY:
            continue
        kind, entry_stat = _classify_entry(entry)
        if kind == "directory":
            child_rel = _join_rel(canonical, entry.name)
            directories.append({"name": entry.name, "path": child_rel})
            counts["child_directories"] += 1
        elif kind == "image":
            file_rel = _join_rel(canonical, entry.name)
            images.append(
                {
                    "name": entry.name,
                    "path": file_rel,
                    "size": entry_stat.st_size,
                    "mtime_ns": entry_stat.st_mtime_ns,
                    "extension": entry.suffix,
                    "thumbnail_token": _token_for_path(file_rel),
                }
            )
            counts["supported_images"] += 1
        else:
            count_key = {
                "unsupported": "unsupported_entries",
                "hidden": "hidden_system",
                "corrupt": "corrupt_images",
                "unsafe": "unsafe_entries",
                "changed": "unsafe_entries",
                "unreadable": "unreadable_entries",
            }[kind]
            counts[count_key] += 1
            message = {
                "unsupported": "Unsupported file type",
                "hidden": "Hidden or system entry ignored",
                "corrupt": "Supported extension but invalid image content",
                "unsafe": "Unsafe filesystem entry ignored",
                "changed": "Source changed while it was inspected",
                "unreadable": "Entry could not be read",
            }[kind]
            issues.append(_issue(entry.name, kind, message))
    parts = _portable_parts(canonical)
    breadcrumbs = [{"label": "Catalogue Intake", "path": ""}]
    for index, part in enumerate(parts):
        breadcrumbs.append({"label": part, "path": PurePosixPath(*parts[: index + 1]).as_posix()})
    parent = PurePosixPath(*parts[:-1]).as_posix() if len(parts) > 1 else ""
    return {
        "path": canonical,
        "display_path": canonical or "Catalogue Intake",
        "folder_name": folder.name,
        "parent": parent,
        "breadcrumbs": breadcrumbs,
        "directories": directories,
        "images": images,
        "issues": issues,
        "counts": counts,
        "empty": not entries,
    }


def _join_rel(*parts):
    safe = [str(part).strip("/") for part in parts if str(part).strip("/")]
    return PurePosixPath(*safe).as_posix() if safe else ""


def _safe_component(value, *, lowercase=False):
    value = unicodedata.normalize("NFC", str(value)).strip()
    value = re.sub(r"\s+", "_", value)
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or _CONTROL.search(value)
        or _DRIVE.match(value)
    ):
        raise ValueError("Unsafe filename component")
    return value.lower() if lowercase else value


def _safe_folder_component(value):
    """Normalise a visible folder name without changing meaningful inner spaces."""

    value = unicodedata.normalize("NFC", str(value)).strip()
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or _CONTROL.search(value)
        or _DRIVE.match(value)
    ):
        raise ValueError("Unsafe folder component")
    return value


def normalize_prefix(value):
    value = _decode_path(value)
    if "/" in value or "\\" in value or _CONTROL.search(value) or _DRIVE.match(value.strip()):
        raise ValueError("Prefix contains an unsafe path value")
    normalised = _safe_component(value, lowercase=True)
    if normalised.endswith(".") or _WINDOWS_RESERVED.match(normalised):
        raise ValueError("Prefix contains an unsafe filesystem name")
    return normalised


def _prepared_result(root: Path, source_name: str):
    result_name = _safe_folder_component(source_name)
    prepared = root / PREPARED_DIRECTORY
    existing = []
    if prepared.exists():
        kind, _entry_stat = _classify_entry(prepared)
        if kind != "directory":
            raise ValueError("Prepared result root is unsafe")
        existing = [entry.name for entry in prepared.iterdir()]
    folded = {unicodedata.normalize("NFC", name).casefold() for name in existing}
    conflict = result_name.casefold() in folded
    candidate = result_name
    sequence = 2
    while unicodedata.normalize("NFC", candidate).casefold() in folded:
        candidate = f"{result_name} ({sequence})"
        sequence += 1
    return candidate, conflict


def _digest(kind, selected, result_name, mappings, issues, extra=None):
    payload = {
        "kind": kind,
        "selected": selected,
        "result_name": result_name,
        "mappings": [
            {
                key: item.get(key)
                for key in (
                    "source_relpath",
                    "size",
                    "mtime_ns",
                    "destination_relpath",
                    "legacy_base",
                    "proposed_group",
                    "legacy_filename",
                    "recommended_filename",
                    "sequence",
                    "state",
                )
            }
            for item in mappings
        ],
        "issues": [
            {key: issue.get(key) for key in ("code", "name", "state", "message")}
            for issue in issues
        ],
        "extra": extra or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def grouping_preview(root, relative=""):
    root = _safe_root(Path(root))
    browser = browse_intake(root, relative)
    result_name, result_conflict = _prepared_result(root, browser["folder_name"])
    mappings = []
    issues = list(browser["issues"])
    if not browser["path"]:
        issues.append(_issue("Catalogue Intake", "selection", "Select a child intake folder before confirming grouping", code="source_root", state="blocking"))
    bases = {}
    legacy_by_safe = {}
    for image in browser["images"]:
        stem = Path(image["name"]).stem
        legacy = re.sub(r"\d+$", "", stem)
        try:
            proposed = _safe_folder_component(legacy)
            state = "ready"
        except ValueError:
            proposed = ""
            state = "blocking"
            issues.append(_issue(image["name"], "unsafe", "Trailing-number removal produces an unsafe or empty group", code="unsafe_group", state="blocking"))
        mapping = {
            "source_name": image["name"],
            "source_relpath": image["path"],
            "size": image["size"],
            "mtime_ns": image["mtime_ns"],
            "legacy_base": legacy,
            "proposed_group": proposed,
            "destination_relpath": _join_rel(PREPARED_DIRECTORY, result_name, proposed, image["name"]) if proposed else None,
            "reserved_parent": bool(proposed and is_reserved_directory_name(proposed)),
            "state": state,
            "thumbnail_token": image["thumbnail_token"],
        }
        mappings.append(mapping)
        if proposed:
            bases.setdefault(proposed, []).append(mapping)
            legacy_by_safe.setdefault(unicodedata.normalize("NFC", proposed).casefold(), set()).add(legacy)

    folded_groups = {}
    for group in bases:
        folded_groups.setdefault(unicodedata.normalize("NFC", group).casefold(), []).append(group)
    for folded, groups in folded_groups.items():
        members = [item for group in groups for item in bases[group]]
        if len(groups) > 1:
            issues.append(_issue(", ".join(groups), "ambiguity", "Case-insensitive grouping ambiguity", code="case_ambiguity", state="blocking"))
            for item in members:
                item["state"] = "blocking"
        if len(legacy_by_safe.get(folded, set())) > 1:
            issues.append(_issue(", ".join(sorted(legacy_by_safe[folded], key=_ordered)), "ambiguity", "Different legacy bases normalise to the same proposed group", code="normalisation_collision", state="blocking"))
            for item in members:
                item["state"] = "blocking"

    parent_groups = [group for group in bases if is_reserved_directory_name(group)]
    if len(parent_groups) > 1:
        issues.append(_issue(", ".join(parent_groups), "ambiguity", "Multiple Parent case variants are ambiguous", code="duplicate_parent", state="blocking"))
        for group in parent_groups:
            for item in bases[group]:
                item["state"] = "blocking"
    elif parent_groups:
        issues.append(_issue(parent_groups[0], "reserved", "This proposal creates the scanner-reserved Parent folder. Confirm that these files are intended as parent-product images.", code="reserved_parent"))
        for item in bases[parent_groups[0]]:
            if item["state"] == "ready":
                item["state"] = "warning"

    for items in bases.values():
        single = len(items) == 1
        for item in items:
            item["single_image"] = single
    for directory in browser["directories"]:
        issues.append(_issue(directory["name"], "directory", "Child directory is not part of a loose-image grouping preview", code="child_directory"))
    if result_conflict:
        issues.append(_issue(browser["folder_name"], "conflict", f"The default prepared result already exists; the duplicate-safe proposal is {result_name}", code="existing_result"))

    groups = []
    for name in sorted(bases, key=_ordered):
        items = sorted(bases[name], key=lambda item: _ordered(item["source_name"]))
        groups.append(
            {
                "name": name,
                "destination": _join_rel(PREPARED_DIRECTORY, result_name, name),
                "file_count": len(items),
                "single_image": len(items) == 1,
                "state": "blocking" if any(item["state"] == "blocking" for item in items) else "warning" if any(item["state"] == "warning" for item in items) else "ready",
                "files": items,
            }
        )
    mappings.sort(key=lambda item: _ordered(item["source_name"]))
    digest = _digest("group", browser["path"], result_name, mappings, issues)
    blocking = grouping_confirmation_blockers({"browser": browser, "mappings": mappings, "issues": issues})
    return {
        "kind": "group",
        "preview_only": True,
        "browser": browser,
        "source": browser["display_path"],
        "result_name": result_name,
        "proposed_result": _join_rel(PREPARED_DIRECTORY, result_name),
        "groups": groups,
        "mappings": mappings,
        "issues": sorted(issues, key=lambda item: (_ordered(item["name"]), item["code"])),
        "digest": digest,
        "ready": bool(mappings) and not blocking,
        "compatibility": {
            "state": "warning" if issues else "compatible",
            "label": "Scanner-compatible based on current visible structure" if mappings else "No supported loose images found",
            "detail": "Grouping does not determine collection type or image-attribute order.",
        },
    }


def grouping_confirmation_blockers(preview):
    """Return safe reasons why a grouping proposal cannot become a mutation."""

    blockers = []
    if not (preview.get("browser") or {}).get("path"):
        blockers.append("Select a child intake folder")
    if not preview.get("mappings"):
        blockers.append("No supported loose images were found")
    for mapping in preview.get("mappings") or ():
        if mapping.get("state") == "blocking":
            blockers.append(mapping.get("source_name") or "Unsafe grouping proposal")
    for issue in preview.get("issues") or ():
        if issue.get("state") == "blocking" or issue.get("category") in {"unsafe", "changed", "unreadable"}:
            blockers.append(issue.get("message") or issue.get("name") or "Unsafe intake entry")
    return list(dict.fromkeys(blockers))


def _walk_intake_images(root: Path, folder: Path, selected: str):
    images = []
    issues = []
    directories = []
    stack = [(folder, ())]
    inspected = 0
    while stack:
        current, rel_parts = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: _ordered(item.name))
        except OSError:
            issues.append(_issue(_join_rel(*rel_parts), "unreadable", "Directory could not be read", state="blocking"))
            continue
        child_dirs = []
        for entry in entries:
            if entry.name == INTAKE_STAGING_DIRECTORY:
                continue
            kind, entry_stat = _classify_entry(entry)
            rel = _join_rel(selected, *rel_parts, entry.name)
            if kind == "directory":
                child_dirs.append((entry, rel_parts + (entry.name,)))
                directories.append(_join_rel(*rel_parts, entry.name))
            elif kind == "image":
                inspected += 1
                if inspected > MAX_PREVIEW_FILES:
                    raise ValueError("Preview exceeds the supported file limit")
                images.append(
                    {
                        "name": entry.name,
                        "source_relpath": rel,
                        "folder_parts": rel_parts,
                        "size": entry_stat.st_size,
                        "mtime_ns": entry_stat.st_mtime_ns,
                        "extension": entry.suffix,
                        "thumbnail_token": _token_for_path(rel),
                    }
                )
            else:
                issues.append(_issue(rel, kind, {
                    "hidden": "Hidden or system entry ignored",
                    "unsupported": "Unsupported file type",
                    "corrupt": "Supported extension but invalid image content",
                    "unsafe": "Unsafe filesystem entry ignored",
                    "changed": "Source changed while it was inspected",
                    "unreadable": "Entry could not be read",
                }[kind], state="blocking" if kind in {"unsafe", "changed"} else "warning"))
        for child in reversed(child_dirs):
            stack.append(child)
    images.sort(key=lambda item: tuple(_ordered(part) for part in (*item["folder_parts"], item["name"])))
    directories.sort(key=lambda value: tuple(_ordered(part) for part in PurePosixPath(value).parts))
    return images, issues, directories


def _metadata_context(folder: Path):
    metadata = folder / "product_info.json"
    try:
        metadata_stat = metadata.lstat()
        if (
            not stat.S_ISREG(metadata_stat.st_mode)
            or stat.S_ISLNK(metadata_stat.st_mode)
            or metadata_stat.st_nlink > 1
            or metadata_stat.st_size > MAX_METADATA_BYTES
        ):
            return {}
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalised_components(parts):
    return [_safe_component(part, lowercase=True) for part in parts]


def rename_preview(root, relative, prefix):
    root = _safe_root(Path(root))
    folder, selected = resolve_intake_folder(root, relative)
    normalised_prefix = normalize_prefix(prefix)
    result_name = folder.name
    images, issues, directories = _walk_intake_images(root, folder, selected)
    metadata = _metadata_context(folder)
    collection_type = metadata.get("collection_type") if isinstance(metadata.get("collection_type"), str) else None
    image_attributes = metadata.get("image_attributes") if isinstance(metadata.get("image_attributes"), list) and all(isinstance(value, str) for value in metadata.get("image_attributes")) else None

    root_directories = [part for part in directories if len(PurePosixPath(part).parts) == 1]
    parent_variants = [value for value in root_directories if is_reserved_directory_name(value)]
    duplicate_parent = len(parent_variants) > 1
    if duplicate_parent:
        issues.append(_issue(", ".join(parent_variants), "ambiguity", "Multiple collection-root Parent variants are ambiguous", code="duplicate_parent", state="blocking"))

    by_directory = {}
    for image in images:
        by_directory.setdefault(image["folder_parts"], []).append(image)
    mappings = []
    for folder_parts in sorted(by_directory, key=lambda parts: tuple(_ordered(part) for part in parts)):
        rows = sorted(by_directory[folder_parts], key=lambda item: _ordered(item["name"]))
        for sequence, image in enumerate(rows, start=1):
            hierarchy_type = "unclassified"
            hierarchy_state = "warning"
            hierarchy_note = "Cannot fully validate image-attribute order without collection metadata"
            recommended = None
            legacy = None
            if folder_parts:
                legacy_parts = (folder.name,) if is_reserved_directory_name(folder_parts[-1]) else folder_parts
                legacy_component = "_".join(_normalised_components(legacy_parts))
                legacy = f"{normalised_prefix}_{legacy_component}_{sequence:02d}{Path(image['name']).suffix.lower()}".lower()
                is_root_parent = len(folder_parts) == 1 and is_reserved_directory_name(folder_parts[0])
                if is_root_parent:
                    hierarchy_type = "parent"
                    hierarchy_state = "blocking" if duplicate_parent else "ready"
                    hierarchy_note = "Collection-root Parent folder recognised case-insensitively"
                    recommended_parts = (folder.name,)
                else:
                    recommended_parts = folder_parts
                    if collection_type in {"Simple", "Variable Collection"}:
                        if len(folder_parts) == 1:
                            hierarchy_type = "product"
                            hierarchy_state = "ready"
                            hierarchy_note = f"{collection_type} product folder recognised"
                        else:
                            hierarchy_type = "unsupported_depth"
                            hierarchy_state = "blocking"
                            hierarchy_note = f"{collection_type} images are expected directly inside product folders"
                    elif collection_type == "Single Variable" and image_attributes:
                        if len(folder_parts) == len(image_attributes):
                            hierarchy_type = "variation"
                            hierarchy_state = "ready"
                            hierarchy_note = "Variation hierarchy matches configured image_attributes depth"
                        else:
                            hierarchy_type = "unsupported_depth"
                            hierarchy_state = "blocking"
                            hierarchy_note = "Folder depth does not match configured image_attributes"
                    elif len(folder_parts) == 1:
                        hierarchy_type = "product"
                    elif len(folder_parts) >= 2:
                        hierarchy_type = "variation"
                try:
                    component = "_".join(_normalised_components(recommended_parts))
                    recommended = f"{normalised_prefix}_{component}_{sequence:02d}{Path(image['name']).suffix.lower()}".lower()
                except ValueError:
                    hierarchy_state = "blocking"
                    hierarchy_note = "Folder hierarchy contains an unsafe filename component"

            destination = _join_rel(PREPARED_DIRECTORY, result_name, *folder_parts, recommended) if recommended else None
            mappings.append(
                {
                    **image,
                    "source_filename": image["name"],
                    "source_folder": _join_rel(selected, *folder_parts) or selected or "Catalogue Intake",
                    "hierarchy_type": hierarchy_type,
                    "hierarchy_components": list(folder_parts),
                    "hierarchy_note": hierarchy_note,
                    "sequence": sequence,
                    "legacy_filename": legacy,
                    "recommended_filename": recommended,
                    "destination_folder": _join_rel(PREPARED_DIRECTORY, result_name, *folder_parts),
                    "destination_relpath": destination,
                    "state": hierarchy_state,
                }
            )
            if not folder_parts:
                issues.append(_issue(image["source_relpath"], "hierarchy", "Root-level files are not renamed by the legacy tool and have no scanner-owned product folder", code="root_level_image", state="blocking"))
            elif hierarchy_state == "blocking":
                issues.append(_issue(image["source_relpath"], "hierarchy", hierarchy_note, code="unsupported_depth", state="blocking"))

    collision_sets = {}
    exact_sets = {}
    for mapping in mappings:
        filename = mapping.get("recommended_filename")
        if not filename:
            continue
        normal = unicodedata.normalize("NFC", filename)
        collision_sets.setdefault(normal.casefold(), []).append(mapping)
        exact_sets.setdefault(filename, []).append(mapping)
    for folded, rows in collision_sets.items():
        if len(rows) < 2:
            continue
        raw_names = {row["recommended_filename"] for row in rows}
        source_paths = {row["source_relpath"] for row in rows}
        normal_sources = {unicodedata.normalize("NFC", value) for value in source_paths}
        issue_name = ", ".join(row["source_relpath"] for row in rows)
        issues.append(_issue(issue_name, "conflict", "Proposed filenames collide when scanner output is flattened", code="flattened_collision", state="blocking"))
        if len(raw_names) == 1:
            issues.append(_issue(issue_name, "conflict", "Exact duplicate proposed filename", code="exact_collision", state="blocking"))
        if len({value.casefold() for value in normal_sources}) < len(normal_sources):
            issues.append(_issue(issue_name, "conflict", "Case-insensitive proposed filename collision", code="case_collision", state="blocking"))
        if len(normal_sources) < len(source_paths) or any(unicodedata.normalize("NFC", value) != value for value in source_paths):
            issues.append(_issue(issue_name, "conflict", "Proposed filenames collide after Unicode normalisation", code="unicode_collision", state="blocking"))
        for row in rows:
            row["state"] = "blocking"
            row["conflict"] = "flattened_collision"
    for filename, rows in exact_sets.items():
        if len(rows) > 1 and not any(issue["code"] == "flattened_collision" and filename in issue["name"] for issue in issues):
            for row in rows:
                row["conflict"] = "exact_collision"

    mappings.sort(key=lambda item: tuple(_ordered(part) for part in (*item["folder_parts"], item["name"])))
    issues.sort(key=lambda item: (_ordered(item["name"]), item["code"]))
    digest = _digest("rename", selected, result_name, mappings, issues, {"prefix": normalised_prefix, "collection_type": collection_type, "image_attributes": image_attributes})
    layout = None
    metadata_attributes = metadata.get("attributes") if isinstance(metadata.get("attributes"), dict) else {}
    if (
        collection_type == "Single Variable"
        and image_attributes
        and metadata_attributes
        and all(name in metadata_attributes for name in image_attributes)
    ):
        layout = resolve_single_variable_image_layout(
            metadata,
            directories,
            [PurePosixPath(*item["folder_parts"], item["name"]).as_posix() for item in images],
        )
    blocking = (
        any(item["state"] == "blocking" for item in mappings)
        or any(issue["state"] == "blocking" for issue in issues)
        or any(item["state"] == "blocking" for item in (layout or {}).get("findings", []))
    )
    metadata_certainty = bool(
        collection_type
        and (collection_type != "Single Variable" or image_attributes)
    )
    if collection_type == "Single Variable" and image_attributes:
        compatibility_label = "Single Variable hierarchy recognised from collection metadata"
    elif collection_type in {"Simple", "Variable Collection"}:
        compatibility_label = f"Likely {collection_type}-compatible based on collection metadata"
    elif mappings:
        compatibility_label = "Cannot fully validate image-attribute order without collection metadata"
    else:
        compatibility_label = "No supported nested images found"
    return {
        "kind": "rename",
        "preview_only": True,
        "browser": browse_intake(root, selected),
        "source": selected or "Catalogue Intake",
        "entered_prefix": prefix,
        "normalised_prefix": normalised_prefix,
        "result_name": result_name,
        "proposed_result": _join_rel(PREPARED_DIRECTORY, result_name),
        "mappings": mappings,
        "issues": issues,
        "digest": digest,
        "ready": bool(mappings) and not blocking,
        "compatibility": {
            "state": (
                "blocking" if layout and any(item["state"] == "blocking" for item in layout["findings"])
                else "warning" if layout and any(item["state"] == "warning" for item in layout["findings"])
                else "compatible" if metadata_certainty and not blocking
                else "warning" if mappings
                else "unavailable"
            ),
            "label": compatibility_label,
            "collection_type": collection_type,
            "image_attributes": image_attributes or [],
            "image_health": (layout or {}).get("image_health"),
            "image_findings": (layout or {}).get("findings", []),
        },
    }


def _token_serializer():
    return URLSafeSerializer(current_app.secret_key, salt="catalogue-intake-image-v1")


def _token_for_path(relative):
    try:
        return _token_serializer().dumps({"path": relative})
    except RuntimeError:
        return None


def intake_image_token(relative):
    _portable_parts(relative)
    return _token_for_path(relative)


def resolve_intake_image_token(token):
    try:
        payload = _token_serializer().loads(token)
        parts = _portable_parts(payload.get("path") if isinstance(payload, dict) else None)
    except (BadData, ValueError, TypeError):
        return None
    if not parts:
        return None
    readiness = intake_readiness()
    if not readiness["readable"]:
        return None
    try:
        root = _safe_root(configured_intake_root())
        current = root
        for part in parts[:-1]:
            current = current / part
            entry_stat = current.lstat()
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
                return None
        candidate = current / parts[-1]
        kind, _entry_stat = _classify_entry(candidate)
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if kind != "image" or not _within(resolved, root):
        return None
    return resolved
