"""Portable, presentation-only collection identity helpers."""

from __future__ import annotations

from pathlib import PurePosixPath


UNTITLED_COLLECTION = "Untitled collection"


def _portable_parts(value):
    if value is None:
        return None
    text = str(value).replace("\\", "/").strip()
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(
        part in {"", ".", ".."} or not part.strip() for part in path.parts
    ):
        return None
    return path.parts


def collection_source_provenance(collection):
    """Return a safe catalogue-relative collection directory, when projected."""

    parts = _portable_parts(getattr(collection, "source_relpath", None))
    if parts:
        return PurePosixPath(*parts).as_posix()

    parts = _portable_parts(getattr(collection, "shared_json_relpath", None))
    if parts:
        if PurePosixPath(parts[-1]).suffix.casefold() == ".json":
            parts = parts[:-1]
        if parts:
            return PurePosixPath(*parts).as_posix()
    return None


def collection_display_name(collection):
    """Resolve the visible collection title without using authored product metadata."""

    provenance = collection_source_provenance(collection)
    if provenance:
        name = PurePosixPath(provenance).name.strip()
        if name and name.casefold() != "product_info.json":
            return name

    existing = str(getattr(collection, "name", "") or "").strip()
    if existing and existing.casefold() not in {"none", "null", "product_info.json"}:
        return existing
    return UNTITLED_COLLECTION
