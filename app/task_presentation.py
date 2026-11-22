"""Read-only formatting of persisted task timestamps and durations."""

from datetime import datetime, timezone


def _stamp(value):
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def relative(value, *, future=False, now=None):
    stamp = _stamp(value)
    if stamp is None:
        return "Not scheduled" if future else "Not yet"
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int(((stamp - now) if future else (now - stamp)).total_seconds()))
    if seconds < 60:
        return "Due now" if future else "Just now"
    minutes = seconds // 60
    if minutes < 60:
        amount, unit = minutes, "minute"
    elif minutes < 86400 // 60:
        amount, unit = minutes // 60, "hour"
    else:
        amount, unit = minutes // 1440, "day"
    if future and unit == "day" and amount == 1:
        return "Tomorrow"
    phrase = f"{amount} {unit}{'' if amount == 1 else 's'}"
    return f"In {phrase}" if future else f"{phrase} ago"


def duration(value):
    if value is None:
        return "Not yet"
    seconds = max(0, int(round(value)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def interval(task):
    if not task["enabled"]:
        return "Disabled"
    minutes = task["interval"]
    if minutes == 1440:
        return "Daily"
    if minutes == 60:
        return "Every hour"
    return f"Every {minutes} minutes"
