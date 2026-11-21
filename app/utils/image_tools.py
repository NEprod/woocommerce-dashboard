import os
from pathlib import Path
from PIL import Image

"""
image_tools.py

Handles image processing for product folders.

Responsibilities:
- Inventory original catalogue images without copying or rewriting them
- Build portable references to those authoritative originals
"""

SUPPORTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"]

def is_image_file(filename):
    """
    Determines whether a file has a supported image extension.

    Args:
        filename (str): Name of the file

    Returns:
        bool: True if file is an image, False otherwise
    """

    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_EXTENSIONS

def crop_to_square(image):
    """
    Crops a PIL Image to the largest centered square.

    Args:
        image (PIL.Image): Input image object

    Returns:
        PIL.Image: Cropped square image
    """

    width, height = image.size
    if width == height:
        return image
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    right = left + side
    bottom = top + side
    return image.crop((left, top, right, bottom))

def source_images(source_folder, log=print, *, deterministic=False):
    """Inventory originals. An unreadable image fails the scan, not a deletion.

    The old flattened WordPress upload directory is deliberately never written.
    Shopify validates and uploads these same original bytes at publication time.
    """
    names = os.listdir(source_folder)
    if deterministic:
        names = sorted(names, key=lambda value: (value.casefold(), value))
    result = []
    for name in names:
        if name.startswith("._") or not is_image_file(name):
            continue
        path = Path(source_folder) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Image source is not a regular catalogue file: {name}")
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, ValueError) as error:
            raise ValueError(f"Image source cannot be verified: {name}") from error
        result.append(name)
        log(f"🖼️ Found original image: {name}", level="INFO")
    return result


def source_references(names, source_folder, product_folder):
    """Return product-relative references, including variant subdirectories."""
    source = Path(source_folder).resolve()
    product = Path(product_folder).resolve()
    relative = source.relative_to(product)
    return [(relative / name).as_posix() for name in names]
