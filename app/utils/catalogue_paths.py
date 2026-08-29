"""Shared catalogue path rules for reserved semantic directories."""

from pathlib import Path


PARENT_DIRECTORY_NAME = "parent"


class AmbiguousReservedDirectoryError(ValueError):
    """Raised when one catalogue root contains duplicate reserved-name variants."""


def is_reserved_directory_name(value, reserved_name=PARENT_DIRECTORY_NAME):
    """Return whether a single directory name has the reserved semantic name."""

    return isinstance(value, str) and value.casefold() == reserved_name.casefold()


def find_reserved_directory(root, reserved_name=PARENT_DIRECTORY_NAME):
    """Return the one case-insensitive reserved directory, preserving real casing.

    Directory names in an ambiguity error are safe catalogue-local names. Absolute
    host paths are deliberately excluded from the message.
    """

    root = Path(root)
    try:
        matches = [
            entry
            for entry in root.iterdir()
            if entry.is_dir() and is_reserved_directory_name(entry.name, reserved_name)
        ]
    except OSError:
        return None
    matches.sort(key=lambda entry: (entry.name.casefold(), entry.name))
    if len(matches) > 1:
        names = ", ".join(entry.name for entry in matches)
        raise AmbiguousReservedDirectoryError(
            f"Ambiguous reserved '{reserved_name}' directory variants: {names}"
        )
    return matches[0] if matches else None
