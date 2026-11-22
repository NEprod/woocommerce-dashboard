"""Content-addressed collection artwork in persistent instance storage."""
import hashlib
import os
from pathlib import Path
import re
import tempfile

from flask import current_app
from app.inventory.images import optimise, ImageUploadError


def root():
    path = Path(current_app.instance_path) / 'taxonomy-images'
    if path.is_symlink():
        raise ImageUploadError('Collection image storage must not be a symbolic link.')
    path.mkdir(mode=0o750, exist_ok=True)
    return path


def read(digest):
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ImageUploadError('Invalid collection image reference.')
    fd = os.open(root() / (digest + '.webp'), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        content = handle.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != digest:
        raise ImageUploadError('Collection image changed or is unavailable. Upload it again.')
    return content


def store(upload):
    content = optimise(upload)
    if content is None:
        return None
    digest = hashlib.sha256(content).hexdigest()
    directory = root()
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o640)
            os.fsync(handle.fileno())
        try:
            os.link(temporary, directory / (digest + '.webp'), follow_symlinks=False)
        except FileExistsError:
            read(digest)  # Never replace an existing file with different contents.
    finally:
        os.unlink(temporary)
    return digest
