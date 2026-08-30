"""Central diagnostic redaction for persistence and browser presentation."""

from __future__ import annotations

import os
import re
from pathlib import Path


_HEADER_PATTERN = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*"
    r"(?!\[REDACTED\])[^\r\n]+"
)
_QUOTED_HEADER_PATTERN = re.compile(
    r"(?i)(['\"])(authorization|proxy-authorization|cookie|set-cookie)\1"
    r"\s*:\s*(['\"])(.*?)\3"
)
_DISCORD_PATTERN = re.compile(
    r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[^\s'\"<>]+",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_BASIC_PATTERN = re.compile(r"\bBasic\s+[^\s,;]+", re.IGNORECASE)
_WOO_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:ck|cs)_[A-Za-z0-9_-]{8,}", re.IGNORECASE)
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:consumer_key|consumer_secret|oauth_consumer_key|oauth_signature|oauth_token|access_token)=)[^&#\s]+"
)
_ASSIGNMENT_PATTERN = re.compile(
    r"\b(consumer[_-]?(?:key|secret)|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?secret|client[_-]?secret|token|password|"
    r"secret|webhook)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_QUOTED_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(['\"])(consumer[_-]?(?:key|secret)|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?secret|client[_-]?secret|token|password|"
    r"secret|webhook)\1\s*:\s*(['\"])(.*?)\3"
)
_POSIX_HOME_PATTERN = re.compile(r"(?<![\w.-])/(?:Users|home)/[^/\s]+")
_WINDOWS_HOME_PATTERN = re.compile(
    r"(?i)(?<![\w.-])[A-Z]:\\Users\\[^\\\s]+"
)
_RUNTIME_SECRET_NAMES = (
    "SECRET_KEY", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET",
    "DISCORD_WEBHOOK_SCANS_INFO", "DISCORD_WEBHOOK_SCANS_ERRORS",
    "DISCORD_WEBHOOK_EDITS", "DISCORD_WEBHOOK_OVERRIDES", "DISCORD_WEBHOOK_INGEST",
)


def _normalized_paths(paths):
    entries = []
    for label, raw_path in (paths or {}).items():
        if not raw_path:
            continue
        value = os.path.normpath(os.fspath(raw_path))
        if value not in ("", ".", os.path.sep):
            entries.append((value, str(label)))
    return sorted(entries, key=lambda entry: len(entry[0]), reverse=True)


def redact_diagnostic(value, *, paths=None, limit: int | None = None) -> str:
    """Redact credentials and sensitive path prefixes while keeping context."""

    text = str(value)
    for name in _RUNTIME_SECRET_NAMES:
        secret = os.environ.get(name, "")
        if len(secret) >= 8:
            text = text.replace(secret, f"[REDACTED_{name}]")
    for prefix, label in _normalized_paths(paths):
        text = text.replace(prefix, label)
    text = _DISCORD_PATTERN.sub("[REDACTED_WEBHOOK]", text)
    text = _HEADER_PATTERN.sub(lambda match: f"{match.group(1)}: [REDACTED]", text)
    text = _QUOTED_HEADER_PATTERN.sub(
        lambda match: f"{match.group(2)}: [REDACTED]", text
    )
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _BASIC_PATTERN.sub("Basic [REDACTED]", text)
    text = _SENSITIVE_QUERY_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _WOO_TOKEN_PATTERN.sub("[REDACTED_WOO_CREDENTIAL]", text)
    text = _QUOTED_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(2)}=[REDACTED]", text
    )
    text = _ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", text
    )
    text = _POSIX_HOME_PATTERN.sub("<home>", text)
    text = _WINDOWS_HOME_PATTERN.sub("<home>", text)
    return text[:limit] if limit is not None else text


def runtime_redaction_paths(*, catalogue=None, output=None, instance=None):
    """Build neutral labels for configured runtime roots without persisting them."""

    paths = {"<home>": Path.home()}
    if catalogue:
        paths["<catalogue>"] = catalogue
    if output:
        paths["<output>"] = output
    if instance:
        paths["<instance>"] = instance
    return paths
