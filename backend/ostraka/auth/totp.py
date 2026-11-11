"""TOTP enrolment and verification, with replay protection."""

from __future__ import annotations

import time

import pyotp
import segno

from ostraka import branding

STEP_SECONDS = 30
DRIFT_STEPS = 1  # accept the previous and next 30-second window


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=branding.NAME)


def qr_svg_data_uri(uri: str) -> str:
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


def matching_step(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """The time step `code` belongs to, or None. A step at or before `last_step` is a replay."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    current = int((now if now is not None else time.time()) // STEP_SECONDS)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if step > last_step and totp.at(step * STEP_SECONDS) == code:
            return step
    return None
