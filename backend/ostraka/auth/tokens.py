"""Short-lived JWTs: access tokens, and the five-minute ticket between password and TOTP."""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt

from ostraka.config import get_settings
from ostraka.models import Role, utcnow

ALGORITHM = "HS256"
MFA_TICKET_MINUTES = 5


def _encode(claims: dict, lifetime: timedelta) -> str:
    now = utcnow()
    payload = {**claims, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def _decode(token: str, kind: str) -> dict | None:
    try:
        claims = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return claims if claims.get("typ") == kind else None


def access_token(user_id: uuid.UUID, role: Role, session_id: uuid.UUID) -> str:
    minutes = get_settings().access_token_minutes
    return _encode(
        {"typ": "access", "sub": str(user_id), "role": role.value, "sid": str(session_id)},
        timedelta(minutes=minutes),
    )


def read_access_token(token: str) -> dict | None:
    return _decode(token, "access")


def mfa_ticket(user_id: uuid.UUID) -> str:
    """Proof the password was right, good for one TOTP attempt window. Not a session."""
    return _encode({"typ": "mfa", "sub": str(user_id)}, timedelta(minutes=MFA_TICKET_MINUTES))


def read_mfa_ticket(token: str) -> uuid.UUID | None:
    claims = _decode(token, "mfa")
    try:
        return uuid.UUID(claims["sub"]) if claims else None
    except (KeyError, ValueError):
        return None
