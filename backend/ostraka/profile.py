"""Small rules behind the profile page: quiet hours, which channels a notification may use, and a
readable name for a browser."""

from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from ostraka.models import Incident, NotificationKind, Severity, User

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Digits, spaces, +, -, brackets and dots: enough for any way people write a phone number
PHONE = re.compile(r"^\+?[0-9 ()\-.]{6,40}$")


def valid_timezone(name: str) -> bool:
    """An IANA zone such as Europe/London."""
    return name in available_timezones()


def in_quiet_hours(quiet_from: str | None, quiet_until: str | None, zone: str, now: datetime) -> bool:
    """Whether `now` falls in the person's quiet hours, read in their own time zone. A window that
    ends earlier than it starts runs overnight (22:00 to 07:00)."""
    if not quiet_from or not quiet_until or quiet_from == quiet_until:
        return False
    try:
        local = now.astimezone(ZoneInfo(zone)).time()
    except ZoneInfoNotFoundError:
        return False
    start, end = time.fromisoformat(quiet_from), time.fromisoformat(quiet_until)
    if start < end:
        return start <= local < end
    return local >= start or local < end


def outside_channels(
    user: User, kind: NotificationKind, incident: Incident | None, now: datetime
) -> tuple[bool, bool]:
    """(push, email) for one notification. It always appears under the bell; these decide whether it
    also goes out. A new-device alert follows its own switch and ignores quiet hours, since it may
    be the only sign that someone else has the password. Otherwise a muted kind stays in ostraka,
    and quiet hours hold everything back except work on a critical incident."""
    if kind is NotificationKind.NEW_DEVICE:
        return user.new_device_alerts and user.notify_push, user.new_device_alerts
    if kind.value in (user.muted_kinds or []):
        return False, False
    critical = incident is not None and incident.severity is Severity.CRITICAL
    if not critical and in_quiet_hours(user.quiet_from, user.quiet_until, user.timezone, now):
        return False, False
    return user.notify_push, user.notify_email


_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def describe_agent(user_agent: str) -> str:
    """'Firefox on macOS' from a User-Agent header, or the raw start of it when it's something else
    (a script, curl)."""
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), None)
    system = next((name for marker, name in _SYSTEMS if marker in user_agent), None)
    if browser and system:
        return f"{browser} on {system}"
    if browser or system:
        return browser or system or ""
    return user_agent.split(" ", 1)[0][:60] or "Unknown browser"
