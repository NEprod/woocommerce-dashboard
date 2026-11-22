"""Bounded, metadata-free inventory photos. Bytes remain in the existing DB blob."""
from io import BytesIO
from threading import Lock
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 60_000_000  # Includes ordinary 48-megapixel phone photographs.
MAX_SIDE = 1600
_optimizer = Lock()  # Bound peak decode memory across threaded web requests.


class ImageUploadError(ValueError):
    """Safe operator-facing validation/processing explanation."""

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except (ImportError, OSError):
    HEIF_SUPPORTED = False
else:
    HEIF_SUPPORTED = True


def _format(data):
    if data.startswith(b'\xff\xd8'):
        return 'JPEG'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'PNG'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'WEBP'
    if data[4:8] == b'ftyp' and any(brand in data[8:64] for brand in (b'heic', b'heix', b'heif', b'mif1', b'msf1')):
        if not HEIF_SUPPORTED:
            raise ImageUploadError('HEIC/HEIF decoding is unavailable on this server. Export the photograph as JPEG, PNG or WebP.')
        return 'HEIF'
    raise ImageUploadError('Unsupported image contents. Choose JPEG, PNG or WebP' + (', or HEIC/HEIF.' if HEIF_SUPPORTED else '. HEIC/HEIF decoding is unavailable on this server.'))


def optimise(upload):
    if upload is None or not upload.filename:
        return None
    data = upload.stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ImageUploadError('Original image exceeds the 25 MB upload limit.')
    format_name = _format(data)
    try:
        with _optimizer, warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=[format_name]) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ImageUploadError('Image exceeds the 60-megapixel safety limit. Export a smaller photograph.')
                if getattr(source, 'is_animated', False) or getattr(source, 'n_frames', 1) != 1:
                    raise ImageUploadError('Choose a single still photograph, not an animation or multi-image file.')
                if format_name == 'JPEG':
                    source.draft('RGB', (MAX_SIDE, MAX_SIDE))
                ImageOps.exif_transpose(source, in_place=True)
                source.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                mode = 'RGBA' if 'A' in source.getbands() or 'transparency' in source.info else 'RGB'
                pixels = source.convert(mode)
                try:
                    # Do not propagate EXIF, GPS, comments, XMP or source filenames.
                    with Image.frombytes(mode, pixels.size, pixels.tobytes()) as clean, BytesIO() as output:
                        clean.save(output, 'WEBP', quality=85, method=4)
                        return output.getvalue()
                finally:
                    pixels.close()
    except ImageUploadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageUploadError('Image dimensions exceed the safe decoding limit.') from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ImageUploadError('The image is invalid, corrupted or could not be processed. Choose another photograph.') from None


def stored_mime(content):
    """Keep legacy .jpg URLs while correctly serving old JPEG/new WebP blobs."""
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return 'image/webp'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    return 'image/jpeg'
