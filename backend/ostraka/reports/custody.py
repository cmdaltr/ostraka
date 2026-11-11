"""The technical report's evidence section: each exhibit's hashes and its chain of custody, with
whether the chain verified at export."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import custody
from ostraka.models import CustodyKind, EvidenceItem, Finding, FindingEvidence, HashCheck

STEP_WORDS = {
    CustodyKind.RECEIVED: "Received",
    CustodyKind.OFFERED: "Offered",
    CustodyKind.ACCEPTED: "Accepted",
    CustodyKind.DECLINED: "Declined",
    CustodyKind.WITHDRAWN: "Withdrawn",
}


async def gather(db: AsyncSession, incident_id) -> list[tuple[EvidenceItem, list[str]]]:
    """Every exhibit in the incident, in order, with the findings that cite it."""
    items = (
        await db.scalars(
            select(EvidenceItem).where(EvidenceItem.incident_id == incident_id).order_by(EvidenceItem.seq)
        )
    ).all()
    cited: dict = {i.id: [] for i in items}
    if items:
        rows = await db.execute(
            select(FindingEvidence.evidence_id, Finding.seq)
            .join(Finding, Finding.id == FindingEvidence.finding_id)
            .where(FindingEvidence.evidence_id.in_(list(cited)))
            .order_by(Finding.seq)
        )
        for evidence_id, seq in rows.all():
            cited[evidence_id].append(f"FND-{seq}")
    return [(i, cited[i.id]) for i in items]


def _when(moment: datetime) -> str:
    return f"{moment:%Y-%m-%d %H:%M} UTC"


def _detail(entry) -> str:
    parts = [p for p in (entry.purpose, entry.acknowledgement) if p]
    if entry.location and entry.kind in (CustodyKind.RECEIVED, CustodyKind.ACCEPTED):
        parts.append(f"Held at {entry.location}")
    if entry.hash_check is HashCheck.MATCH:
        parts.append("SHA-256 re-checked: matches")
    elif entry.hash_check is HashCheck.MISMATCH:
        parts.append("SHA-256 re-checked: DOES NOT MATCH")
    return ". ".join(parts) or "—"


def blocks(exhibits: list[tuple[EvidenceItem, list[str]]]) -> list[tuple]:
    """Report blocks for the evidence section; nothing when the incident holds no exhibits."""
    if not exhibits:
        return []
    rows = []
    for item, cited in exhibits:
        now = custody.state(item, list(item.entries))
        check = custody.verify(item, list(item.entries))
        rows.append(
            [
                item.ref,
                item.label,
                now.holder_name or "—",
                now.location or "—",
                ", ".join(cited) or "—",
                "Intact" if check.ok else f"BROKEN at {check.broken_at}",
            ]
        )
    out: list[tuple] = [
        ("section", "Evidence and Chain of Custody"),
        (
            "para",
            "Each exhibit is identified by the SHA-256 taken when it was recorded. Custody moves only "
            "when the person receiving it accepts, in their own words; every step is hash-chained to "
            "the one before and written to the activity log, so the record can't be rewritten without "
            "it showing. Each chain was verified at export.",
        ),
        (
            "table",
            (
                ["Ref", "Exhibit", "Held by", "Where", "Cited by", "Chain"],
                rows,
                [1.8, 4.4, 2.6, 4.0, 2.2, 2.0],
                {},
            ),
            "Exhibits and where they are now.",
            False,
        ),
    ]
    for item, _ in exhibits:
        out.append(("sub", f"{item.ref}: {item.label}"))
        hashes = [f"SHA-256 {item.sha256}"]
        if item.sha1:
            hashes.append(f"SHA-1   {item.sha1}")
        if item.md5:
            hashes.append(f"MD5     {item.md5}")
        if item.size_bytes is not None:
            hashes.append(f"Size    {item.size_bytes:,} bytes")
        out.append(("code", "\n".join(hashes)))
        if item.description.strip():
            out.append(("para", item.description.strip()))
        out.append(
            (
                "table",
                (
                    ["#", "When", "Step", "From", "To", "Detail"],
                    [
                        [
                            str(e.position),
                            _when(e.at),
                            STEP_WORDS[e.kind],
                            e.from_name or "—",
                            e.to_name or "—",
                            _detail(e),
                        ]
                        for e in item.entries
                    ],
                    [0.8, 3.0, 1.9, 2.4, 2.4, 6.5],
                    {},
                ),
                f"The chain of custody of {item.ref}.",
                False,
            )
        )
    return out
