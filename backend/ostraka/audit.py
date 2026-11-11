"""The security log: sign-ins, invitations, account and MFA changes."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.log import get_logger
from ostraka.models import SecurityEvent

log = get_logger(__name__)


def record(
    db: AsyncSession,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    ip: str = "",
    **detail: Any,
) -> None:
    """Add an event to the session; it's written with the caller's commit."""
    db.add(SecurityEvent(action=action, actor_id=actor_id, subject_id=subject_id, ip=ip, detail=detail))
    log.info("security: %s actor=%s subject=%s %s", action, actor_id, subject_id, detail or "")
