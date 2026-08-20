"""Safe read-only resolution for catalogue-backed product thumbnails."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError

from app.models import Product, Settings


SUPPORTED_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def primary_image_reference(product: Product) -> str | None:
    """Return the ordered gallery primary, falling back to the legacy shortcut."""

    if product.images:
        return product.images[0].url
    return product.image_url


def primary_image_alt(product: Product) -> str:
    if product.images and product.images[0].alt_text:
        return product.images[0].alt_text
    return product.title or ""


def product_thumbnail_url(product: Product) -> str | None:
    """Expose an opaque authenticated route, never a filesystem or export URL."""

    if not primary_image_reference(product):
        return None
    return f"/catalogue-images/products/{product.id}"


def _within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _portable_parts(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.parts


def _valid_image(path: Path, product_folder: Path, catalogue_root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
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
    try:
        with Image.open(resolved) as image:
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError):
        return False
    return True


def _reference_path(reference: str) -> str:
    parsed = urlsplit(reference)
    value = parsed.path if parsed.scheme or parsed.netloc else reference
    return unquote(value).replace("\\", "/")


def _explicit_candidates(
    reference_path: str, product_folder: Path, catalogue_root: Path
):
    catalogue_prefix = "/catalogue/"
    if reference_path.startswith(catalogue_prefix):
        parts = _portable_parts(reference_path[len(catalogue_prefix) :])
        if parts:
            yield catalogue_root.joinpath(*parts)
    elif not PurePosixPath(reference_path).is_absolute():
        parts = _portable_parts(reference_path)
        if parts:
            catalogue_candidate = catalogue_root.joinpath(*parts)
            yield catalogue_candidate
            yield product_folder.joinpath(*parts)


def _filename_candidates(reference_path: str, product_folder: Path):
    filename = PurePosixPath(reference_path).name
    if not filename or filename in {".", ".."}:
        return
    requested = PurePosixPath(filename)
    stem = requested.stem

    # The scanner emits `.webp` URLs while retaining source image extensions.
    # Match the recorded filename first, then the same stem in supported formats.
    names = [requested.name]
    names.extend(
        f"{stem}{suffix}"
        for suffix in SUPPORTED_IMAGE_SUFFIXES
        if f"{stem}{suffix}" not in names
    )
    for name in names:
        yield product_folder / name
    try:
        descendants = sorted(
            path
            for path in product_folder.rglob("*")
            if path.is_file()
            and path.stem == stem
            and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )
    except OSError:
        return
    yield from descendants


def resolve_product_catalogue_image(product: Product) -> Path | None:
    """Resolve a projected primary reference within its portable product folder."""

    reference = primary_image_reference(product)
    source_parts = _portable_parts(product.source_relpath)
    settings = Settings.query.first()
    if not (reference and source_parts and settings and settings.product_folder):
        return None

    try:
        catalogue_root = Path(settings.product_folder).resolve(strict=True)
        product_folder = catalogue_root.joinpath(*source_parts).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not product_folder.is_dir() or not _within(product_folder, catalogue_root):
        return None

    reference_path = _reference_path(reference)
    seen = set()
    candidates = (
        *_explicit_candidates(reference_path, product_folder, catalogue_root),
        *_filename_candidates(reference_path, product_folder),
    )
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _valid_image(candidate, product_folder, catalogue_root):
            return candidate.resolve()
    return None
