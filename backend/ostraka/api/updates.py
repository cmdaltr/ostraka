"""Stakeholder updates: the rhythm, the stakeholders, drafting, rewording, approval, and the
observers' briefing.

Ostraka drafts each update from the record (`ostraka.updates.drafts`); comms adds a note, may ask an
AI model to reword it, and approves it. An update with a linter error can't be approved, and once
sent it never changes. Comms (or the commander, while nobody holds comms) approves.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit
from ostraka.analysis_schemas import LintFlag
from ostraka.auth import policy
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer, mask_text
from ostraka.models import (
    FINISHED,
    Asset,
    Finding,
    FindingStatus,
    Incident,
    IncidentRole,
    Stakeholder,
    StakeholderUpdate,
    Task,
    Team,
    UpdatePlan,
    UpdateStatus,
    User,
    utcnow,
)
from ostraka.notify import channels, restricted
from ostraka.standard import rephrase as guard
from ostraka.standard import update as standard
from ostraka.standard.assessment import sentences
from ostraka.update_schemas import (
    BriefingOut,
    BriefingUpdate,
    DraftPatch,
    LineOut,
    PlanIn,
    PlanOut,
    RephraseOut,
    RevisionOut,
    RhythmRow,
    StakeholderIn,
    StakeholderOut,
    UpdateOut,
    UpdatesOut,
)
from ostraka.updates import ai, drafts, rhythm

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["updates"])


def _roles(user: User, incident: Incident) -> tuple[bool, bool, bool]:
    """(commander, comms, anyone in comms)"""
    mine = {m.role for m in incident.members if m.user_id == user.id}
    named = any(m.role is IncidentRole.COMMS for m in incident.members)
    return IncidentRole.COMMANDER in mine, IncidentRole.COMMS in mine, named


def run_refusal(user: User, incident: Incident) -> str | None:
    commander, comms, _ = _roles(user, incident)
    return policy.can_run_updates(user, is_commander=commander, is_comms=comms)


def approve_refusal(user: User, incident: Incident) -> str | None:
    commander, comms, named = _roles(user, incident)
    return policy.can_approve_update(user, is_commander=commander, is_comms=comms, comms_named=named)


def _require(why: str | None) -> None:
    if why:
        raise HTTPException(status.HTTP_403_FORBIDDEN, why)


async def _update(db: AsyncSession, incident: Incident, update_id: uuid.UUID) -> StakeholderUpdate:
    update = await db.get(StakeholderUpdate, update_id)
    if update is None or update.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such update.")
    return update


def _editable(update: StakeholderUpdate, version: int) -> None:
    if update.status is not UpdateStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{update.ref} has been {update.status.value}: it can't change."
        )
    views.check_version(update.version, version, "update")


async def update_out(
    db: AsyncSession, user: User, incident: Incident, update: StakeholderUpdate
) -> UpdateOut:
    lines = drafts.lines_of(update)
    texts = drafts.texts_of(update, lines)
    reworded = update.rephrased if update.status is UpdateStatus.DRAFT else None
    shown_rephrase = reworded if reworded and len(reworded) == len(lines) else None
    if update.status is UpdateStatus.SENT and update.use_rephrased:
        shown_rephrase = texts
    check = await drafts.check(db, update) if update.status is UpdateStatus.DRAFT else standard.Check()
    why = approve_refusal(user, incident) or ("Fix the errors first." if check.errors else "")
    lookup = await views.names(db, [update.drafted_by, update.approved_by, *(r.by for r in update.revisions)])
    return UpdateOut(
        id=update.id,
        ref=update.ref,
        number=update.number,
        status=update.status,
        since=update.since,
        until=update.until,
        assessment=(update.content or {}).get("assessment_ref"),
        lines=[
            LineOut(
                section=line.section,
                kind=line.kind,
                ref=line.ref,
                text=for_viewer(user, line.text),
                rephrased=for_viewer(user, shown_rephrase[i]) if shown_rephrase else None,
            )
            for i, line in enumerate(lines)
        ],
        note=for_viewer(user, update.note),
        rephrased_by=update.rephrased_by,
        use_rephrased=update.use_rephrased,
        flags=[
            LintFlag(rule=f.rule, severity=f.severity, field=f.field, message=f.message, words=f.words)
            for f in check.flags
        ],
        can_approve=update.status is UpdateStatus.DRAFT and not why,
        why_not=why if update.status is UpdateStatus.DRAFT else "",
        drafted_by=views.person(lookup, update.drafted_by),
        drafted_at=update.drafted_at,
        approved_by=views.person(lookup, update.approved_by),
        approved_at=update.approved_at,
        recipients=update.recipients if user.role.value != "observer" else [],
        revisions=[
            RevisionOut(
                version=r.version,
                change=r.change,
                by=views.person(lookup, r.by),
                at=r.at,
                lines=[for_viewer(user, t) for t in r.lines],
            )
            for r in update.revisions
        ],
        version=update.version,
    )


async def plan_out(db: AsyncSession, incident: Incident) -> PlanOut:
    plan = await drafts.plan_of(db, incident.id)
    minutes = rhythm.every(incident.severity, plan)
    last = await drafts.last_sent(db, incident.id)
    base = last.approved_at if last and last.approved_at else incident.declared_at
    mode = (
        "severity"
        if plan is None or not plan.rhythm_set
        else ("every" if plan.every_minutes else "on_change")
    )
    lookup = await views.names(db, [plan.set_by] if plan else [])
    return PlanOut(
        mode=mode,
        every_minutes=minutes,
        words=rhythm.words(minutes),
        by_severity=[
            RhythmRow(severity=s, every_minutes=m, words=rhythm.words(m))
            for s, m in rhythm.BY_SEVERITY.items()
        ],
        last_sent_at=last.approved_at if last else None,
        next_due_at=rhythm.next_due(minutes, base),
        set_by=views.person(lookup, plan.set_by) if plan and plan.rhythm_set else None,
        version=plan.version if plan else 1,
    )


def stakeholder_out(incident: Incident, s: Stakeholder) -> StakeholderOut:
    if s.user is not None:
        ok = s.user.is_active and access.can_view(s.user, incident)
        return StakeholderOut(
            id=s.id,
            kind="person",
            name=s.user.name,
            email=s.user.email,
            can_receive=ok,
            why_not="" if ok else f"{s.user.name} can no longer see this incident.",
        )
    if s.team is not None:
        ok = bool(s.team.teams_webhook)
        return StakeholderOut(
            id=s.id,
            kind="teams",
            name=s.team.name,
            email=None,
            can_receive=ok,
            why_not="" if ok else f"{s.team.name} hasn't connected a Teams channel.",
        )
    ok = channels.email_enabled()
    return StakeholderOut(
        id=s.id,
        kind="email",
        name=s.name or s.email or "",
        email=s.email,
        can_receive=ok,
        why_not="" if ok else "Email isn't set up on this installation.",
    )


async def _all(db: AsyncSession, user: User, incident: Incident) -> UpdatesOut:
    draft = await drafts.open_draft(db, incident.id)
    sent = (
        await db.scalars(
            select(StakeholderUpdate)
            .where(
                StakeholderUpdate.incident_id == incident.id, StakeholderUpdate.status == UpdateStatus.SENT
            )
            .order_by(StakeholderUpdate.number)
        )
    ).all()
    stakeholders = (
        await db.scalars(
            select(Stakeholder).where(Stakeholder.incident_id == incident.id).order_by(Stakeholder.added_at)
        )
    ).all()
    try:
        provider, ai_why = ai.provider_for(incident), ""
    except ai.Unavailable as e:
        provider, ai_why = "", str(e)
    run_why = run_refusal(user, incident)
    approve_why = approve_refusal(user, incident)
    return UpdatesOut(
        plan=await plan_out(db, incident),
        stakeholders=[stakeholder_out(incident, s) for s in stakeholders],
        draft=await update_out(db, user, incident, draft) if draft else None,
        sent=[await update_out(db, user, incident, u) for u in sent],
        can_run=run_why is None,
        can_approve=approve_why is None,
        why_not=run_why or approve_why or "",
        email_enabled=channels.email_enabled(),
        ai=provider,
        ai_why_not=ai_why,
        restricted=restricted(incident),
    )


@router.get("/updates", response_model=UpdatesOut)
async def list_updates(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> UpdatesOut:
    """Everything about the incident's updates, for the people who work it. Observers read the
    briefing instead."""
    incident = await access.load(db, user, incident_id, edit=True)
    return await _all(db, user, incident)


@router.put("/updates/plan", response_model=UpdatesOut)
async def set_plan(
    incident_id: uuid.UUID,
    body: PlanIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UpdatesOut:
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    plan = await drafts.plan_of(db, incident.id)
    views.check_version(plan.version if plan else 1, body.version, "update rhythm")
    if plan is None:
        plan = UpdatePlan(incident_id=incident.id, version=1)
        db.add(plan)
    else:
        plan.version += 1
    plan.rhythm_set = body.mode != "severity"
    plan.every_minutes = body.every_minutes if body.mode == "every" else None
    plan.set_by, plan.set_at = user.id, utcnow()
    plan.nudged_for = None
    minutes = rhythm.every(incident.severity, plan)
    said = (
        f"the {incident.severity.value} default, {rhythm.words(minutes)}"
        if body.mode == "severity"
        else rhythm.words(minutes)
    )
    await activity.record(
        db,
        incident,
        user,
        "update_rhythm_set",
        f"{user.name} set stakeholder updates to {said}",
        entity_type="update",
        mode=body.mode,
        every_minutes=minutes,
    )
    await activity.finish(db, user)
    return await _all(db, user, incident)


# ---- stakeholders ------------------------------------------------------------------------------------


@router.post("/updates/stakeholders", response_model=UpdatesOut, status_code=status.HTTP_201_CREATED)
async def add_stakeholder(
    incident_id: uuid.UUID,
    body: StakeholderIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UpdatesOut:
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    stakeholder = Stakeholder(incident_id=incident.id, added_by=user.id, name=body.name.strip())
    if body.user_id:
        person = await db.get(User, body.user_id)
        if person is None or not person.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
        if not access.can_view(person, incident):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{person.name} can't see {incident.ref}. "
                "Add them to the incident first, then as a stakeholder.",
            )
        stakeholder.user_id, described = person.id, person.name
    elif body.team_id:
        team = await db.get(Team, body.team_id)
        if team is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team.")
        if not team.teams_webhook:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{team.name} hasn't connected a Teams channel.")
        stakeholder.team_id, described = team.id, f"{team.name}'s Teams channel"
    else:
        if not channels.email_enabled():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Email isn't set up on this installation, so outside addresses can't be sent to.",
            )
        stakeholder.email = str(body.email).lower()
        described = stakeholder.name or stakeholder.email
    db.add(stakeholder)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{described} is already a stakeholder.") from None
    if stakeholder.email:
        # An outside address receives incident text: a security-relevant change
        audit.record(
            db,
            "stakeholder_added",
            actor_id=user.id,
            ip=request.client.host if request.client else "",
            incident=incident.ref,
            email=stakeholder.email,
        )
    await activity.record(
        db,
        incident,
        user,
        "stakeholder_added",
        f"{user.name} added {described} to the stakeholders",
        entity_type="update",
        entity_id=stakeholder.id,
    )
    await activity.finish(db, user)
    return await _all(db, user, incident)


@router.delete("/updates/stakeholders/{stakeholder_id}", response_model=UpdatesOut)
async def remove_stakeholder(
    incident_id: uuid.UUID,
    stakeholder_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UpdatesOut:
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    stakeholder = await db.get(Stakeholder, stakeholder_id)
    if stakeholder is None or stakeholder.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such stakeholder.")
    described = stakeholder_out(incident, stakeholder).name
    if stakeholder.email:
        audit.record(
            db,
            "stakeholder_removed",
            actor_id=user.id,
            ip=request.client.host if request.client else "",
            incident=incident.ref,
            email=stakeholder.email,
        )
    await db.delete(stakeholder)
    await activity.record(
        db,
        incident,
        user,
        "stakeholder_removed",
        f"{user.name} removed {described} from the stakeholders",
        entity_type="update",
    )
    await activity.finish(db, user)
    return await _all(db, user, incident)


# ---- drafts ------------------------------------------------------------------------------------------


@router.post("/updates", response_model=UpdateOut, status_code=status.HTTP_201_CREATED)
async def draft_now(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> UpdateOut:
    """Draft the next update now, ahead of the rhythm."""
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    if existing := await drafts.open_draft(db, incident.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{existing.ref} is already in draft: rebuild it instead."
        )
    update = await drafts.make_draft(db, incident, user)
    await activity.finish(db, user)
    return await update_out(db, user, incident, update)


@router.post("/updates/{update_id}/rebuild", response_model=UpdateOut)
async def rebuild(
    incident_id: uuid.UUID,
    update_id: uuid.UUID,
    body: DraftPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UpdateOut:
    """Bring the draft up to now: the latest assessment and everything changed since the last update."""
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    update = await _update(db, incident, update_id)
    _editable(update, body.version)
    await drafts.rebuild(db, incident, update, user)
    await activity.record(
        db,
        incident,
        user,
        "update_rebuilt",
        f"{user.name} rebuilt {update.ref} from the record",
        entity_type="update",
        entity_id=update.id,
    )
    await activity.finish(db, user)
    return await update_out(db, user, incident, update)


@router.patch("/updates/{update_id}", response_model=UpdateOut)
async def revise(
    incident_id: uuid.UUID,
    update_id: uuid.UUID,
    body: DraftPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UpdateOut:
    """Comms' note, or the choice between Ostraka's words and the accepted rewording."""
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    update = await _update(db, incident, update_id)
    _editable(update, body.version)
    described = []
    if body.note is not None and body.note.strip() != update.note:
        update.note = body.note.strip()
        # The rewording covered the old lines; a new note makes it stale
        update.rephrased, update.rephrased_by, update.use_rephrased = None, "", False
        described.append("changed the note")
    if body.use_rephrased is not None and body.use_rephrased != update.use_rephrased:
        if body.use_rephrased and not update.rephrased:
            raise HTTPException(status.HTTP_409_CONFLICT, "There's no accepted rewording to use.")
        update.use_rephrased = body.use_rephrased
        described.append("chose the AI rewording" if body.use_rephrased else "chose Ostraka's own wording")
    if described:
        drafts.revise(update, "note" if "changed the note" in described else "wording", user)
        await activity.record(
            db,
            incident,
            user,
            "update_revised",
            f"{user.name} {' and '.join(described)} on {update.ref}",
            entity_type="update",
            entity_id=update.id,
        )
        await activity.finish(db, user)
    return await update_out(db, user, incident, update)


@router.post("/updates/{update_id}/rephrase", response_model=RephraseOut)
async def rephrase(
    incident_id: uuid.UUID,
    update_id: uuid.UUID,
    body: DraftPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> RephraseOut:
    """Ask the AI model to reword the draft. The TLP guard picks the model; Ostraka's rephrase check
    decides whether the result may be used, and a rejection leaves the draft as Ostraka wrote it."""
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    update = await _update(db, incident, update_id)
    _editable(update, body.version)
    try:
        provider = ai.provider_for(incident)
    except ai.Unavailable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None

    # The model is outside Ostraka: it sees the draft with indicators masked, as notify.outward would
    original = [mask_text(line.text) for line in standard.compose(drafts.composition(update))]
    reworded = await ai.rephrase(provider, original)
    problems = (
        ["The model didn't return the lines it was asked for."]
        if reworded is None
        else guard.problems(original, reworded)
    )
    accepted = not problems
    if accepted:
        update.rephrased, update.rephrased_by, update.use_rephrased = reworded, provider, True
        drafts.revise(update, "rephrased", user)
    await activity.record(
        db,
        incident,
        user,
        "update_rephrased" if accepted else "update_rephrase_rejected",
        f"{user.name} had {provider} reword {update.ref}: "
        + ("accepted by Ostraka's check" if accepted else f"rejected ({len(problems)} problem(s))"),
        entity_type="update",
        entity_id=update.id,
        provider=provider,
        problems=problems[:10],
    )
    await activity.finish(db, user)
    return RephraseOut(
        accepted=accepted, problems=problems, update=await update_out(db, user, incident, update)
    )


@router.post("/updates/{update_id}/approve", response_model=UpdateOut)
async def approve(
    incident_id: uuid.UUID,
    update_id: uuid.UUID,
    body: DraftPatch,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> UpdateOut:
    """Comms signs the update off and it goes to every stakeholder. It can't change after this."""
    incident = await access.load(db, user, incident_id, edit=True)
    _require(approve_refusal(user, incident))
    update = await _update(db, incident, update_id)
    _editable(update, body.version)
    check = await drafts.check(db, update)
    if check.errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, " ".join(f.message for f in check.errors))
    sent = await drafts.send(db, incident, update, user)
    reached = sum(r["sent"] for r in sent)
    audit.record(
        db,
        "stakeholder_update_approved",
        actor_id=user.id,
        ip=request.client.host if request.client else "",
        incident=incident.ref,
        update=update.ref,
        recipients=reached,
        rephrased_by=update.rephrased_by if update.use_rephrased else None,
    )
    await activity.record(
        db,
        incident,
        user,
        "update_sent",
        f"{user.name} approved {update.ref} (update {update.number}); "
        f"sent to {reached} of {len(sent)} stakeholder(s)",
        entity_type="update",
        entity_id=update.id,
        number=update.number,
        warnings=[f.rule for f in check.flags],
        rephrased_by=update.rephrased_by if update.use_rephrased else "",
    )
    await activity.finish(db, user)
    return await update_out(db, user, incident, update)


@router.post("/updates/{update_id}/discard", status_code=status.HTTP_204_NO_CONTENT)
async def discard(
    incident_id: uuid.UUID,
    update_id: uuid.UUID,
    body: DraftPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    incident = await access.load(db, user, incident_id, edit=True)
    _require(run_refusal(user, incident))
    update = await _update(db, incident, update_id)
    _editable(update, body.version)
    update.status = UpdateStatus.DISCARDED
    drafts.revise(update, "discarded", user)
    await activity.record(
        db,
        incident,
        user,
        "update_discarded",
        f"{user.name} discarded the draft {update.ref}",
        entity_type="update",
        entity_id=update.id,
    )
    await activity.finish(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- the briefing ------------------------------------------------------------------------------------


@router.get("/briefing", response_model=BriefingOut)
async def briefing(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> BriefingOut:
    """Read-only, for anyone who can see the incident, observers above all: where things stand and
    every update sent, in order. Indicators are masked by the usual rules."""
    incident = await access.load(db, user, incident_id)
    of = incident.id
    findings = (await db.scalars(select(Finding.status).where(Finding.incident_id == of))).all()
    assets = (await db.scalars(select(Asset.status).where(Asset.incident_id == of))).all()
    tasks = (await db.scalars(select(Task.status).where(Task.incident_id == of))).all()
    assessment = await drafts.latest_assessment(db, of)
    draft = await drafts.assessment_draft(db, assessment)
    sent = (
        await db.scalars(
            select(StakeholderUpdate)
            .where(StakeholderUpdate.incident_id == of, StakeholderUpdate.status == UpdateStatus.SENT)
            .order_by(StakeholderUpdate.number)
        )
    ).all()
    plan = await plan_out(db, incident)
    lookup = await views.names(db, [u.approved_by for u in sent])
    commander = access.commander_id(incident)
    names = await views.names(db, [commander])
    out = []
    for u in sent:
        lines = drafts.lines_of(u)
        texts = drafts.texts_of(u, lines)
        out.append(
            BriefingUpdate(
                ref=u.ref,
                number=u.number,
                approved_at=u.approved_at,
                approved_by=lookup.get(u.approved_by, "Former member") if u.approved_by else "",
                lines=[
                    LineOut(section=line.section, kind=line.kind, ref=line.ref, text=for_viewer(user, t))
                    for line, t in zip(lines, texts, strict=True)
                ],
                ai_worded=u.use_rephrased,
            )
        )
    return BriefingOut(
        ref=incident.ref,
        title=incident.title,
        severity=incident.severity,
        tlp=incident.tlp,
        phase=incident.phase,
        status=incident.status.value,
        commander=names.get(commander, "None named") if commander else "None named",
        declared_at=incident.declared_at,
        confirmed=sum(s is FindingStatus.CONFIRMED for s in findings),
        leads=sum(s is FindingStatus.LEAD for s in findings),
        assets=len(assets),
        contained=sum(s.value in standard.CONTAINED for s in assets),
        tasks_open=sum(s not in FINISHED for s in tasks),
        tasks_done=sum(s in FINISHED for s in tasks),
        assessment=[for_viewer(user, s) for s in sentences(draft)] if draft else [],
        rhythm=plan.words,
        next_due_at=plan.next_due_at if incident.status.value == "open" else None,
        updates=out,
    )
