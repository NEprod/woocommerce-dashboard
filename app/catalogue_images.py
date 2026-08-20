"""Safe read-only resolution for catalogue-backed product and variation images."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from flask import g, has_request_context
from PIL import Image, UnidentifiedImageError

from app.models import Product, Settings, Variation
from app.utils.json_utils import merge_product_json


SUPPORTED_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
MAX_METADATA_BYTES = 1024 * 1024


def _ordered_product_images(product: Product):
    return sorted(
        product.images,
        key=lambda image: (
            image.position if image.position is not None else 2**31,
            image.id or 0,
        ),
    )


def _ordered_variation_images(variation: Variation):
    return sorted(
        variation.images,
        key=lambda image: (
            image.position if image.position is not None else 2**31,
            image.id or 0,
        ),
    )


def _ordered_variations(product: Product):
    return sorted(
        product.variations,
        key=lambda variation: (
            variation.menu_order if variation.menu_order is not None else 2**31,
            variation.id or 0,
        ),
    )


def primary_image_reference(product: Product) -> str | None:
    images = _ordered_product_images(product)
    if images:
        return images[0].url
    return product.image_url


def primary_image_alt(product: Product) -> str:
    images = _ordered_product_images(product)
    if images and images[0].alt_text:
        return images[0].alt_text
    return product.title or ""


def variation_image_alt(variation: Variation) -> str:
    images = _ordered_variation_images(variation)
    if images and images[0].alt_text:
        return images[0].alt_text
    return primary_image_alt(variation.product)


def product_thumbnail_url(product: Product) -> str | None:
    """Expose an opaque route only when a mounted source image resolves."""

    if not resolve_product_catalogue_image(product):
        return None
    return f"/catalogue-images/products/{product.id}"


def variation_thumbnail_url(variation: Variation) -> str | None:
    if not resolve_variation_catalogue_image(variation):
        return None
    return f"/catalogue-images/variations/{variation.id}"


def _within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _portable_parts(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.parts


@lru_cache(maxsize=16384)
def _verified_image(path_value: str, modified_ns: int, size: int) -> bool:
    del modified_ns, size
    try:
        with Image.open(path_value) as image:
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError):
        return False
    return True


def _valid_image(path: Path, product_folder: Path, catalogue_root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except (OSError, RuntimeError):
        return False
    if not (
        resolved.is_file()
        and _within(resolved, catalogue_root)
        and _within(resolved, product_folder)
        and resolved.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        and os.access(resolved, os.R_OK)
    ):
        return False
    return _verified_image(str(resolved), stat.st_mtime_ns, stat.st_size)


def _catalogue_root() -> Path | None:
    cache_key = "_catalogue_image_root"
    if has_request_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)
    settings = Settings.query.first()
    root = None
    if settings and settings.product_folder:
        try:
            candidate = Path(settings.product_folder).resolve(strict=True)
            if candidate.is_dir():
                root = candidate
        except (OSError, RuntimeError):
            root = None
    if has_request_context():
        setattr(g, cache_key, root)
    return root


def _source_context(product: Product):
    catalogue_root = _catalogue_root()
    source_parts = _portable_parts(product.source_relpath)
    if not (catalogue_root and source_parts):
        return None
    try:
        product_folder = catalogue_root.joinpath(*source_parts).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not product_folder.is_dir() or not _within(product_folder, catalogue_root):
        return None
    return catalogue_root, product_folder, source_parts


def _read_json_object(path: Path, catalogue_root: Path) -> dict:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
        if (
            not resolved.is_file()
            or not _within(resolved, catalogue_root)
            or stat.st_size > MAX_METADATA_BYTES
        ):
            return {}
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _scanner_layout(product: Product, context) -> dict:
    catalogue_root, product_folder, source_parts = context
    collection_folder = catalogue_root / source_parts[0]
    shared = _read_json_object(collection_folder / "product_info.json", catalogue_root)
    override = {}
    if product_folder != collection_folder:
        override = _read_json_object(product_folder / "product_info.json", catalogue_root)
    return merge_product_json(shared, override, path=str(product_folder))


def _marker_images(product_folder: Path, catalogue_root: Path) -> list[str]:
    marker = _read_json_object(product_folder / ".scanned", catalogue_root)
    images = marker.get("images_used", [])
    if not isinstance(images, list):
        return []
    result = []
    for value in images:
        if not isinstance(value, str):
            continue
        parts = _portable_parts(unquote(value).replace("\\", "/"))
        if parts and len(parts) == 1:
            result.append(parts[0])
    return result


def _parent_directories(product: Product, context, metadata: dict) -> list[Path]:
    _catalogue_root_value, product_folder, _source_parts = context
    collection_type = product.collection_type or metadata.get("collection_type")
    if collection_type != "Single Variable":
        return [product_folder]
    parent_folder = product_folder / "parent"
    if parent_folder.is_dir():
        return [parent_folder]
    image_attributes = metadata.get("image_attributes")
    attributes = metadata.get("attributes")
    if (
        isinstance(image_attributes, list)
        and image_attributes
        and isinstance(attributes, dict)
    ):
        values = attributes.get(image_attributes[0])
        if isinstance(values, list) and values and isinstance(values[0], str):
            parts = _portable_parts(values[0])
            if parts and len(parts) == 1:
                return [product_folder / parts[0]]
    return [product_folder]


def _variation_directories(variation: Variation, context, metadata: dict) -> list[Path]:
    _catalogue_root_value, product_folder, _source_parts = context
    collection_type = variation.product.collection_type or metadata.get("collection_type")
    if collection_type != "Single Variable":
        return _parent_directories(variation.product, context, metadata)
    image_attributes = metadata.get("image_attributes")
    if not isinstance(image_attributes, list) or not image_attributes:
        return []
    values = {attribute.name: attribute.value for attribute in variation.attributes}
    directories = []
    current = product_folder
    for attribute_name in image_attributes:
        value = values.get(attribute_name)
        parts = _portable_parts(value)
        if not parts or len(parts) != 1:
            break
        current = current / parts[0]
        directories.append(current)
    return directories


def _reference_path(reference: str) -> str:
    parsed = urlsplit(reference)
    value = parsed.path if parsed.scheme or parsed.netloc else reference
    return unquote(value).replace("\\", "/")


def _explicit_candidates(reference_path: str, product_folder: Path, catalogue_root: Path):
    catalogue_prefix = "/catalogue/"
    if reference_path.startswith(catalogue_prefix):
        parts = _portable_parts(reference_path[len(catalogue_prefix) :])
        if parts:
            yield catalogue_root.joinpath(*parts)
    elif not PurePosixPath(reference_path).is_absolute():
        parts = _portable_parts(reference_path)
        if parts:
            yield catalogue_root.joinpath(*parts)
            yield product_folder.joinpath(*parts)


def _directory_files(directory: Path) -> list[Path]:
    try:
        return sorted(
            (path for path in directory.iterdir() if path.is_file()),
            key=lambda path: (path.name.casefold(), path.name),
        )
    except OSError:
        return []


def _filename_candidates(filename: str, directories: list[Path]):
    requested = PurePosixPath(filename).name
    if not requested or requested in {".", ".."}:
        return
    requested_path = PurePosixPath(requested)
    for directory in directories:
        yield directory / requested
        files = _directory_files(directory)
        for suffix in SUPPORTED_IMAGE_SUFFIXES:
            for path in files:
                if path.stem == requested_path.stem and path.suffix.lower() == suffix:
                    yield path


def _reference_candidates(
    reference: str,
    directories: list[Path],
    product_folder: Path,
    catalogue_root: Path,
):
    reference_path = _reference_path(reference)
    yield from _explicit_candidates(reference_path, product_folder, catalogue_root)
    yield from _filename_candidates(PurePosixPath(reference_path).name, directories)


def _first_valid(candidates, product_folder: Path, catalogue_root: Path) -> Path | None:
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _valid_image(candidate, product_folder, catalogue_root):
            return candidate.resolve()
    return None


def _discover_first(directories: list[Path], product_folder: Path, catalogue_root: Path):
    candidates = (
        path
        for directory in directories
        for path in _directory_files(directory)
        if path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )
    return _first_valid(candidates, product_folder, catalogue_root)


def _resolve_named_source(
    name: str,
    directories: list[Path],
    product_folder: Path,
    catalogue_root: Path,
):
    return _first_valid(
        _filename_candidates(name, directories), product_folder, catalogue_root
    )


def _resolve_reference(
    reference: str | None,
    directories: list[Path],
    product_folder: Path,
    catalogue_root: Path,
):
    if not reference:
        return None
    return _first_valid(
        _reference_candidates(reference, directories, product_folder, catalogue_root),
        product_folder,
        catalogue_root,
    )


def _resolve_parent_only(product: Product, context) -> Path | None:
    catalogue_root, product_folder, _source_parts = context
    metadata = _scanner_layout(product, context)
    directories = _parent_directories(product, context, metadata)
    marker_images = _marker_images(product_folder, catalogue_root)
    images = _ordered_product_images(product)

    if images:
        position = images[0].position if images[0].position is not None else 0
        if 0 <= position < len(marker_images):
            selected = _resolve_named_source(
                marker_images[position], directories, product_folder, catalogue_root
            )
            if selected:
                return selected
        selected = _resolve_reference(images[0].url, directories, product_folder, catalogue_root)
        if selected:
            return selected

    if product.image_url:
        if marker_images:
            selected = _resolve_named_source(
                marker_images[0], directories, product_folder, catalogue_root
            )
            if selected:
                return selected
        selected = _resolve_reference(product.image_url, directories, product_folder, catalogue_root)
        if selected:
            return selected

    for index, image in enumerate(images[1:], start=1):
        position = image.position if image.position is not None else index
        if 0 <= position < len(marker_images):
            selected = _resolve_named_source(
                marker_images[position], directories, product_folder, catalogue_root
            )
            if selected:
                return selected
        selected = _resolve_reference(image.url, directories, product_folder, catalogue_root)
        if selected:
            return selected

    for filename in marker_images:
        selected = _resolve_named_source(filename, directories, product_folder, catalogue_root)
        if selected:
            return selected
    return _discover_first(directories, product_folder, catalogue_root)


def _resolve_variation_only(variation: Variation, context) -> Path | None:
    catalogue_root, product_folder, _source_parts = context
    metadata = _scanner_layout(variation.product, context)
    directories = _variation_directories(variation, context, metadata)
    images = _ordered_variation_images(variation)
    if images:
        selected = _resolve_reference(images[0].url, directories, product_folder, catalogue_root)
        if selected:
            return selected
    if variation.image_url:
        selected = _resolve_reference(variation.image_url, directories, product_folder, catalogue_root)
        if selected:
            return selected
    for image in images[1:]:
        selected = _resolve_reference(image.url, directories, product_folder, catalogue_root)
        if selected:
            return selected
    return _discover_first(directories, product_folder, catalogue_root)


def resolve_product_catalogue_image(product: Product) -> Path | None:
    """Resolve ordered parent sources, then the first valid variation source."""

    context = _source_context(product)
    if not context:
        return None
    selected = _resolve_parent_only(product, context)
    if selected:
        return selected
    for variation in _ordered_variations(product):
        selected = _resolve_variation_only(variation, context)
        if selected:
            return selected
    return None


def resolve_variation_catalogue_image(
    variation: Variation, *, fallback_to_parent: bool = True
) -> Path | None:
    """Resolve a variation-specific source before an optional parent fallback."""

    context = _source_context(variation.product)
    if not context:
        return None
    selected = _resolve_variation_only(variation, context)
    if selected or not fallback_to_parent:
        return selected
    return _resolve_parent_only(variation.product, context)
