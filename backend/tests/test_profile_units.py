"""The profile page's small rules: quiet hours in the person's own time zone, which channels a
notification may use, and naming a browser."""

from datetime import UTC, datetime

import pytest

from ostraka import profile
from ostraka.models import Incident, NotificationKind, Severity, User

# 23:30 in London in November (GMT), 18:30 in New York
LATE = datetime(2026, 11, 10, 23, 30, tzinfo=UTC)
NOON = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "end", "zone", "now", "quiet"),
    [
        ("22:00", "07:00", "Europe/London", LATE, True),  # overnight window
        ("22:00", "07:00", "Europe/London", NOON, False),
        ("22:00", "07:00", "America/New_York", LATE, False),  # 18:30 there
        ("09:00", "17:00", "Europe/London", NOON, True),  # a daytime window
        ("09:00", "17:00", "Europe/London", LATE, False),
        (None, None, "Europe/London", LATE, False),
        ("22:00", "22:00", "Europe/London", LATE, False),  # an empty window is no window
    ],
)
def test_quiet_hours_are_read_in_the_persons_own_time_zone(start, end, zone, now, quiet):
    assert profile.in_quiet_hours(start, end, zone, now) is quiet


def person(**overrides) -> User:
    values = {
        "notify_push": True,
        "notify_email": True,
        "muted_kinds": [],
        "quiet_from": None,
        "quiet_until": None,
        "timezone": "Europe/London",
        "new_device_alerts": True,
    }
    return User(**{**values, **overrides})


def test_a_muted_kind_stays_under_the_bell():
    user = person(muted_kinds=["mention"])
    assert profile.outside_channels(user, NotificationKind.MENTION, None, NOON) == (False, False)
    assert profile.outside_channels(user, NotificationKind.ASSIGNED, None, NOON) == (True, True)


def test_quiet_hours_hold_everything_but_a_critical_incident():
    user = person(quiet_from="22:00", quiet_until="07:00")
    high, critical = Incident(severity=Severity.HIGH), Incident(severity=Severity.CRITICAL)
    assert profile.outside_channels(user, NotificationKind.ASSIGNED, high, LATE) == (False, False)
    assert profile.outside_channels(user, NotificationKind.ASSIGNED, critical, LATE) == (True, True)
    assert profile.outside_channels(user, NotificationKind.ASSIGNED, high, NOON) == (True, True)


def test_a_new_device_alert_ignores_quiet_hours_and_follows_its_own_switch():
    user = person(quiet_from="22:00", quiet_until="07:00", notify_email=False)
    assert profile.outside_channels(user, NotificationKind.NEW_DEVICE, None, LATE) == (True, True)
    off = person(new_device_alerts=False)
    assert profile.outside_channels(off, NotificationKind.NEW_DEVICE, None, NOON) == (False, False)


@pytest.mark.parametrize(
    ("agent", "name"),
    [
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
            "Firefox on macOS",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.1 Mobile/15E148 Safari/604.1",
            "Safari on iPhone",
        ),
        ("python-httpx/0.28.1", "python-httpx/0.28.1"),
        ("", "Unknown browser"),
    ],
)
def test_browsers_get_a_readable_name(agent, name):
    assert profile.describe_agent(agent) == name


def test_time_zones_and_times_are_checked():
    assert profile.valid_timezone("Europe/London")
    assert not profile.valid_timezone("Mars/Olympus_Mons")
    assert profile.HHMM.match("07:30") and not profile.HHMM.match("24:00")
    assert profile.PHONE.match("+44 7700 900123") and not profile.PHONE.match("call me")
