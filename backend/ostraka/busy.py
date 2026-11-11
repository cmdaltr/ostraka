"""Whether ostraka is in the middle of something a restart would interrupt.

`homelab pull` and `homelab update` ask `GET /api/busy` before rebuilding and hold off while it says
`{"busy": true}`. Anything that shouldn't be cut off mid-flight (a responder's open editing session,
a report being rendered) registers a check here.
"""

from __future__ import annotations

from collections.abc import Callable

from ostraka.log import get_logger

log = get_logger(__name__)

BusyCheck = Callable[[], bool]
_checks: dict[str, BusyCheck] = {}


def register(name: str, check: BusyCheck) -> None:
    _checks[name] = check


def unregister(name: str) -> None:
    _checks.pop(name, None)


def busy_reasons() -> list[str]:
    """Names of the checks that currently say busy. A check that raises counts as busy."""
    reasons = []
    for name, check in _checks.items():
        try:
            if check():
                reasons.append(name)
        except Exception:
            log.exception("busy check %s failed; treating as busy", name)
            reasons.append(name)
    return reasons
