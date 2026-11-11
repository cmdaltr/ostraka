"""What an incident's reports say: the full technical report and the shorter briefing, as blocks for
the branded shell (`branded.render`).

Everything is computed from the record at the moment of export. Nothing is written by a model: the
assessments' sentences come from their structured parts, the ACH figures from plain arithmetic.
The technical report carries indicators defanged; the briefing masks them, as ostraka does for
observers.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.api.ach import ach_out
from ostraka.api.assessments import basis_findings, saved_draft
from ostraka.config import get_settings
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import mask_text
from ostraka.models import (
    FINISHED,
    PHASES_OF,
    ActivityEvent,
    Assessment,
    AssessmentStatus,
    Asset,
    Finding,
    FindingStatus,
    Incident,
    IncidentObservable,
    IncidentRole,
    Lesson,
    LessonKind,
    Phase,
    RecordKind,
    Task,
    User,
    utcnow,
)
from ostraka.reports import attack, charts, custody
from ostraka.reports.branded import SEVERITY, STATUS, Cover
from ostraka.reports.theme import pal
from ostraka.standard import ach as ach_maths
from ostraka.standard import yardstick
from ostraka.standard.assessment import ordered_basis, sentences
from ostraka.updates.drafts import sent_updates


def phase_fills() -> dict[Phase, tuple[int, int, int]]:
    """Each phase's colour in the phase bar, in the report's palette."""
    return {
        Phase.DETECT: pal().ink,
        Phase.SCOPE: pal().muted,
        Phase.CONTAIN: pal().clay,
        Phase.ERADICATE: STATUS["lead"],
        Phase.RECOVER: STATUS["refuted"],
        Phase.LEARN: pal().rule,
    }


def hunt_fills() -> list[tuple[int, int, int]]:
    return [pal().ink, pal().muted, pal().clay, STATUS["lead"]]


STATUS_WORDS = {
    FindingStatus.CONFIRMED: "Confirmed",
    FindingStatus.LEAD: "Lead",
    FindingStatus.REFUTED: "Refuted",
}
RELIABILITY = {
    "A": "Completely reliable",
    "B": "Usually reliable",
    "C": "Fairly reliable",
    "D": "Not usually reliable",
    "E": "Unreliable",
    "F": "Reliability can't be judged",
}
CREDIBILITY = {
    "1": "Confirmed by other sources",
    "2": "Probably true",
    "3": "Possibly true",
    "4": "Doubtful",
    "5": "Improbable",
    "6": "Truth can't be judged",
}
RATING_WORDS = {"c": "C", "i": "I", "na": "N/A"}
WEIGHT_WORDS = {"h": "High", "m": "Medium", "l": "Low"}


@dataclass
class Record:
    """Everything a report draws on, read once."""

    incident: Incident
    exported_by: User
    names: dict[uuid.UUID, str]
    findings: list[Finding]
    assets: list[Asset]
    observables: list[IncidentObservable]
    tasks: list[Task]
    assessments: list[tuple[Assessment, list[Finding]]]
    ach: object  # AchOut
    lessons: list[Lesson]
    timeline: list
    phases: list[tuple[Phase, float]]  # hours in each phase, in the order entered
    contained_after: float | None  # hours from declaration to containment
    chain_ok: bool
    chain_events: int
    chain_head: str
    exported_at: datetime = field(default_factory=utcnow)
    exhibits: list = field(default_factory=list)  # (EvidenceItem, [citing finding refs])
    # Stakeholder updates sent, in order: (UPD-7, number, approved at, approved by, texts, AI-worded)
    updates: list[tuple] = field(default_factory=list)

    def name(self, user_id: uuid.UUID | None) -> str:
        return self.names.get(user_id, "Former member") if user_id else "—"

    @property
    def hunt(self) -> bool:
        return self.incident.kind is RecordKind.HUNT

    @property
    def commander(self) -> str:
        cid = access.commander_id(self.incident)
        return self.name(cid) if cid else "None named"


async def gather(db: AsyncSession, incident: Incident, viewer: User) -> Record:
    """Read the incident as it stands, as `viewer` sees it."""
    from ostraka.api.incidents import timeline  # the router module imports this one's dependencies

    of = incident.id
    findings = list(
        (await db.scalars(select(Finding).where(Finding.incident_id == of).order_by(Finding.seq))).all()
    )
    assets = list((await db.scalars(select(Asset).where(Asset.incident_id == of).order_by(Asset.seq))).all())
    observables = list(
        (await db.scalars(select(IncidentObservable).where(IncidentObservable.incident_id == of))).all()
    )
    tasks = list((await db.scalars(select(Task).where(Task.incident_id == of).order_by(Task.seq))).all())
    assessments = []
    for a in (
        await db.scalars(select(Assessment).where(Assessment.incident_id == of).order_by(Assessment.seq))
    ).all():
        assessments.append((a, await basis_findings(db, a)))
    lessons = list(
        (await db.scalars(select(Lesson).where(Lesson.incident_id == of).order_by(Lesson.seq))).all()
    )

    people = [
        *(m.user_id for m in incident.members),
        *(f.created_by for f in findings),
        *(f.decided_by for f in findings),
        *(v.ran_by for f in findings for v in f.verifications),
        *(t.assignee_id for t in tasks),
        *(a.created_by for a, _ in assessments),
        *(lesson.created_by for lesson in lessons),
    ]
    phases, contained = await _phases(db, incident)
    check = await activity.verify(db, of)
    head = await db.scalar(
        select(ActivityEvent.hash)
        .where(ActivityEvent.incident_id == of)
        .order_by(ActivityEvent.position.desc())
    )
    return Record(
        incident=incident,
        exported_by=viewer,
        names=await views.names(db, people),
        findings=findings,
        assets=assets,
        observables=observables,
        tasks=tasks,
        assessments=assessments,
        ach=await ach_out(db, viewer, of),
        lessons=lessons,
        timeline=await timeline(of, viewer, db),
        phases=phases,
        contained_after=contained,
        chain_ok=check.ok,
        chain_events=check.events,
        chain_head=head or activity.GENESIS,
        exhibits=await custody.gather(db, of),
        updates=await sent_updates(db, of),
    )


async def _phases(db: AsyncSession, incident: Incident) -> tuple[list[tuple[Phase, float]], float | None]:
    """Hours spent in each phase, from the activity log's phase changes, and hours to containment."""
    moves = [incident.declared_at]
    entered = [PHASES_OF[incident.kind][0]]
    for event in (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.incident_id == incident.id, ActivityEvent.action == "incident_updated")
            .order_by(ActivityEvent.position)
        )
    ).all():
        phase = (event.data or {}).get("changes", {}).get("phase")
        if phase and phase != entered[-1].value:
            moves.append(event.at)
            entered.append(Phase(phase))
    moves.append(incident.closed_at or utcnow())
    spent: dict[Phase, float] = {}
    for phase, start, end in zip(entered, moves, moves[1:], strict=False):
        spent[phase] = spent.get(phase, 0.0) + max((end - start).total_seconds(), 0) / 3600
    contained = next(
        (
            (at - incident.declared_at).total_seconds() / 3600
            for phase, at in zip(entered, moves, strict=False)
            if phase is Phase.CONTAIN
        ),
        None,
    )
    return list(spent.items()), contained


# ---- wording helpers --------------------------------------------------------------------------------


def _date(moment: datetime | None) -> str:
    return moment.strftime("%d %b %Y %H:%M UTC") if moment else "—"


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment.strftime('%B %Y')}"


def _hours(h: float) -> str:
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} days"


REF = re.compile(r"^[A-Z]{3}-\d+$")
# Kinds that become live links or addresses when pasted; file names and commands stay as written
NETWORK_KINDS = {"ip_address", "cidr", "domain", "url", "email"}


def defang(kind: str, value: str) -> str:
    """A network indicator made safe to paste: dots bracketed, the scheme broken, @ bracketed."""
    if kind not in NETWORK_KINDS:
        return value
    value = re.sub(r"^http", "hxxp", value, flags=re.IGNORECASE).replace("@", "[@]")
    return re.sub(r"(?<=[A-Za-z0-9])\.(?=[A-Za-z0-9])", "[.]", value)


def _tlp(incident: Incident) -> str:
    return f"TLP:{incident.tlp.value.upper()}"


def _cover(record: Record, kind: str) -> Cover:
    incident = record.incident
    lines = [
        f"Commander: {record.commander}",
        f"Status: {incident.status.value}, {incident.phase.value} phase",
        f"Exported by {record.exported_by.name}",
    ]
    return Cover(
        kind=kind,
        title=incident.title,
        reference=incident.ref,
        date=_day(record.exported_at),
        tlp=_tlp(incident),
        prepared_for=get_settings().organisation,
        lines=lines,
    )


def _current(record: Record) -> list[tuple[Assessment, list[Finding]]]:
    return [(a, f) for a, f in record.assessments if a.status is AssessmentStatus.CURRENT]


def _cards(record: Record) -> bytes:
    confirmed = sum(f.status is FindingStatus.CONFIRMED for f in record.findings)
    open_tasks = sum(t.status not in FINISHED for t in record.tasks)
    cards = [
        (record.incident.severity.value.capitalize(), "Severity", SEVERITY[record.incident.severity.value]),
        (str(confirmed), "Confirmed findings", pal().ink),
        (str(len(record.assets)), "Hosts and accounts" if record.hunt else "Assets affected", pal().clay),
    ]
    if record.contained_after is not None:
        cards.append((_hours(record.contained_after), "To containment", pal().band))
    else:
        cards.append((str(open_tasks), "Tasks open", pal().band))
    return charts.headline_cards(cards)


def _bluf(record: Record, mask) -> list[tuple]:
    current = _current(record)
    if not current:
        return [
            (
                "para",
                "No assessment has been made yet. What follows is the record as it stands; it carries no "
                "judgement of what it means.",
            )
        ]
    return [("para", mask(sentences(saved_draft(a, f))[0])) for a, f in current]


def _incident_table(record: Record, mask, *, brief: bool = False) -> tuple:
    incident = record.incident
    roles = [
        f"{record.name(m.user_id)} ({m.role.value})"
        for m in incident.members
        if m.role is not IncidentRole.RESPONDER
    ]
    rows = [
        ["Reference", incident.ref],
        ["Title", mask(incident.title)],
        ["Severity", incident.severity.value.capitalize()],
        ["TLP", _tlp(incident)],
        ["Status", f"{incident.status.value.capitalize()}, {incident.phase.value} phase"],
        ["Declared", _date(incident.declared_at)],
        ["Aware since", _date(incident.aware_at)],
        ["Closed", _date(incident.closed_at)],
        ["Commander", record.commander],
        ["Teams", ", ".join(t.team.name for t in incident.teams) or "—"],
    ]
    if not brief:
        rows.append(["Roles", ", ".join(roles) or "—"])
        rows.append(["Responders", str(sum(m.role is IncidentRole.RESPONDER for m in incident.members))])
    fills = {(2, 1): SEVERITY[incident.severity.value]}  # the severity row
    return ("table", (["Item", "Detail"], rows, [4.5, 12.5], fills), "The incident.", True)


# ---- the technical report ---------------------------------------------------------------------------


def technical(record: Record) -> tuple[Cover, list[tuple]]:
    """The full record: bottom line, the incident, the attack, findings, assessment and ACH, assets,
    indicators, the response, lessons, and the appendices the standard asks for."""
    incident = record.incident
    keep = lambda text: text  # noqa: E731 (the technical report shows what responders see)
    blocks: list[tuple] = []

    blocks += [("section", "Bottom Line Up Front"), *_bluf(record, keep)]
    blocks.append(("figure", _cards(record), "The incident in four numbers.", 17.0))
    if incident.summary.strip():
        blocks += [("sub", "Summary"), ("para", incident.summary.strip())]

    blocks += [("section", "The Hunt" if record.hunt else "The Incident"), _incident_table(record, keep)]
    if record.phases:
        fills = hunt_fills() if record.hunt else None
        bars = [
            (p.value, h, fills[i % len(fills)] if fills else phase_fills()[p])
            for i, (p, h) in enumerate(record.phases)
        ]
        blocks += [
            ("sub", "Time in each phase"),
            ("figure", charts.phase_bar(bars), "Hours in each phase, from the activity log.", 17.0),
        ]

    blocks += [("section", "Assumptions and Limitations"), ("bullets", _assumptions(record))]

    if record.timeline:
        rows = [
            [
                _date(t.at),
                _date(t.recorded_at),
                t.ref if REF.match(t.ref) else "Link",
                t.title,
                t.asset or "—",
            ]
            for t in sorted(record.timeline, key=lambda t: t.at)
        ]
        blocks += [
            ("section", "Timeline of the Attack"),
            (
                "para",
                "What happened, in the order it happened, from sightings, findings and links that carry a "
                "time. "
                "Each row also says when it was recorded, so the two orders can be compared.",
            ),
            (
                "table",
                (["When", "Recorded", "Ref", "What", "Host"], rows, [3.4, 3.4, 2.0, 5.9, 2.3], {}),
                "The attack in time order.",
                False,
            ),
        ]

    blocks += _findings(record)
    blocks += _assessment(record, keep)
    blocks += _ach(record)

    if record.assets:
        rows, fills = [], {}
        for i, a in enumerate(record.assets):
            rows.append(
                [
                    f"AST-{a.seq}",
                    a.hostname or a.ip or "—",
                    a.ip or "—",
                    a.os or "—",
                    a.criticality.value.capitalize(),
                    a.status.value.capitalize(),
                    "Yes" if a.is_patient_zero else "",
                ]
            )
            fills[(i, 4)] = SEVERITY[a.criticality.value]
        blocks += [
            ("section", "Affected Assets"),
            (
                "table",
                (
                    ["Ref", "Host", "IP", "OS", "Criticality", "Status", "Patient zero"],
                    rows,
                    [1.8, 3.4, 2.6, 2.6, 2.2, 2.4, 2.0],
                    fills,
                ),
                "Every asset recorded in the incident.",
                False,
            ),
        ]

    if record.observables:
        rows = []
        for item in sorted(record.observables, key=lambda o: o.observable.seq):
            seen = [s.seen_at for s in item.sightings if s.seen_at]
            rows.append(
                [
                    f"OBS-{item.observable.seq}",
                    item.observable.kind.value.replace("_", " "),
                    defang(item.observable.kind.value, item.observable.value),
                    item.verdict.value.capitalize(),
                    str(len(item.sightings)),
                    _date(min(seen)) if seen else "—",
                ]
            )
        blocks += [
            ("section", "Indicators"),
            (
                "para",
                "Network indicators are defanged: dots in addresses and domains are bracketed and URL "
                "schemes broken, so nothing here is a live link.",
            ),
            (
                "table",
                (
                    ["Ref", "Kind", "Value", "Verdict", "Seen", "First seen"],
                    rows,
                    [1.9, 2.4, 6.4, 2.2, 1.3, 2.8],
                    {},
                ),
                "Indicators recorded in the incident.",
                False,
            ),
        ]

    blocks += custody.blocks(record.exhibits)
    blocks += _response(record)
    blocks += _lessons(record, keep)
    blocks += _conclusions(record)
    blocks += _appendices(record)
    return _cover(record, "Hunt Report" if record.hunt else "Incident Report"), blocks


def _assumptions(record: Record) -> list[str]:
    return [
        f"This is the record as it stood at {_date(record.exported_at)}. Anything recorded since isn't "
        "in it.",
        "A verification covers the host or source it searched and nothing wider. A confirmed finding is "
        "confirmed there, not everywhere.",
        "Leads are reported as leads. Only a finding with a verification attached can be confirmed, and the "
        "person who verified it is named separately from the person who reported it.",
        "“No evidence of” always names the search that looked. It says where nobody found something, not "
        "that it didn't happen.",
        "Likelihood uses the PHIA probability yardstick (Appendix A) and nothing else.",
        "Network indicators are defanged in the Indicators table; queries are quoted exactly, so they "
        "can be run again. Both are real values: handle the report at its TLP.",
        "No AI wrote this report. Every sentence is assembled by Ostraka from the structured record, and "
        "the ACH figures are plain arithmetic set out in Appendix F.",
    ]


def _finding_card(record: Record, f: Finding) -> tuple:
    techniques = ", ".join(f"{t} {attack.technique(t)[0]}".strip() for t in f.techniques) or "—"
    host = next((a.hostname for a in record.assets if a.id == f.asset_id), None)
    rows: list[tuple[str, str, object]] = [
        ("Statement", f.statement.strip() or f.title, None),
        ("Techniques", techniques, None),
        ("Host", host or "—", None),
        ("Occurred", _date(f.occurred_at), None),
        ("Reported by", f"{record.name(f.created_by)}, {_date(f.created_at)}", None),
    ]
    if f.source_reliability or f.information_credibility:
        grade = f"{f.source_reliability or '?'}{f.information_credibility or '?'}"
        source = RELIABILITY.get(f.source_reliability or "", "")
        meaning = f"{source}; {CREDIBILITY.get(f.information_credibility or '', '')}"
        rows.append(("Grading", f"{grade}: {meaning}", None))
    if f.handling_code:
        rows.append(("Handling", f"{f.handling_code} {f.handling_conditions}".strip(), None))
    for v in f.verifications:
        window = f", {_date(v.time_from)} to {_date(v.time_to)}" if v.time_from or v.time_to else ""
        results = f", {v.result_count} results" if v.result_count is not None else ""
        how = (
            f"{v.platform} search of {v.scope}"
            if v.method.value == "search"
            else f"{v.method.value}: {v.reference}"
        )
        rows.append(("Verified", f"{how}{window}{results}, by {record.name(v.ran_by)}", None))
    rows.append(("Confirmed by", f"{record.name(f.decided_by)}, {_date(f.decided_at)}", STATUS["confirmed"]))
    return ("card", f"FND-{f.seq}  {f.title}", STATUS["confirmed"], rows)


def _findings(record: Record) -> list[tuple]:
    if not record.findings:
        return []
    counts = Counter(f.status for f in record.findings)
    blocks: list[tuple] = [
        ("section", "Findings"),
        (
            "para",
            f"{len(record.findings)} findings: {counts[FindingStatus.CONFIRMED]} confirmed, "
            f"{counts[FindingStatus.LEAD]} still leads and {counts[FindingStatus.REFUTED]} refuted. "
            "Leads and "
            "confirmed findings are kept apart throughout.",
        ),
        (
            "figure",
            charts.horizontal_bars(
                [(STATUS_WORDS[s], counts[s], STATUS[s.value]) for s in STATUS_WORDS],
                total=len(record.findings),
            ),
            "Findings by status.",
            15.0,
        ),
    ]
    matrix: dict[str, list[tuple[str, tuple[int, int, int]]]] = {}
    rank = {FindingStatus.CONFIRMED: 0, FindingStatus.LEAD: 1, FindingStatus.REFUTED: 2}
    best: dict[str, FindingStatus] = {}
    for f in record.findings:
        for t in f.techniques:
            if t not in best or rank[f.status] < rank[best[t]]:
                best[t] = f.status
    for t, status in sorted(best.items()):
        for tactic in attack.technique(t)[1]:
            matrix.setdefault(tactic, []).append((t, STATUS[status.value]))
    if matrix:
        blocks += [
            ("sub", "Techniques"),
            (
                "para",
                "Each ATT&CK technique the findings name, under its tactic, coloured by its strongest "
                "finding.",
            ),
            (
                "figure",
                charts.attack_matrix(
                    matrix, legend=[(STATUS_WORDS[s], STATUS[s.value]) for s in STATUS_WORDS]
                ),
                f"The findings' techniques as an ATT&CK matrix ({attack.VERSION or 'ATT&CK'}).",
                17.0,
            ),
        ]
    confirmed = [f for f in record.findings if f.status is FindingStatus.CONFIRMED]
    if confirmed:
        blocks += [("sub", "Confirmed findings"), *(_finding_card(record, f) for f in confirmed)]
    leads = [f for f in record.findings if f.status is FindingStatus.LEAD]
    if leads:
        rows = [
            [f"FND-{f.seq}", f.title, ", ".join(f.techniques) or "—", record.name(f.created_by)]
            for f in leads
        ]
        blocks += [
            ("sub", "Leads, not confirmed"),
            ("para", "Recorded but not verified. None of these is a finding of fact."),
            (
                "table",
                (["Ref", "Lead", "Techniques", "Reported by"], rows, [2.0, 8.5, 3.3, 3.2], {}),
                "Open leads.",
                False,
            ),
        ]
    refuted = [f for f in record.findings if f.status is FindingStatus.REFUTED]
    if refuted:
        rows = [[f"FND-{f.seq}", f.title, f.decision_note or "—", record.name(f.decided_by)] for f in refuted]
        blocks.append(
            (
                "table",
                (["Ref", "Finding", "Why refuted", "By"], rows, [2.0, 6.0, 6.0, 3.0], {}),
                "Refuted findings.",
                False,
            )
        )
    return blocks


def _assessment(record: Record, mask) -> list[tuple]:
    if not record.assessments:
        return []
    blocks: list[tuple] = [("section", "Assessment")]
    for a, findings in _current(record):
        draft = saved_draft(a, findings)
        blocks.append(
            ("sub", f"ASM-{a.seq}: {yardstick.words(a.probability)} ({yardstick.range_of(a.probability)})")
        )
        blocks += [("para", mask(sentence)) for sentence in sentences(draft)]
        if draft.basis:
            rows = [[b.ref, STATUS_WORDS[b.status], mask(b.title)] for b in ordered_basis(draft.basis)]
            fills = {(i, 1): STATUS[b.status.value] for i, b in enumerate(ordered_basis(draft.basis))}
            blocks.append(
                (
                    "table",
                    (["Ref", "Status", "Finding"], rows, [2.2, 2.6, 12.2], fills),
                    f"What ASM-{a.seq} rests on.",
                    False,
                )
            )
        blocks.append(("para", f"Made by {record.name(a.created_by)}, {_date(a.created_at)}."))
    superseded = [(a, f) for a, f in record.assessments if a.status is AssessmentStatus.SUPERSEDED]
    if superseded:
        rows = [
            [
                f"ASM-{a.seq}",
                _date(a.created_at),
                yardstick.words(a.probability),
                mask(a.judgement),
                _date(a.superseded_at),
            ]
            for a, _ in superseded
        ]
        blocks += [
            ("sub", "How the judgement moved"),
            (
                "table",
                (
                    ["Ref", "Made", "Likelihood", "Judgement", "Superseded"],
                    rows,
                    [1.9, 3.3, 2.6, 5.9, 3.3],
                    {},
                ),
                "Earlier assessments, each replaced by a later one.",
                False,
            ),
        ]
    return blocks


def _ach(record: Record) -> list[tuple]:
    matrix = record.ach
    if not matrix.hypotheses:
        return []
    blocks: list[tuple] = [
        ("section", "Analysis of Competing Hypotheses"),
        (
            "para",
            "Each piece of evidence was rated against each hypothesis: consistent (C), inconsistent (I) "
            "or not "
            "applicable (N/A). The hypothesis with the least weighted evidence against it leads; evidence "
            "consistent with everything proves nothing.",
        ),
        (
            "figure",
            charts.ach_scores([(f"{h.ref}: {h.statement}", h.score, h.leading) for h in matrix.hypotheses]),
            "Weighted inconsistency by hypothesis; nearer zero is better supported.",
            17.0,
        ),
    ]
    rows = [
        [
            h.ref,
            h.statement,
            f"{h.score:.2f}",
            "Leading" if h.leading else ("Rejected" if h.status.value == "rejected" else "Open"),
            h.rejection_reason or "—",
        ]
        for h in matrix.hypotheses
    ]
    fills = {(i, 3): pal().clay for i, h in enumerate(matrix.hypotheses) if h.leading}
    blocks.append(
        (
            "table",
            (
                ["Ref", "Hypothesis", "Score", "Standing", "Why rejected"],
                rows,
                [1.8, 6.0, 1.6, 2.2, 5.4],
                fills,
            ),
            "Every hypothesis, including those rejected and why.",
            False,
        )
    )
    if matrix.evidence:
        consensus = {(c.hypothesis_id, c.evidence_id): c for c in matrix.cells}
        headers = ["Evidence", "Cred.", "Rel.", *(h.ref for h in matrix.hypotheses)]
        rows, fills = [], {}
        for i, e in enumerate(matrix.evidence):
            row = [f"{e.ref} {e.text}", WEIGHT_WORDS[e.credibility.value], WEIGHT_WORDS[e.relevance.value]]
            for j, h in enumerate(matrix.hypotheses):
                cell = consensus.get((h.id, e.id))
                value = cell.consensus.value if cell and cell.consensus else None
                row.append(
                    "disputed" if cell and cell.disputed and not value else RATING_WORDS.get(value, "–")
                )
                if value == "i":
                    fills[(i, 3 + j)] = SEVERITY["high"]
            rows.append(row)
        width = 17.0 - 3.0 - 1.6 * len(matrix.hypotheses)
        blocks += [
            ("sub", "The matrix"),
            (
                "table",
                (headers, rows, [max(width, 5.0), 1.5, 1.5, *([1.6] * len(matrix.hypotheses))], fills),
                f"Ratings agreed by {len(matrix.raters)} analyst(s): "
                f"{', '.join(r.name for r in matrix.raters) or 'none'}.",
                False,
            ),
        ]
        notes = [
            f"{e.ref} is rated the same against every open hypothesis, so it can't tell them apart."
            for e in matrix.evidence
            if e.non_diagnostic
        ]
        notes += [
            f"If {e.ref} were wrong, "
            f"{e.lead_without + ' would lead' if e.lead_without else 'no hypothesis would lead outright'}."
            for e in matrix.evidence
            if e.pivotal
        ]
        if notes:
            blocks += [("sub", "Diagnosticity and sensitivity"), ("bullets", notes)]
    milestones = [
        f"{h.ref}: {h.milestone}" + (f" ({h.milestone_task.ref})" if h.milestone_task else "")
        for h in matrix.hypotheses
        if h.milestone
    ]
    if milestones:
        blocks += [("sub", "What would change the assessment"), ("bullets", milestones)]
    return blocks


def _response(record: Record) -> list[tuple]:
    if not record.tasks:
        return []
    rows = [
        [
            t.ref,
            t.title,
            record.name(t.assignee_id) if t.assignee_id else (t.team.name if t.team else "—"),
            t.status.value.replace("_", " ").capitalize(),
            _date(t.completed_at) if t.completed_at else _date(t.due_at) if t.due_at else "—",
        ]
        for t in record.tasks
    ]
    done = sum(t.status in FINISHED for t in record.tasks)
    return [
        ("section", "The Response"),
        ("para", f"{len(record.tasks)} tasks, {done} finished and {len(record.tasks) - done} open."),
        (
            "table",
            (["Ref", "Task", "Owner", "Status", "Done or due"], rows, [1.9, 7.1, 3.0, 2.2, 2.8], {}),
            "Every task in the incident.",
            False,
        ),
    ]


def _lessons(record: Record, mask) -> list[tuple]:
    if not record.lessons:
        return []
    tasks = {t.id: t for t in record.tasks}
    blocks: list[tuple] = [("section", "Lessons Learned")]
    for kind, title in ((LessonKind.IMPROVE, "What we'd change"), (LessonKind.WENT_WELL, "What went well")):
        these = [lesson for lesson in record.lessons if lesson.kind is kind]
        if not these:
            continue
        rows = [
            [
                f"LSN-{lesson.seq}",
                lesson.area.value.capitalize(),
                mask(lesson.observation),
                mask(lesson.recommendation) or "—",
                tasks[lesson.task_id].ref if lesson.task_id in tasks else "—",
            ]
            for lesson in these
        ]
        blocks += [
            ("sub", title),
            (
                "table",
                (
                    ["Ref", "Area", "What happened", "Keep or change", "Task"],
                    rows,
                    [1.9, 3.0, 5.3, 5.1, 1.7],
                    {},
                ),
                f"{title}.",
                False,
            ),
        ]
    return blocks


def _conclusions(record: Record) -> list[tuple]:
    leads = sum(f.status is FindingStatus.LEAD for f in record.findings)
    open_tasks = sum(t.status not in FINISHED for t in record.tasks)
    untasked = sum(lesson.kind is LessonKind.IMPROVE and not lesson.task_id for lesson in record.lessons)
    lines = []
    if current := _current(record):
        lines.append(f"{len(current)} current assessment(s), set out in full above.")
    if leads:
        lines.append(f"{leads} lead(s) remain unconfirmed and are not findings of fact.")
    if open_tasks:
        lines.append(f"{open_tasks} task(s) are still open.")
    if untasked:
        lines.append(f"{untasked} improvement(s) have no follow-up task yet.")
    if record.ach.leading:
        lines.append(f"In the ACH matrix, {record.ach.leading} leads.")
    if not lines:
        lines.append("Nothing is outstanding on the record.")
    return [("section", "Conclusions"), ("bullets", lines)]


def _appendices(record: Record) -> list[tuple]:
    blocks: list[tuple] = [
        ("section", "Appendices"),
        ("sub", "Appendix A: The PHIA probability yardstick"),
        (
            "table",
            (
                ["Term", "Probability"],
                [[t.capitalize(), r] for t, r in yardstick.TERMS.values()],
                [8.5, 8.5],
                {},
            ),
            "The only terms this report uses for likelihood (College of Policing, Intelligence "
            "management APP).",
            True,
        ),
        ("sub", "Appendix B: Grading and handling"),
        (
            "table",
            (
                ["Code", "Source reliability", "Code", "Information credibility"],
                [
                    [k, v, c, CREDIBILITY[c]]
                    for (k, v), c in zip(RELIABILITY.items(), CREDIBILITY, strict=True)
                ],
                [1.5, 7.0, 1.5, 7.0],
                {},
            ),
            "Admiralty grading. Handling codes: P, lawful sharing allowed; C, conditions apply (stated).",
            False,
        ),
    ]
    searches = [
        (f, v) for f in record.findings for v in f.verifications if v.query.strip() or v.reference.strip()
    ]
    if searches:
        blocks.append(("sub", "Appendix C: Verifications"))
        for f, v in searches:
            blocks.append(
                (
                    "para",
                    f"FND-{f.seq}: {v.platform or v.method.value} over {v.scope}, by {record.name(v.ran_by)}",
                )
            )
            blocks.append(("code", v.query or v.reference))
    searched: dict[tuple[str, str, str, str], list[str]] = {}
    for a, _ in record.assessments:
        for x in a.absences:
            searched.setdefault((x.absent, x.platform, x.scope, x.query), []).append(f"ASM-{a.seq}")
    if searched:
        blocks.append(("sub", "Appendix D: Searches that found nothing"))
        for (absent, platform, scope, query), cited in searched.items():
            blocks.append(
                ("para", f"No evidence of {absent} ({platform}, {scope}); cited by {', '.join(cited)}.")
            )
            blocks.append(("code", query))
    blocks += [
        ("sub", "Appendix E: Integrity of the activity log"),
        (
            "para",
            f"Every action in {record.incident.ref} is an event in a hash chain: each event's SHA-256 "
            "covers the "
            f"one before it. At export the chain held {record.chain_events} events and "
            + ("verified intact." if record.chain_ok else "did NOT verify: treat this record with caution.")
            + " The head of the chain was:",
        ),
        ("code", record.chain_head),
        ("sub", "Appendix F: How ACH is scored"),
        ("para", ach_maths.ALGORITHM),
        (
            "note",
            [
                "No AI was used to write this report. Ostraka assembles every sentence from the structured "
                "record; the reporting standard (verified confirmation, defensive wording, the yardstick, "
                "ACH) is enforced by "
                "fixed rules.",
            ],
        ),
    ]
    return blocks


# ---- the briefing -----------------------------------------------------------------------------------


def _updates(record: Record, mask) -> list[tuple]:
    """Every stakeholder update as it was sent, oldest first. An AI-worded one is marked †."""
    if not record.updates:
        return []
    blocks: list[tuple] = [("section", "Updates Sent")]
    for ref, number, at, by, texts, ai_worded in record.updates:
        mark = " †" if ai_worded else ""
        blocks.append(("sub", f"Update {number} ({ref}), {_date(at)}, approved by {by}{mark}"))
        blocks += [("para", mask(t)) for t in texts]
    if any(u[5] for u in record.updates):
        blocks.append(
            ("note", ["† Worded with AI help from Ostraka's draft, and checked against it before approval."])
        )
    return blocks


def briefing(record: Record) -> tuple[Cover, list[tuple]]:
    """For stakeholders: the bottom line, the incident in brief, what we know, what's being done and
    what we've learned. Indicators are masked, as ostraka does for observers."""
    mask = mask_text
    confirmed = [f for f in record.findings if f.status is FindingStatus.CONFIRMED]
    leads = sum(f.status is FindingStatus.LEAD for f in record.findings)
    blocks: list[tuple] = [("section", "Bottom Line Up Front"), *_bluf(record, mask)]
    blocks.append(("figure", _cards(record), "The incident in four numbers.", 17.0))
    if record.incident.summary.strip():
        blocks.append(("para", mask(record.incident.summary.strip())))

    blocks += [("section", "The Incident"), _incident_table(record, mask, brief=True)]

    blocks.append(("section", "What We Know"))
    if confirmed:
        rows = [[f"FND-{f.seq}", mask(f.title), _date(f.decided_at)] for f in confirmed]
        blocks.append(
            (
                "table",
                (["Ref", "Confirmed finding", "Confirmed"], rows, [2.0, 11.3, 3.7], {}),
                "Confirmed findings.",
                False,
            )
        )
    else:
        blocks.append(("para", "Nothing is confirmed yet."))
    if leads:
        blocks.append(
            ("para", f"{leads} further lead(s) are being checked and are not yet findings of fact.")
        )
    for a, findings in _current(record):
        blocks += [("sub", f"ASM-{a.seq}"), *(("para", mask(s)) for s in sentences(saved_draft(a, findings)))]

    open_tasks = [t for t in record.tasks if t.status not in FINISHED]
    blocks.append(("section", "What's Being Done"))
    blocks.append(
        ("para", f"{len(record.tasks) - len(open_tasks)} of {len(record.tasks)} tasks are finished.")
    )
    if open_tasks:
        rows = [
            [t.ref, mask(t.title), t.team.name if t.team else record.name(t.assignee_id), _date(t.due_at)]
            for t in open_tasks
        ]
        blocks.append(
            (
                "table",
                (["Ref", "Open task", "Owner", "Due"], rows, [1.9, 9.3, 2.8, 3.0], {}),
                "Work still open.",
                False,
            )
        )

    blocks += _updates(record, mask)

    improve = [lesson for lesson in record.lessons if lesson.kind is LessonKind.IMPROVE]
    if improve:
        blocks += [
            ("section", "What We'll Change"),
            ("bullets", [mask(lesson.recommendation or lesson.observation) for lesson in improve]),
        ]
    blocks += [
        ("section", "Appendix: The PHIA probability yardstick"),
        (
            "table",
            (
                ["Term", "Probability"],
                [[t.capitalize(), r] for t, r in yardstick.TERMS.values()],
                [8.5, 8.5],
                {},
            ),
            "The only terms used for likelihood.",
            True,
        ),
        (
            "note",
            [
                "Indicators are masked in this briefing. No AI was used to write it: Ostraka assembles every "
                "sentence from the incident's structured record."
            ],
        ),
    ]
    cover = _cover(record, "Hunt Briefing" if record.hunt else "Incident Briefing")
    cover.compact = True
    return cover, blocks
