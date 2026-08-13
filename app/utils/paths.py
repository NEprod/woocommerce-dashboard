import os


def is_subpath(child: str, parent: str) -> bool:
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except Exception:
        return False


def safe_join(base: str, *parts: str) -> str:
    path = os.path.join(base, *parts)
    if not is_subpath(path, base):
        raise ValueError("Unsafe path traversal attempt")
    return path
