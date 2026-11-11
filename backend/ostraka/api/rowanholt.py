"""Rowanholt on an incident: the jobs and cases it's linked to and their status, the rows its findings
cite, and Rowanholt's findings brought in as leads.

Rowanholt holds the evidence and Ostraka doesn't copy it: a citation keeps the row's reference, its
hash and the row as it read when cited, with a link back. Whatever Rowanholt called a finding
(lead, confirmed, benign), it arrives here as a lead: only an analyst confirms a finding in Ostraka,
after attaching a verification. Citing a row can be that verification, if the analyst says so.

When Rowanholt can't be reached, the incident shows what was last seen, and the parts that need an
answer (linking, importing, citing) say so and change nothing. The TLP guard is
`integrations.tlp_refusal`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.integration_schemas import (
    CitationIn,
    CitationOut,
    ImportIn,
    ImportOut,
    RemoteFinding,
    RemoteJob,
    RowanholtLinkIn,
    RowanholtLinkOut,
    RowanholtOut,
    RowsOut,
)
from ostraka.integrations import configured, tlp_refusal
from ostraka.integrations.http import Refused, Unreachable
from ostraka.integrations.rowanholt import CASE_HOST, Rowanholt, case_status, cite, job_summary
from ostraka.models import (
    Finding,
    FindingKind,
    FindingStatus,
    Incident,
    Product,
    RowanholtCitation,
    RowanholtLink,
    User,
    Verification,
    VerificationMethod,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["rowanholt"])

NOT_CONNECTED = "No Rowanholt server is connected. An admin connects one under Installation."
MAX_EXCERPT_CELLS = 40
MAX_CELL = 500


async def _server(db: AsyncSession, incident: Incident) -> Rowanholt:
    """The Rowanholt client this incident may use, or an error that says why not."""
    row = await configured(db, Product.ROWANHOLT)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_CONNECTED)
    if refusal := tlp_refusal(Product.ROWANHOLT, incident, row):
        raise HTTPException(status.HTTP_403_FORBIDDEN, refusal)
    return Rowanholt.of(row)


def _failed(exc: Unreachable | Refused) -> HTTPException:
    if isinstance(exc, Refused) and exc.status == 404:
        return HTTPException(status.HTTP_404_NOT_FOUND, f"Rowanholt: {exc}")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


async def _link(db: AsyncSession, incident: Incident, link_id: uuid.UUID) -> RowanholtLink:
    link = await db.get(RowanholtLink, link_id)
    if link is None or link.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That Rowanholt link isn't in this incident.")
    return link


def _job_of(link: RowanholtLink, job_id: str) -> str:
    """The job a request is about: the link's own, or one of its case's jobs."""
    if link.job_id:
        if job_id and job_id != link.job_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That job isn't the one linked.")
        return link.job_id
    if not job_id or job_id not in {str(j.get("id")) for j in link.jobs or []}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Choose one of this case's jobs.")
    return job_id


async def _refresh(client: Rowanholt, link: RowanholtLink) -> None:
    """Fetch a link's status from Rowanholt and keep it. Raises Unreachable or Refused."""
    if link.job_id:
        job = await client.job(link.job_id)
        link.status, link.progress = job_summary(job)
        link.case_number = link.case_number or str(job.get("case_number") or "")
    else:
        case = await client.case_jobs(link.case_number)
        if not case.get("exists"):
            raise Refused(404, f"there's no case {link.case_number}.")
        link.jobs = [
            {"id": str(j.get("id")), "status": str(j.get("status") or ""), "created_at": j.get("created_at")}
            for j in case.get("jobs", [])
        ]
        link.status, link.progress = case_status(link.jobs), None
    link.checked_at = utcnow()


def _excerpt(user: User, cells: dict) -> dict[str, str]:
    return {str(k): for_viewer(user, str(v)) for k, v in (cells or {}).items()}


async def _out(db: AsyncSession, user: User, incident: Incident, base: str | None) -> RowanholtOut:
    links = (
        await db.scalars(
            select(RowanholtLink)
            .where(RowanholtLink.incident_id == incident.id)
            .order_by(RowanholtLink.linked_at)
        )
    ).all()
    citations = (
        await db.scalars(
            select(RowanholtCitation)
            .where(RowanholtCitation.incident_id == incident.id)
            .order_by(RowanholtCitation.cited_at)
        )
    ).all()
    seqs = dict(
        (await db.execute(select(Finding.id, Finding.seq).where(Finding.incident_id == incident.id))).all()
    )
    lookup = await views.names(db, [*(k.linked_by for k in links), *(c.cited_by for c in citations)])
    client = Rowanholt(base or "", "")
    return RowanholtOut(
        configured=base is not None,
        reachable=None,
        message="",
        links=[
            RowanholtLinkOut(
                id=k.id,
                job_id=k.job_id,
                case_number=k.case_number,
                status=k.status,
                progress=k.progress,
                jobs=[
                    RemoteJob(
                        id=str(j.get("id")),
                        status=str(j.get("status") or ""),
                        created_at=j.get("created_at"),
                        url=client.job_link(str(j.get("id"))) if base else "",
                    )
                    for j in k.jobs or []
                ],
                checked_at=k.checked_at,
                url=(client.job_link(k.job_id) if k.job_id else client.url) if base else "",
                linked_by=views.person(lookup, k.linked_by),
                linked_at=k.linked_at,
            )
            for k in links
        ],
        citations=[
            CitationOut(
                id=c.id,
                finding_id=c.finding_id,
                finding_ref=f"FND-{seqs.get(c.finding_id, '?')}",
                job_id=c.job_id,
                case_number=c.case_number,
                host=c.host,
                artefact=c.artefact,
                row_ref=c.row_ref,
                row_hash=c.row_hash,
                cite=cite(c.host, c.artefact, c.row_ref, c.row_hash),
                excerpt=_excerpt(user, c.excerpt),
                remote_finding_id=c.remote_finding_id,
                remote_status=c.remote_status,
                verification_id=c.verification_id,
                url=client.row_link(c.job_id, c.host, c.artefact, c.row_ref) if base else "",
                cited_by=views.person(lookup, c.cited_by),
                cited_at=c.cited_at,
            )
            for c in citations
        ],
    )


@router.get("/rowanholt", response_model=RowanholtOut)
async def rowanholt_view(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> RowanholtOut:
    """The links with their status, fetched now when Rowanholt can be reached, otherwise as last seen."""
    incident = await access.load(db, user, incident_id)
    row = await configured(db, Product.ROWANHOLT)
    if row is None:
        out = await _out(db, user, incident, None)
        out.message = NOT_CONNECTED if out.links else ""
        return out
    client = Rowanholt.of(row)
    refusal = tlp_refusal(Product.ROWANHOLT, incident, row)
    links = (await db.scalars(select(RowanholtLink).where(RowanholtLink.incident_id == incident.id))).all()
    reachable, message = None, refusal or ""
    if links and not refusal:
        reachable = True
        for link in links:
            try:
                await _refresh(client, link)
            except Unreachable as exc:
                reachable, message = False, f"{exc} Showing the status as last seen."
                break
            except Refused as exc:
                message = f"Rowanholt: {exc}"
        await db.commit()
    out = await _out(db, user, incident, client.url)
    out.reachable, out.message = reachable, message
    return out


@router.post("/rowanholt/links", response_model=RowanholtOut, status_code=status.HTTP_201_CREATED)
async def link_rowanholt(
    incident_id: uuid.UUID,
    body: RowanholtLinkIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> RowanholtOut:
    """Link a Rowanholt job, or a case number and all its jobs. Rowanholt is asked first, so only
    something it has (and this token may see) can be linked."""
    incident = await access.load(db, user, incident_id, edit=True)
    client = await _server(db, incident)
    existing = await db.scalar(
        select(RowanholtLink).where(
            RowanholtLink.incident_id == incident.id,
            RowanholtLink.job_id == body.job_id,
            RowanholtLink.case_number == ("" if body.job_id else body.case_number),
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That's linked already.")
    link = RowanholtLink(
        id=uuid.uuid4(),
        incident_id=incident.id,
        job_id=body.job_id,
        case_number="" if body.job_id else body.case_number,
        status="",
        jobs=[],
        linked_by=user.id,
        linked_at=utcnow(),
    )
    try:
        await _refresh(client, link)
    except (Unreachable, Refused) as exc:
        raise _failed(exc) from exc
    db.add(link)
    what = f"job {link.job_id}" if link.job_id else f"case {link.case_number}"
    await activity.record(
        db,
        incident,
        user,
        "rowanholt_linked",
        f"{user.name} linked Rowanholt {what}",
        entity_type="rowanholt",
        entity_id=link.id,
    )
    await activity.finish(db, user)
    return await _out(db, user, incident, client.url)


@router.delete("/rowanholt/links/{link_id}", response_model=RowanholtOut)
async def unlink_rowanholt(
    incident_id: uuid.UUID,
    link_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> RowanholtOut:
    """Unlink. Citations already made stay with their findings."""
    incident = await access.load(db, user, incident_id, edit=True)
    link = await _link(db, incident, link_id)
    what = f"job {link.job_id}" if link.job_id else f"case {link.case_number}"
    await db.delete(link)
    await activity.record(
        db,
        incident,
        user,
        "rowanholt_unlinked",
        f"{user.name} unlinked Rowanholt {what}",
        entity_type="rowanholt",
        entity_id=link_id,
    )
    await activity.finish(db, user)
    row = await configured(db, Product.ROWANHOLT)
    return await _out(db, user, incident, Rowanholt.of(row).url if row else None)


@router.get("/rowanholt/links/{link_id}/case")
async def rowanholt_case(
    incident_id: uuid.UUID,
    link_id: uuid.UUID,
    job_id: str = "",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """A linked job's hosts and their tables, from Rowanholt, for choosing a row to cite."""
    incident = await access.load(db, user, incident_id)
    client = await _server(db, incident)
    job = _job_of(await _link(db, incident, link_id), job_id)
    try:
        case = await client.case(job)
    except (Unreachable, Refused) as exc:
        raise _failed(exc) from exc
    hosts = [
        {"host": str(h.get("host")), "files": [str(f.get("path")) for f in h.get("files", [])]}
        for h in case.get("hosts", [])
    ]
    tables = [str(f.get("path")) for f in case.get("case_files", [])]
    if tables:
        hosts.append({"host": CASE_HOST, "files": tables})
    return {"job_id": job, "hosts": hosts, "findings": case.get("findings", {})}


@router.get("/rowanholt/links/{link_id}/rows", response_model=RowsOut)
async def rowanholt_rows(
    incident_id: uuid.UUID,
    link_id: uuid.UUID,
    host: str = Query(min_length=1, max_length=255),
    path: str = Query(min_length=1, max_length=1000),
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    job_id: str = "",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> RowsOut:
    """A page of a table's rows, read from Rowanholt as this viewer would see them in Ostraka."""
    incident = await access.load(db, user, incident_id)
    client = await _server(db, incident)
    job = _job_of(await _link(db, incident, link_id), job_id)
    try:
        page = await client.rows(job, host, path, q, offset, 50)
    except (Unreachable, Refused) as exc:
        raise _failed(exc) from exc
    rows = [
        {
            "ref": str(r.get("ref")),
            "hash": str(r.get("hash") or ""),
            "cells": _excerpt(user, r.get("cells", {})),
        }
        for r in page.get("rows", [])
    ]
    return RowsOut(
        columns=[str(c) for c in page.get("columns", [])],
        rows=rows,
        total=int(page.get("total") or 0),
        exact=bool(page.get("exact", True)),
    )


@router.get("/rowanholt/links/{link_id}/findings", response_model=list[RemoteFinding])
async def rowanholt_findings(
    incident_id: uuid.UUID,
    link_id: uuid.UUID,
    job_id: str = "",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[RemoteFinding]:
    """Rowanholt's findings for a linked job, each marked with what it is here if it's been brought in."""
    incident = await access.load(db, user, incident_id)
    client = await _server(db, incident)
    job = _job_of(await _link(db, incident, link_id), job_id)
    try:
        found = await client.findings(job)
    except (Unreachable, Refused) as exc:
        raise _failed(exc) from exc
    imported = dict(
        (
            await db.execute(
                select(RowanholtCitation.remote_finding_id, Finding.seq)
                .join(Finding, Finding.id == RowanholtCitation.finding_id)
                .where(
                    RowanholtCitation.incident_id == incident.id,
                    RowanholtCitation.remote_finding_id.is_not(None),
                )
            )
        ).all()
    )
    out = []
    for f in found:
        remote = f"{job}:{f.get('id')}"
        host, artefact, ref, digest = (
            str(f.get(k) or "") for k in ("host", "artefact", "row_ref", "row_hash")
        )
        out.append(
            RemoteFinding(
                id=str(f.get("id")),
                job_id=job,
                host=host,
                artefact=artefact,
                row_ref=ref,
                row_hash=digest,
                title=for_viewer(user, str(f.get("title") or "")),
                technique=str(f.get("technique") or ""),
                status=str(f.get("status") or "lead"),
                intact=f.get("intact") if isinstance(f.get("intact"), bool) else None,
                excerpt=for_viewer(user, str(f.get("excerpt") or ""))[:MAX_CELL],
                imported_as=f"FND-{imported[remote]}" if remote in imported else None,
                cite=cite(host, artefact, ref, digest),
            )
        )
    return out


def _snapshot(cells: dict) -> dict[str, str]:
    """The row as it read, cut to a size a finding can carry."""
    return {str(k)[:120]: str(v)[:MAX_CELL] for k, v in list((cells or {}).items())[:MAX_EXCERPT_CELLS]}


@router.post("/rowanholt/import", response_model=ImportOut, status_code=status.HTTP_201_CREATED)
async def import_findings(
    incident_id: uuid.UUID,
    body: ImportIn,
    link_id: uuid.UUID = Query(),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ImportOut:
    """Bring Rowanholt findings in as leads, each citing its row. Never as confirmed findings, whatever
    Rowanholt called them: confirming one here takes an analyst and a verification."""
    incident = await access.load(db, user, incident_id, edit=True)
    client = await _server(db, incident)
    link = await _link(db, incident, link_id)
    job = _job_of(link, body.job_id)
    try:
        found = {str(f.get("id")): f for f in await client.findings(job)}
    except (Unreachable, Refused) as exc:
        raise _failed(exc) from exc
    if missing := set(body.finding_ids) - set(found):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Rowanholt has no finding {sorted(missing)[0]} in that job."
        )
    already = set(
        (
            await db.scalars(
                select(RowanholtCitation.remote_finding_id).where(
                    RowanholtCitation.incident_id == incident.id
                )
            )
        ).all()
    )
    imported, skipped = [], 0
    for remote_id in dict.fromkeys(body.finding_ids):
        remote = f"{job}:{remote_id}"
        if remote in already:
            skipped += 1
            continue
        f = found[remote_id]
        host, artefact, ref, digest = (
            str(f.get(k) or "") for k in ("host", "artefact", "row_ref", "row_hash")
        )
        technique = str(f.get("technique") or "").upper()
        said = str(f.get("status") or "lead")
        changed = (
            " The row no longer reads as it did when it was flagged." if f.get("intact") is False else ""
        )
        finding = Finding(
            incident_id=incident.id,
            kind=FindingKind.EVIDENCE,
            title=(str(f.get("title") or "") or f"Rowanholt row {ref}")[:200],
            statement=(
                f"From Rowanholt job {job} ({cite(host, artefact, ref, digest)}), where it is {said}. "
                f"It's a lead here until it's verified and confirmed in Ostraka.{changed}"
            ),
            status=FindingStatus.LEAD,
            techniques=[technique] if technique else [],
            tags=[],
            created_by=user.id,
        )
        db.add(finding)
        await db.flush()
        db.add(
            RowanholtCitation(
                incident_id=incident.id,
                finding_id=finding.id,
                job_id=job,
                case_number=link.case_number,
                host=host,
                artefact=artefact,
                row_ref=ref,
                row_hash=digest,
                excerpt={"excerpt": str(f.get("excerpt") or "")[:MAX_CELL]} if f.get("excerpt") else {},
                remote_finding_id=remote,
                remote_status=said[:16],
                cited_by=user.id,
                cited_at=utcnow(),
            )
        )
        await activity.record(
            db,
            incident,
            user,
            "finding_added",
            f"{user.name} brought in FND-{finding.seq} from Rowanholt as a lead: {finding.title}",
            entity_type="finding",
            entity_id=finding.id,
            rowanholt=remote,
        )
        imported.append(f"FND-{finding.seq}")
    await activity.finish(db, user)
    return ImportOut(imported=imported, skipped=skipped)


@router.post(
    "/findings/{finding_id}/rowanholt", response_model=CitationOut, status_code=status.HTTP_201_CREATED
)
async def cite_row(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: CitationIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> CitationOut:
    """Cite a Rowanholt row in a finding. Rowanholt is asked for the row, so the citation carries its
    hash and how it read. With `verify`, the row is also the finding's verification, by this person;
    the finding stays as it is until someone confirms it."""
    incident = await access.load(db, user, incident_id, edit=True)
    finding = await db.get(Finding, finding_id)
    if finding is None or finding.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That finding isn't in this incident.")
    client = await _server(db, incident)
    link = await _link(db, incident, body.link_id)
    job = _job_of(link, body.job_id)
    try:
        found = await client.row(job, body.host, body.artefact, body.row_ref)
    except (Unreachable, Refused) as exc:
        raise _failed(exc) from exc
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Rowanholt has no row {body.row_ref} in that table.")
    digest = str(found.get("hash") or "")
    reference = cite(body.host, body.artefact, body.row_ref, digest)
    citation = RowanholtCitation(
        id=uuid.uuid4(),
        incident_id=incident.id,
        finding_id=finding.id,
        job_id=job,
        case_number=link.case_number,
        host=body.host,
        artefact=body.artefact,
        row_ref=body.row_ref,
        row_hash=digest,
        excerpt=_snapshot(found.get("cells", {})),
        remote_status="",
        cited_by=user.id,
        cited_at=utcnow(),
    )
    if body.verify:
        verification = Verification(
            id=uuid.uuid4(),
            finding_id=finding.id,
            method=VerificationMethod.ROWANHOLT,
            platform="Rowanholt",
            scope=f"{body.host}: {body.artefact}"[:500],
            reference=f"Rowanholt job {job} · {reference}"[:500],
            note=body.note.strip(),
            ran_by=user.id,
        )
        db.add(verification)
        citation.verification_id = verification.id
    db.add(citation)
    await activity.record(
        db,
        incident,
        user,
        "verification_added" if body.verify else "rowanholt_cited",
        f"{user.name} {'verified' if body.verify else 'cited'} FND-{finding.seq} with Rowanholt {reference}",
        entity_type="finding",
        entity_id=finding.id,
    )
    await activity.finish(db, user)
    out = await _out(db, user, incident, client.url)
    return next(c for c in out.citations if c.id == citation.id)
