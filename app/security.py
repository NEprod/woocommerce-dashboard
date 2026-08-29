"""Runtime configuration security checks."""

from __future__ import annotations

import os


_UNSAFE_SECRET_KEYS = {
    "changeme",
    "change-me",
    "dev-secret",
    "development-secret",
    "secret",
    "your-secret-key",
}
_PLACEHOLDER_FRAGMENTS = (
    "example",
    "placeholder",
    "replace-me",
    "replace_with",
    "replace-with",
)


def validate_secret_key(value: str | None = None) -> str:
    """Return a configured secret or fail without echoing it."""

    supplied = os.environ.get("SECRET_KEY") if value is None else value
    normalized = (supplied or "").strip()
    lowered = normalized.lower()
    if (
        not normalized
        or lowered in _UNSAFE_SECRET_KEYS
        or any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS)
    ):
        raise RuntimeError(
            "SECRET_KEY is required and must be set to a non-placeholder value"
        )
    return normalized
