"""The chain of custody: hashing entries, verifying the chain, and reading who holds an exhibit now.

Pure functions over the stored rows, so every rule has a unit test. The API in `api/evidence.py`
writes entries; nothing else does.

The first entry's `prev_hash` is the exhibit's own digest (its id, hashes, size and when it was
recorded), so changing a stored hash breaks the chain as surely as editing a handover does.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ostraka.models import CustodyEntry, CustodyKind, EvidenceItem

SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
MD5 = re.compile(r"^[0-9a-f]{32}$")
ANSWERS = frozenset({CustodyKind.ACCEPTED, CustodyKind.DECLINED, CustodyKind.WITHDRAWN})


def _iso(moment: datetime) -> str:
    """A time as hashed: UTC, so it reads the same however the database hands it back."""
    return (moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)).isoformat()


def _canonical(body: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def anchor(item: EvidenceItem) -> str:
    """The digest the chain starts from: what identifies the exhibit and can never change."""
    return _canonical(
        {
            "evidence_id": str(item.id),
            "incident_id": str(item.incident_id),
            "sha256": item.sha256,
            "sha1": item.sha1,
            "md5": item.md5,
            "size_bytes": item.size_bytes,
            "created_at": _iso(item.created_at),
        }
    )


def digest(entry: CustodyEntry) -> str:
    """An entry's hash: every field it records, and the hash of the entry before it."""
    ids = ("actor_id", "from_id", "to_id")
    return _canonical(
        {
            "evidence_id": str(entry.evidence_id),
            "position": entry.position,
            "kind": entry.kind.value,
            "at": _iso(entry.at),
            "recorded_at": _iso(entry.recorded_at),
            **{k: (str(getattr(entry, k)) if getattr(entry, k) else None) for k in ids},
            "actor_name": entry.actor_name,
            "from_name": entry.from_name,
            "to_name": entry.to_name,
            "purpose": entry.purpose,
            "location": entry.location,
            "acknowledgement": entry.acknowledgement,
            "hash_check": entry.hash_check.value if entry.hash_check else None,
            "offer_position": entry.offer_position,
            "prev_hash": entry.prev_hash,
        }
    )


@dataclass
class ChainCheck:
    ok: bool
    entries: int
    broken_at: int | None = None


def verify(item: EvidenceItem, entries: list[CustodyEntry]) -> ChainCheck:
    """Recompute every hash in order; the first position that doesn't match is reported."""
    previous = anchor(item)
    ordered = sorted(entries, key=lambda e: e.position)
    for expected, entry in enumerate(ordered, start=1):
        if entry.position != expected or entry.prev_hash != previous or digest(entry) != entry.hash:
            return ChainCheck(False, len(ordered), entry.position)
        previous = entry.hash
    return ChainCheck(True, len(ordered))


@dataclass
class State:
    """Where custody stands, read from the entries."""

    holder_id: Any = None
    holder_name: str = ""
    location: str = ""
    pending: CustodyEntry | None = None  # an offer not yet answered
    mismatch: bool = False  # someone re-hashed it on receipt and the SHA-256 didn't match


def state(item: EvidenceItem, entries: list[CustodyEntry]) -> State:
    """The current holder and location, and any open offer. Only a received or accepted entry moves
    custody; an offer leaves it where it is until the recipient accepts."""
    now = State(location=item.location)
    offers: dict[int, CustodyEntry] = {}
    for entry in sorted(entries, key=lambda e: e.position):
        if entry.kind in (CustodyKind.RECEIVED, CustodyKind.ACCEPTED):
            now.holder_id, now.holder_name = entry.to_id, entry.to_name
            now.location = entry.location or now.location
            if entry.hash_check is not None and entry.hash_check.value == "mismatch":
                now.mismatch = True
        if entry.kind is CustodyKind.OFFERED:
            offers[entry.position] = entry
        if entry.kind in ANSWERS and entry.offer_position is not None:
            offers.pop(entry.offer_position, None)
    now.pending = max(offers.values(), key=lambda e: e.position) if offers else None
    return now


def clean_hash(value: str, pattern: re.Pattern[str], name: str, *, required: bool = False) -> str:
    """A hash as stored: lower-case hex, spaces and colons removed. Empty is allowed unless required."""
    cleaned = re.sub(r"[\s:]", "", value or "").lower()
    if not cleaned:
        if required:
            raise ValueError(f"{name} is required.")
        return ""
    if not pattern.match(cleaned):
        raise ValueError(f"That isn't a {name}: it should be {_LENGTHS[name]} hexadecimal characters.")
    return cleaned


_LENGTHS = {"SHA-256": 64, "SHA-1": 40, "MD5": 32}
