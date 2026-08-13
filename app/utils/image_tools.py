import os
import shutil
from PIL import Image

"""
image_tools.py

Handles image processing for product folders.

Responsibilities:
- Crop images to square format
- Convert images to supported formats
- Prepare image filenames for WooCommerce CSV export
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

def process_images(source_folder, output_folder, log=print):
    """
    Processes all supported images in a folder:
    - Crops to square
    - Saves to a flattened output folder

    Args:
        source_folder (str): Path to input folder with images
        output_folder (str): Path to output folder to store processed images
        log (function): Logging function (default: print)

    Returns:
        list[str]: List of processed filenames
    """

    processed_files = []
    for filename in os.listdir(source_folder):
        if filename.startswith("._"):
            log(f"⏭️ Skipped macOS hidden file: {filename}", level="INFO")
            continue
        if not is_image_file(filename):
            continue

        source_path = os.path.join(source_folder, filename)
        try:
            img = Image.open(source_path)
            img = crop_to_square(img)

            # Save processed image to output folder (original format retained)
            output_path = os.path.join(output_folder, filename)
            img.save(output_path)
            processed_files.append(filename)
            log(f"🖼️ Processed image: {filename}", level="INFO")
        except Exception as e:
            log(f"❌ Error processing image {filename}: {e}", level="ERROR")
    
    return processed_files

def get_image_csv_urls(image_filenames, url_prefix):
    """
    Converts image filenames to .webp URLs for CSV export.

    Args:
        image_filenames (list[str]): Filenames of processed images
        url_prefix (str): Base URL path (e.g., https://site.com/images/)

    Returns:
        list[str]: List of URL strings for use in CSV output
    """
    
    urls = []
    for fname in image_filenames:
        name, _ = os.path.splitext(fname)
        urls.append(f"{url_prefix}{name}.webp")
    return urls