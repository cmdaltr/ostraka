"""Short references (#OST-2026-014, #HNT-2026-003, #FND-12, #AST-27, #OBS-311, #TSK-8) and mentions in
comments.

Mentions are written by the editor as `@[Name](user:<uuid>)` or `@[Team](team:<uuid>)`, so a name
with spaces is unambiguous and renames don't break old comments.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from ostraka import branding

REF = re.compile(
    rf"#(({branding.INCIDENT_PREFIX}|{branding.HUNT_PREFIX})-(\d{{4}})-(\d{{3,}})|(FND|AST|OBS|TSK)-(\d+))\b"
)
MENTION = re.compile(r"@\[([^\]]{1,120})\]\((user|team):([0-9a-f-]{36})\)")


@dataclass(frozen=True)
class Ref:
    kind: str  # incident (an incident or a hunt) | finding | asset | observable | task
    year: int | None = None
    number: int | None = None
    record: str = "incident"  # for kind "incident": incident or hunt

    @property
    def text(self) -> str:
        if self.kind == "incident":
            prefix = branding.HUNT_PREFIX if self.record == "hunt" else branding.INCIDENT_PREFIX
            return f"{prefix}-{self.year}-{self.number:03d}"
        prefix = {"finding": "FND", "asset": "AST", "observable": "OBS", "task": "TSK"}[self.kind]
        return f"{prefix}-{self.number}"


_KINDS = {"FND": "finding", "AST": "asset", "OBS": "observable", "TSK": "task"}


def parse_ref(text: str) -> Ref | None:
    match = REF.fullmatch("#" + text.lstrip("#"))
    if not match:
        return None
    if match.group(3):
        record = "hunt" if match.group(2).upper() == branding.HUNT_PREFIX else "incident"
        return Ref("incident", int(match.group(3)), int(match.group(4)), record)
    return Ref(_KINDS[match.group(5)], number=int(match.group(6)))


def mentions(body: str) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """The user and team ids mentioned in a comment body."""
    users: set[uuid.UUID] = set()
    teams: set[uuid.UUID] = set()
    for _, kind, raw in MENTION.findall(body):
        try:
            (users if kind == "user" else teams).add(uuid.UUID(raw))
        except ValueError:
            continue
    return users, teams


def plain(body: str) -> str:
    """The text as a person reads it: `@[Name](user:<id>)` becomes `@Name`. For notifications and email."""
    return MENTION.sub(lambda m: f"@{m.group(1)}", body)
