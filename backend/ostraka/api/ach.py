"""Analysis of Competing Hypotheses for an incident: hypotheses, the evidence weighed against them,
and each analyst's ratings. Every change returns the whole matrix with its scores, so the grid and
the arithmetic never disagree."""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.analysis_schemas import (
    AchOut,
    CellOut,
    EvidenceIn,
    EvidenceOut,
    EvidencePatch,
    FollowUpIn,
    HypothesisIn,
    HypothesisOut,
    HypothesisPatch,
    RaterRating,
    RatingIn,
    RejectIn,
    TaskRef,
)
from ostraka.api.tasks import create_task
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import Person, Versioned
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.models import (
    AchEvidence,
    AchHypothesis,
    AchRating,
    EvidenceKind,
    Finding,
    HypothesisStatus,
    Incident,
    IncidentObservable,
    Observable,
    Task,
    User,
    utcnow,
)
from ostraka.standard import ach
from ostraka.work_schemas import TaskIn

router = APIRouter(prefix="/api/incidents/{incident_id}/ach", tags=["ach"])


async def matrix(db: AsyncSession, incident_id: uuid.UUID) -> tuple[list, list, dict, ach.Result]:
    """The incident's hypotheses, evidence and ratings, and the arithmetic on them."""
    hypotheses = list(
        (
            await db.scalars(
                select(AchHypothesis)
                .where(AchHypothesis.incident_id == incident_id)
                .order_by(AchHypothesis.seq)
            )
        ).all()
    )
    evidence = list(
        (
            await db.scalars(
                select(AchEvidence).where(AchEvidence.incident_id == incident_id).order_by(AchEvidence.seq)
            )
        ).all()
    )
    ratings: dict[ach.Cell, dict[uuid.UUID, object]] = defaultdict(dict)
    if hypotheses:
        rows = await db.scalars(
            select(AchRating).where(AchRating.hypothesis_id.in_([h.id for h in hypotheses]))
        )
        for r in rows.all():
            ratings[(r.hypothesis_id, r.evidence_id)][r.rater_id] = r.value
    result = ach.analyse(
        [h.id for h in hypotheses],
        [h.id for h in hypotheses if h.status is HypothesisStatus.OPEN],
        [ach.Evidence(e.id, e.credibility, e.relevance) for e in evidence],
        ratings,
    )
    return hypotheses, evidence, ratings, result


async def ach_out(db: AsyncSession, user: User, incident_id: uuid.UUID) -> AchOut:
    hypotheses, evidence, ratings, result = await matrix(db, incident_id)
    raters = sorted({r for values in ratings.values() for r in values}, key=str)
    lookup = await views.names(
        db,
        [*raters, *(h.created_by for h in hypotheses), *(h.rejected_by for h in hypotheses)]
        + [e.created_by for e in evidence],
    )
    task_ids = [h.milestone_task_id for h in hypotheses if h.milestone_task_id]
    tasks = (
        {t.id: t for t in (await db.scalars(select(Task).where(Task.id.in_(task_ids)))).all()}
        if task_ids
        else {}
    )
    finding_ids = [e.finding_id for e in evidence if e.finding_id]
    finding_refs = (
        dict((await db.execute(select(Finding.id, Finding.seq).where(Finding.id.in_(finding_ids)))).all())
        if finding_ids
        else {}
    )
    hyp_ref = {h.id: f"HYP-{h.seq}" for h in hypotheses}

    def task_ref(task_id: uuid.UUID | None) -> TaskRef | None:
        task = tasks.get(task_id) if task_id else None
        return TaskRef(id=task.id, ref=task.ref, status=task.status) if task else None

    return AchOut(
        hypotheses=[
            HypothesisOut(
                id=h.id,
                ref=hyp_ref[h.id],
                statement=for_viewer(user, h.statement),
                status=h.status,
                rejection_reason=for_viewer(user, h.rejection_reason),
                rejected_by=views.person(lookup, h.rejected_by),
                rejected_at=h.rejected_at,
                milestone=for_viewer(user, h.milestone),
                milestone_task=task_ref(h.milestone_task_id),
                score=result.scores.get(h.id, 0.0),
                leading=result.leading == h.id,
                created_by=views.person(lookup, h.created_by),
                created_at=h.created_at,
                version=h.version,
            )
            for h in hypotheses
        ],
        evidence=[
            EvidenceOut(
                id=e.id,
                ref=f"EVD-{e.seq}",
                kind=e.kind,
                finding_id=e.finding_id,
                finding_ref=f"FND-{finding_refs[e.finding_id]}" if e.finding_id in finding_refs else None,
                observable_id=e.incident_observable_id,
                text=for_viewer(user, e.text),
                credibility=e.credibility,
                relevance=e.relevance,
                non_diagnostic=e.id in result.non_diagnostic,
                pivotal=e.id in result.pivotal,
                lead_without=hyp_ref.get(result.pivotal[e.id]) if e.id in result.pivotal else None,
                created_by=views.person(lookup, e.created_by),
                version=e.version,
            )
            for e in evidence
        ],
        cells=[
            CellOut(
                hypothesis_id=h,
                evidence_id=e,
                consensus=result.consensus.get((h, e)),
                disputed=(h, e) in result.disputed,
                mine=values.get(user.id),
                ratings=[
                    RaterRating(rater=Person(id=r, name=lookup.get(r, "Former member")), value=v)
                    for r, v in values.items()
                ],
            )
            for (h, e), values in ratings.items()
        ],
        raters=[Person(id=r, name=lookup.get(r, "Former member")) for r in raters],
        max_raters=ach.MAX_RATERS,
        leading=hyp_ref.get(result.leading) if result.leading else None,
        algorithm=result.algorithm,
    )


async def _hypothesis(db: AsyncSession, incident: Incident, hypothesis_id: uuid.UUID) -> AchHypothesis:
    hypothesis = await db.get(AchHypothesis, hypothesis_id)
    if hypothesis is None or hypothesis.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That hypothesis isn't in this incident.")
    return hypothesis


async def _evidence(db: AsyncSession, incident: Incident, evidence_id: uuid.UUID) -> AchEvidence:
    evidence = await db.get(AchEvidence, evidence_id)
    if evidence is None or evidence.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That evidence isn't in this incident.")
    return evidence


async def _done(db: AsyncSession, user: User, incident: Incident) -> AchOut:
    await activity.finish(db, user)
    return await ach_out(db, user, incident.id)


@router.get("", response_model=AchOut)
async def get_matrix(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> AchOut:
    await access.load(db, user, incident_id)
    return await ach_out(db, user, incident_id)


# ---- hypotheses -----------------------------------------------------------------------------------


@router.post("/hypotheses", response_model=AchOut, status_code=status.HTTP_201_CREATED)
async def add_hypothesis(
    incident_id: uuid.UUID,
    body: HypothesisIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    incident = await access.load(db, user, incident_id, edit=True)
    hypothesis = AchHypothesis(
        incident_id=incident.id,
        statement=body.statement.strip(),
        milestone=body.milestone.strip(),
        created_by=user.id,
    )
    db.add(hypothesis)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "hypothesis_added",
        f"{user.name} added HYP-{hypothesis.seq}: {hypothesis.statement}",
        entity_type="hypothesis",
        entity_id=hypothesis.id,
    )
    return await _done(db, user, incident)


@router.patch("/hypotheses/{hypothesis_id}", response_model=AchOut)
async def update_hypothesis(
    incident_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    body: HypothesisPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    incident = await access.load(db, user, incident_id, edit=True)
    hypothesis = await _hypothesis(db, incident, hypothesis_id)
    views.check_version(hypothesis.version, body.version, "hypothesis")
    changes = {k: v.strip() for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items() if v}
    for field, value in changes.items():
        setattr(hypothesis, field, value)
    if changes:
        hypothesis.version += 1
        await activity.record(
            db,
            incident,
            user,
            "hypothesis_updated",
            f"{user.name} edited HYP-{hypothesis.seq}",
            entity_type="hypothesis",
            entity_id=hypothesis.id,
            fields=sorted(changes),
        )
    return await _done(db, user, incident)


@router.post("/hypotheses/{hypothesis_id}/reject", response_model=AchOut)
async def reject_hypothesis(
    incident_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    body: RejectIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    """Reject a hypothesis, saying why. Its score and the algorithm behind it go into the activity
    log, so the reason can be checked against the arithmetic at the time."""
    incident = await access.load(db, user, incident_id, edit=True)
    hypothesis = await _hypothesis(db, incident, hypothesis_id)
    views.check_version(hypothesis.version, body.version, "hypothesis")
    if hypothesis.status is HypothesisStatus.REJECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "That hypothesis is already rejected.")
    _, _, _, result = await matrix(db, incident.id)
    hypothesis.status = HypothesisStatus.REJECTED
    hypothesis.rejection_reason = body.reason.strip()
    hypothesis.rejected_by = user.id
    hypothesis.rejected_at = utcnow()
    hypothesis.version += 1
    await activity.record(
        db,
        incident,
        user,
        "hypothesis_rejected",
        f"{user.name} rejected HYP-{hypothesis.seq}: {hypothesis.rejection_reason}",
        entity_type="hypothesis",
        entity_id=hypothesis.id,
        score=result.scores.get(hypothesis.id, 0.0),
        algorithm=result.algorithm,
    )
    return await _done(db, user, incident)


@router.post("/hypotheses/{hypothesis_id}/reopen", response_model=AchOut)
async def reopen_hypothesis(
    incident_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    body: Versioned,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    incident = await access.load(db, user, incident_id, edit=True)
    hypothesis = await _hypothesis(db, incident, hypothesis_id)
    views.check_version(hypothesis.version, body.version, "hypothesis")
    hypothesis.status = HypothesisStatus.OPEN
    hypothesis.rejected_by = hypothesis.rejected_at = None
    hypothesis.version += 1
    await activity.record(
        db,
        incident,
        user,
        "hypothesis_reopened",
        f"{user.name} reopened HYP-{hypothesis.seq}",
        entity_type="hypothesis",
        entity_id=hypothesis.id,
    )
    return await _done(db, user, incident)


@router.post("/hypotheses/{hypothesis_id}/milestone-task", response_model=AchOut)
async def milestone_task(
    incident_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    body: FollowUpIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    """Turn what would change our view of a hypothesis into a task, so someone goes and looks."""
    incident = await access.load(db, user, incident_id, edit=True)
    hypothesis = await _hypothesis(db, incident, hypothesis_id)
    if not hypothesis.milestone:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Write the milestone first.")
    if hypothesis.milestone_task_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "That milestone is already a task.")
    task = await create_task(
        db,
        incident,
        user,
        TaskIn(
            title=body.title or f"Check: {hypothesis.milestone}"[:200],
            description=f"Milestone for HYP-{hypothesis.seq}: {hypothesis.statement}",
            team_id=body.team_id,
            assignee_id=body.assignee_id,
            due_at=body.due_at,
        ),
        origin=f"from HYP-{hypothesis.seq}'s milestone",
    )
    hypothesis.milestone_task_id = task.id
    hypothesis.version += 1
    return await _done(db, user, incident)


# ---- evidence -------------------------------------------------------------------------------------


@router.post("/evidence", response_model=AchOut, status_code=status.HTTP_201_CREATED)
async def add_evidence(
    incident_id: uuid.UUID,
    body: EvidenceIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    """Add evidence to weigh: a finding, an observable, an assumption, or something absent that a
    hypothesis would lead us to expect. A finding or observable is described by its own title or
    value unless other words are given."""
    incident = await access.load(db, user, incident_id, edit=True)
    text = body.text.strip()
    finding_id = observable_id = None
    if body.kind is EvidenceKind.FINDING:
        finding = await db.get(Finding, body.finding_id)
        if finding is None or finding.incident_id != incident.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That finding isn't in this incident.")
        finding_id, text = finding.id, text or f"FND-{finding.seq}: {finding.title}"
    elif body.kind is EvidenceKind.OBSERVABLE:
        row = (
            await db.execute(
                select(IncidentObservable, Observable)
                .join(Observable, Observable.id == IncidentObservable.observable_id)
                .where(
                    IncidentObservable.id == body.observable_id, IncidentObservable.incident_id == incident.id
                )
            )
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That observable isn't in this incident.")
        item, observable = row
        observable_id, text = item.id, text or f"OBS-{observable.seq}: {observable.value}"
    evidence = AchEvidence(
        incident_id=incident.id,
        kind=body.kind,
        finding_id=finding_id,
        incident_observable_id=observable_id,
        text=text,
        credibility=body.credibility,
        relevance=body.relevance,
        created_by=user.id,
    )
    db.add(evidence)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "evidence_added",
        f"{user.name} added EVD-{evidence.seq} ({body.kind.value}) to the ACH matrix: {text}",
        entity_type="evidence",
        entity_id=evidence.id,
    )
    return await _done(db, user, incident)


@router.patch("/evidence/{evidence_id}", response_model=AchOut)
async def update_evidence(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    body: EvidencePatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    incident = await access.load(db, user, incident_id, edit=True)
    evidence = await _evidence(db, incident, evidence_id)
    views.check_version(evidence.version, body.version, "evidence")
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items() if v is not None
    }
    for field, value in changes.items():
        setattr(evidence, field, value.strip() if isinstance(value, str) else value)
    if changes:
        evidence.version += 1
        await activity.record(
            db,
            incident,
            user,
            "evidence_updated",
            f"{user.name} edited EVD-{evidence.seq}",
            entity_type="evidence",
            entity_id=evidence.id,
            changes={k: str(v) for k, v in changes.items() if k != "text"},
        )
    return await _done(db, user, incident)


@router.delete("/evidence/{evidence_id}", response_model=AchOut)
async def remove_evidence(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    incident = await access.load(db, user, incident_id, edit=True)
    evidence = await _evidence(db, incident, evidence_id)
    await activity.record(
        db,
        incident,
        user,
        "evidence_removed",
        f"{user.name} removed EVD-{evidence.seq} and its ratings from the ACH matrix: {evidence.text}",
        entity_type="evidence",
        entity_id=evidence.id,
    )
    await db.delete(evidence)
    return await _done(db, user, incident)


# ---- ratings --------------------------------------------------------------------------------------


@router.put("/ratings", response_model=AchOut)
async def rate(
    incident_id: uuid.UUID,
    body: RatingIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AchOut:
    """Your rating of one piece of evidence against one hypothesis; null takes it back."""
    incident = await access.load(db, user, incident_id, edit=True)
    hypothesis = await _hypothesis(db, incident, body.hypothesis_id)
    evidence = await _evidence(db, incident, body.evidence_id)
    if hypothesis.status is HypothesisStatus.REJECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Reopen the hypothesis before rating against it.")
    existing = await db.get(AchRating, (hypothesis.id, evidence.id, user.id))
    if body.value is None:
        if existing is not None:
            await db.delete(existing)
    elif existing is not None:
        existing.value = body.value
    else:
        raters = set(
            (
                await db.scalars(
                    select(func.distinct(AchRating.rater_id))
                    .join(AchHypothesis, AchHypothesis.id == AchRating.hypothesis_id)
                    .where(AchHypothesis.incident_id == incident.id)
                )
            ).all()
        )
        if user.id not in raters and len(raters) >= ach.MAX_RATERS:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{ach.MAX_RATERS} analysts are already rating this matrix, the most it takes.",
            )
        db.add(
            AchRating(
                hypothesis_id=hypothesis.id, evidence_id=evidence.id, rater_id=user.id, value=body.value
            )
        )
    await activity.record(
        db,
        incident,
        user,
        "ach_rated",
        f"{user.name} rated EVD-{evidence.seq} against HYP-{hypothesis.seq}: "
        f"{body.value.value.upper() if body.value else 'cleared'}",
        entity_type="hypothesis",
        entity_id=hypothesis.id,
        evidence=f"EVD-{evidence.seq}",
        value=body.value.value if body.value else None,
    )
    return await _done(db, user, incident)
