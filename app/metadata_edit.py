"""Pure edit preparation for the future editor; no file, scan or database writes.

This is a same-document patch, NOT collection/override inheritance. Existing
routes do not use it yet; callers must recheck source_digest under the catalogue
operation lock immediately before their atomic write.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from app.product_info import ValidationResult, validate_product_info

MAX_DOCUMENT_BYTES = 1024 * 1024


class MetadataEditConflict(ValueError):
    """The authored source changed since it was opened."""


def source_digest(text):
    if not isinstance(text, str):
        raise ValueError("Metadata source must be text.")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValueError("Metadata source exceeds the supported size limit.")
    return hashlib.sha256(encoded).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate metadata keys require resolution before editing.")
        result[key] = value
    return result


def _patch(target, changes):
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _patch(target[key], value)
        else:
            target[key] = deepcopy(value)


@dataclass(frozen=True)
class PreparedMetadataEdit:
    source_digest: str
    text: str
    changed: bool
    validation: ValidationResult

    @property
    def data(self):
        """Return an independent copy, never mutable shared prepared evidence."""
        return json.loads(self.text)


def prepare_edit(text, changes, *, expected_digest, kind, remove_paths=()):
    """Preserve unrepresented keys/empties; deletion is explicit by object path.

    Paths contain literal keys, not dotted strings or list indexes. Validation
    covers the authored layer only; callers must separately resolve inheritance
    and validate effective values before saving. Invalid edits are returned with
    validation errors so a future UI can show the draft without side effects.
    """
    if kind not in {"collection", "override"}:
        raise ValueError("Choose collection or override metadata.")
    stamp = source_digest(text)
    if stamp != expected_digest:
        raise MetadataEditConflict("Metadata changed since opening; reload and review your edits.")
    original = json.loads(text, object_pairs_hook=_object)
    if not isinstance(original, dict) or not isinstance(changes, dict):
        raise ValueError("Metadata source and patch must be JSON objects.")
    # Reject non-JSON input and NaN/Infinity before preparing reusable evidence.
    changes = json.loads(json.dumps(changes, allow_nan=False))
    result = deepcopy(original)
    _patch(result, changes)
    for path in remove_paths:
        if (not isinstance(path, (tuple, list)) or not path
                or any(not isinstance(key, str) or not key for key in path)):
            raise ValueError("Removal paths must contain explicit object keys.")
        parent = result
        for key in path[:-1]:
            if key not in parent:
                parent = None
                break
            parent = parent[key]
            if not isinstance(parent, dict):
                raise ValueError("Removal paths cannot traverse arrays or scalar values.")
        if parent is not None:
            parent.pop(path[-1], None)
    prepared = json.dumps(result, ensure_ascii=False, indent=4, allow_nan=False)
    source_digest(prepared)  # same size limit for output as input
    return PreparedMetadataEdit(stamp, prepared, result != original,
                                validate_product_info(result, kind))
