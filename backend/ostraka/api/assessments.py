"""Assessments: judgements in structured parts. ostraka writes the sentences and lints the parts; an
error stops the assessment being saved, a warning is saved and shown with it. Assessments are never
edited: a new one supersedes the old, so the record shows how the judgement moved."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.analysis_schemas import (
    AssessmentIn,
    AssessmentOut,
    BasisOut,
    LintFlag,
    LintOut,
    NegativeEvidenceOut,
)
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.models import (
    Assessment,
    AssessmentBasis,
    AssessmentStatus,
    Finding,
    Incident,
    NegativeEvidence,
    User,
    utcnow,
)
from ostraka.standard import yardstick
from ostraka.standard.assessment import Absence, BasisItem, Draft, Flag, lint, sentences

router = APIRouter(prefix="/api/incidents/{incident_id}/assessments", tags=["assessments"])


async def _findings(db: AsyncSession, incident: Incident, ids: list[uuid.UUID]) -> list[Finding]:
    """The cited findings in the order given; 404 if any isn't in this incident."""
    if not ids:
        return []
    found = {
        f.id: f
        for f in (
            await db.scalars(select(Finding).where(Finding.id.in_(ids), Finding.incident_id == incident.id))
        ).all()
    }
    if missing := [i for i in ids if i not in found]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{len(missing)} of those findings aren't in this incident."
        )
    return [found[i] for i in dict.fromkeys(ids)]


def draft_of(
    judgement: str,
    probability,
    findings: list[Finding],
    absences,
    so_what: str = "",
    what_next: str = "",
) -> Draft:
    return Draft(
        judgement=judgement.strip(),
        probability=probability,
        basis=tuple(BasisItem(f"FND-{f.seq}", f.title, f.status) for f in findings),
        absences=tuple(
            Absence(a.absent, a.platform, a.scope, a.query, a.time_from, a.time_to) for a in absences
        ),
        so_what=so_what.strip(),
        what_next=what_next.strip(),
    )


def _flags(flags: list[Flag]) -> list[LintFlag]:
    return [
        LintFlag(rule=f.rule, severity=f.severity, field=f.field, message=f.message, words=f.words)
        for f in flags
    ]


async def basis_findings(db: AsyncSession, assessment: Assessment) -> list[Finding]:
    """The findings an assessment cites, in its order, as they stand now (a lead may since be confirmed)."""
    ids = [b.finding_id for b in assessment.basis]
    by_id = (
        {f.id: f for f in (await db.scalars(select(Finding).where(Finding.id.in_(ids)))).all()} if ids else {}
    )
    return [by_id[i] for i in ids if i in by_id]


def saved_draft(assessment: Assessment, findings: list[Finding]) -> Draft:
    return draft_of(
        assessment.judgement,
        assessment.probability,
        findings,
        assessment.absences,
        assessment.so_what,
        assessment.what_next,
    )


async def assessment_out(db: AsyncSession, user: User, assessment: Assessment) -> AssessmentOut:
    findings = await basis_findings(db, assessment)
    draft = saved_draft(assessment, findings)
    lookup = await views.names(db, [assessment.created_by])
    supersedes = await db.get(Assessment, assessment.supersedes_id) if assessment.supersedes_id else None
    return AssessmentOut(
        id=assessment.id,
        ref=f"ASM-{assessment.seq}",
        judgement=for_viewer(user, assessment.judgement),
        probability=assessment.probability,
        probability_words=yardstick.words(assessment.probability),
        probability_range=yardstick.range_of(assessment.probability),
        so_what=for_viewer(user, assessment.so_what),
        what_next=for_viewer(user, assessment.what_next),
        status=assessment.status,
        supersedes=f"ASM-{supersedes.seq}" if supersedes else None,
        superseded_at=assessment.superseded_at,
        basis=[
            BasisOut(finding_id=f.id, ref=f"FND-{f.seq}", title=for_viewer(user, f.title), status=f.status)
            for f in findings
        ],
        absences=[
            NegativeEvidenceOut(
                id=a.id,
                absent=for_viewer(user, a.absent),
                platform=a.platform,
                query=for_viewer(user, a.query),
                scope=for_viewer(user, a.scope),
                time_from=a.time_from,
                time_to=a.time_to,
            )
            for a in assessment.absences
        ],
        sentences=[for_viewer(user, s) for s in sentences(draft)],
        flags=_flags(lint(draft)),
        created_by=views.person(lookup, assessment.created_by),
        created_at=assessment.created_at,
    )


@router.get("", response_model=list[AssessmentOut])
async def list_assessments(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[AssessmentOut]:
    """Current assessments first, then the ones they superseded; newest first within each."""
    await access.load(db, user, incident_id)
    rows = await db.scalars(
        select(Assessment)
        .where(Assessment.incident_id == incident_id)
        .order_by(
            case((Assessment.status == AssessmentStatus.CURRENT, 0), else_=1), Assessment.created_at.desc()
        )
    )
    return [await assessment_out(db, user, a) for a in rows.all()]


@router.post("/lint", response_model=LintOut)
async def lint_draft(
    incident_id: uuid.UUID,
    body: AssessmentIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LintOut:
    """Check a draft and show what ostraka would write from it, without saving anything."""
    incident = await access.load(db, user, incident_id, edit=True)
    draft = draft_of(
        body.judgement,
        body.probability,
        await _findings(db, incident, body.finding_ids),
        body.absences,
        body.so_what,
        body.what_next,
    )
    return LintOut(flags=_flags(lint(draft)), sentences=[for_viewer(user, s) for s in sentences(draft)])


@router.post("", response_model=AssessmentOut, status_code=status.HTTP_201_CREATED)
async def make_assessment(
    incident_id: uuid.UUID,
    body: AssessmentIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AssessmentOut:
    incident = await access.load(db, user, incident_id, edit=True)
    findings = await _findings(db, incident, body.finding_ids)
    draft = draft_of(body.judgement, body.probability, findings, body.absences, body.so_what, body.what_next)
    if errors := [f for f in lint(draft) if f.severity == "error"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, " ".join(f.message for f in errors))

    replaced = None
    if body.supersedes_id is not None:
        replaced = await db.get(Assessment, body.supersedes_id)
        if replaced is None or replaced.incident_id != incident.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That assessment isn't in this incident.")
        if replaced.status is AssessmentStatus.SUPERSEDED:
            raise HTTPException(status.HTTP_409_CONFLICT, "That assessment has already been superseded.")
        replaced.status = AssessmentStatus.SUPERSEDED
        replaced.superseded_at = utcnow()

    assessment = Assessment(
        incident_id=incident.id,
        judgement=draft.judgement,
        probability=body.probability,
        so_what=draft.so_what,
        what_next=draft.what_next,
        supersedes_id=replaced.id if replaced else None,
        created_by=user.id,
        basis=[AssessmentBasis(finding_id=f.id, position=i) for i, f in enumerate(findings)],
        absences=[NegativeEvidence(position=i, **a.model_dump()) for i, a in enumerate(body.absences)],
    )
    db.add(assessment)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "assessment_made",
        f"{user.name} made ASM-{assessment.seq}{f', superseding ASM-{replaced.seq}' if replaced else ''}: "
        f"{sentences(draft)[0]}",
        entity_type="assessment",
        entity_id=assessment.id,
        probability=body.probability.value,
        basis=[f"FND-{f.seq}" for f in findings],
        absences=len(body.absences),
        warnings=[f.rule for f in lint(draft)],
    )
    await activity.finish(db, user)
    await db.refresh(assessment, ["basis", "absences"])
    return await assessment_out(db, user, assessment)
