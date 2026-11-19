"""The attack flow: how the attack in a record unfolded, what it's likely to have done that nobody has
looked at yet, and how far the team has got in answering each step.

The steps are the attack's anatomy in plain words (`ostraka/anatomy.py`, Shikari's): the way in (Exploit,
Phishing, Credentials), the Toolkit, the Actions once in (Steals logins, Keeps access…) and where it goes
(Cloud, Messaging, Code and storage, Infrastructure). The flow is built per record, never from a template,
in three layers:

- **Seen:** steps the record shows: its findings' techniques, the CVEs, tools and services they name, its
  movement between assets and its malicious addresses.
- **Expected:** steps it hasn't shown that the attack is likely to have, from ATT&CK: the groups whose
  known techniques overlap this record's, and what most of them go on to do. A record attributed to a
  group uses that group's techniques instead. Every expectation says why.
- **Analyst:** a step someone expects that ATT&CK didn't suggest, or one they've ruled out, with a reason.

Each step is then covered (answered: a confirmed finding there and no open task for it, or checked and
nothing found, naming where), focused (a lead or open work there) or not yet looked at. Covering every
step is a measure of thoroughness, never a condition for closing the record. `build()` is a pure function
of a snapshot (`Facts`); nothing here is AI.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import anatomy
from ostraka.models import (
    FINISHED,
    Asset,
    AssetLink,
    Finding,
    FindingStatus,
    FlowStage,
    Incident,
    IncidentObservable,
    LinkKind,
    StageCall,
    Task,
    Verdict,
)
from ostraka.observables.detect import ObservableType
from ostraka.reports import attack


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    column: str  # way, toolkit, action or service: where it's drawn
    verb: str  # the step into it, on the line that reaches it ("Phishes")
    says: str  # what the attacker does here, in a few words
    prefixes: tuple[str, ...]  # techniques placed here, with their sub-techniques


STAGES: tuple[Stage, ...] = (
    *(Stage(k, n, "way", verb, says, p) for k, n, verb, p, says in anatomy.WAYS_IN),
    Stage("toolkit", "Toolkit", "toolkit", "Runs", "What it runs: frameworks, stealers, web shells, RATs",
          anatomy.TOOLKIT),
    *(Stage(k, n, "action", "To", "Something it does once in", p) for k, n, p in anatomy.ACTIONS),
    *(Stage(k, n, "service", verb, ", ".join(g) or "Its own addresses: C2 and staging", p)
      for k, n, verb, g, p in anatomy.SERVICES),
)  # fmt: skip
BY_KEY = {s.key: s for s in STAGES}
_SERVICE_OF_GROUP = {g: k for k, _, _, groups, _ in anatomy.SERVICES for g in groups}
LINK_STAGE = {
    LinkKind.LATERAL_MOVEMENT: "moves",
    LinkKind.C2: "infrastructure",
    LinkKind.EXFILTRATION: "takes_out",
}
_EXFIL = BY_KEY["takes_out"].prefixes

PEERS = 25  # the groups most like this record that expectations are drawn from
EXPECT_SHARE = 0.5  # a stage is expected when at least this share of those groups reach it
SUGGEST = 5  # techniques suggested per stage


def stages_of(technique: str) -> list[str]:
    """The steps a technique belongs to, in the anatomy's order (T1021.007 is a way in and a move)."""
    t = technique.strip().upper()
    return [s.key for s in STAGES if anatomy.has([t], s.prefixes)]


def base(technique: str) -> str:
    return technique.strip().upper().split(".")[0]


# ---- the snapshot -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingFact:
    ref: str
    title: str
    status: FindingStatus
    techniques: tuple[str, ...]
    text: str = ""  # its statement: the CVEs, tools and services it names


@dataclass(frozen=True)
class ObservableFact:
    kind: ObservableType
    value: str


@dataclass(frozen=True)
class LinkFact:
    stage: str
    source: str  # asset names
    target: str
    technique: str = ""


@dataclass(frozen=True)
class TaskFact:
    ref: str
    title: str
    stage: str
    done: bool


@dataclass(frozen=True)
class CallFact:
    stage: str
    call: StageCall
    note: str
    by: str
    at: datetime


@dataclass
class Facts:
    findings: list[FindingFact] = field(default_factory=list)
    links: list[LinkFact] = field(default_factory=list)
    tasks: list[TaskFact] = field(default_factory=list)
    calls: list[CallFact] = field(default_factory=list)
    observables: list[ObservableFact] = field(default_factory=list)  # malicious or suspicious only
    group: str | None = None  # attributed ATT&CK group, G0016


# ---- the flow -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    technique: str
    name: str
    groups: int  # how many of the peer groups use it


# Why a step nothing else expects is drawn: a playbook (or someone) put tasks on it
TASKS_WHY = "Tasks on this record check for it."


@dataclass
class StageOut:
    key: str
    name: str
    column: str
    verb: str
    says: str
    layer: str  # seen, expected (from ATT&CK) or added (by an analyst)
    coverage: str  # covered, focused or open
    findings: list[FindingFact] = field(default_factory=list)
    links: list[LinkFact] = field(default_factory=list)
    techniques: list[tuple[str, str]] = field(default_factory=list)  # seen here: (T1566.001, name)
    suggestions: list[Suggestion] = field(default_factory=list)  # still to check here
    why: str = ""  # why it's expected
    tasks_open: list[TaskFact] = field(default_factory=list)
    tasks_done: int = 0
    call: CallFact | None = None  # cleared or expected, by whom and why
    items: list[str] = field(default_factory=list)  # what's named there: CVEs, tools, services


@dataclass
class Flow:
    stages: list[StageOut]
    dismissed: list[tuple[Stage, CallFact]]
    peers: int  # groups the expectations were drawn from
    group: tuple[str, str] | None  # (G0016, APT29)
    seen_techniques: list[str]

    @property
    def covered(self) -> int:
        return sum(1 for s in self.stages if s.coverage == "covered")


def _peers(seen: set[str], group: str | None) -> tuple[list[frozenset[str]], tuple[str, str] | None]:
    """The groups expectations come from: the attributed one alone, or the ones whose techniques overlap
    this record's most (compared at the parent technique, so T1566.001 matches a group's T1566.002)."""
    known = attack.groups()
    if group and group in known:
        name, techniques = known[group]
        return [techniques], (group, name)
    if not seen:
        return [], None
    seen_base = {base(t) for t in seen}
    scored = []
    for _gid, (_name, techniques) in known.items():
        overlap = len(seen_base & {base(t) for t in techniques})
        if overlap:
            scored.append((overlap, techniques))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:PEERS]], None


def _names(
    facts: Facts, live: list[FindingFact], seen_in: dict[str, list[FindingFact]]
) -> dict[str, list[str]]:
    """What each step names, as Shikari shows it: the CVEs exploited, the services phished through or
    signed in to, the tools run, and the services reached. Adds the steps they show to `seen_in`."""
    items: dict[str, list[str]] = {}

    def put(stage: str, item: str, finding: FindingFact | None = None) -> None:
        if item not in items.setdefault(stage, []):
            items[stage].append(item)
        if finding is not None and finding not in seen_in.setdefault(stage, []):
            seen_in[stage].append(finding)

    for f in live:
        text = f"{f.title}\n{f.text}"
        way = next((k for k, *_ in anatomy.WAYS_IN if anatomy.has(f.techniques, BY_KEY[k].prefixes)), None)
        for cve in anatomy.cves_in(text):
            put("exploit", cve, f)
        for group, name in anatomy.tools_in(text):
            put("toolkit", f"{group}: {anatomy.tool_name(group, name)}", f)
        for group, name in anatomy.services_in(text):
            if way in ("phishing", "credentials"):
                put(way, name, f)
            elif group in anatomy.TOOLKIT_SERVICES:
                if not any(i.endswith(f": {name}") for i in items.get("toolkit", [])):
                    put("toolkit", f"Remote access: {name}", f)
            else:
                put(_SERVICE_OF_GROUP[group], name, f)
    addresses = 0
    for o in facts.observables:
        named = anatomy.services_at(o.value) if o.kind in (ObservableType.DOMAIN, ObservableType.URL) else []
        for group, name in named:
            if group not in anatomy.TOOLKIT_SERVICES:
                put(_SERVICE_OF_GROUP[group], name)
        if not named and o.kind in (ObservableType.IP_ADDRESS, ObservableType.DOMAIN, ObservableType.URL):
            addresses += 1
    if addresses:
        items.setdefault("infrastructure", []).insert(
            0, f"{addresses} address{'es' if addresses > 1 else ''}"
        )
    return items


def build(facts: Facts) -> Flow:
    """The flow for a record, step by step in the anatomy's order."""
    calls = {c.stage: c for c in facts.calls}
    live = [f for f in facts.findings if f.status is not FindingStatus.REFUTED]

    seen_in: dict[str, list[FindingFact]] = {}
    seen_techniques: dict[str, set[str]] = {}
    for f in live:
        for t in f.techniques:
            for s in stages_of(t):
                if f not in seen_in.setdefault(s, []):
                    seen_in[s].append(f)
                seen_techniques.setdefault(s, set()).add(t.upper())
    items = _names(facts, live, seen_in)
    links_in: dict[str, list[LinkFact]] = {}
    for link in facts.links:
        links_in.setdefault(link.stage, []).append(link)
        if link.technique:
            seen_techniques.setdefault(link.stage, set()).add(link.technique.upper())
    every_seen = {t for ts in seen_techniques.values() for t in ts}
    seen_base = {base(t) for t in every_seen}

    peers, group = _peers(every_seen, facts.group)
    # how many peers reach each step, and with which techniques this record hasn't shown
    reach: Counter[str] = Counter()
    unseen: dict[str, Counter[str]] = {}
    for techniques in peers:
        stages_here: set[str] = set()
        for t in techniques:
            for s in stages_of(t):
                stages_here.add(s)
                if base(t) not in seen_base:
                    unseen.setdefault(s, Counter())[t] += 1
        reach.update(stages_here)

    def suggestions(stage: str) -> list[Suggestion]:
        ranked = sorted(unseen.get(stage, Counter()).items(), key=lambda x: (-x[1], x[0]))[:SUGGEST]
        return [Suggestion(t, attack.technique(t)[0], n) for t, n in ranked]

    # the ways in are alternatives: once the record shows how they got in, the others aren't expected
    got_in = any(k in seen_in or k in items for k, *_ in anatomy.WAYS_IN)
    shown = ", ".join(sorted(every_seen)[:4]) + ("…" if len(every_seen) > 4 else "")
    out: list[StageOut] = []
    dismissed: list[tuple[Stage, CallFact]] = []
    for stage in STAGES:
        call = calls.get(stage.key)
        if call is not None and call.call is StageCall.DISMISSED:
            dismissed.append((stage, call))
            continue
        checked = any(t.stage == stage.key for t in facts.tasks)
        if stage.key in seen_in or stage.key in links_in or stage.key in items:
            layer, why = "seen", ""
        elif stage.column == "way" and got_in:
            if call is None and not checked:
                continue
            layer, why = ("added", "") if call is not None else ("expected", TASKS_WHY)
        elif group is not None and reach[stage.key]:
            layer, why = "expected", f"{group[1]} ({group[0]}) is known to do this."
        elif peers and reach[stage.key] / len(peers) >= EXPECT_SHARE:
            layer = "expected"
            why = (
                f"{reach[stage.key]} of the {len(peers)} ATT&CK groups most like this record "
                f"(sharing {shown}) are known to do this."
            )
        elif call is not None:
            layer, why = "added", ""
        elif checked:
            layer, why = "expected", TASKS_WHY
        else:
            continue
        tasks = [t for t in facts.tasks if t.stage == stage.key]
        open_tasks = [t for t in tasks if not t.done]
        findings = seen_in.get(stage.key, [])
        confirmed = any(f.status is FindingStatus.CONFIRMED for f in findings)
        lead = any(f.status is FindingStatus.LEAD for f in findings)
        if (call is not None and call.call is StageCall.CLEARED and not open_tasks) or (
            confirmed and not open_tasks
        ):
            coverage = "covered"
        elif lead or open_tasks or confirmed or links_in.get(stage.key) or items.get(stage.key):
            coverage = "focused"
        else:
            coverage = "open"
        verb = stage.verb
        if stage.key == "storage" and (seen_in.get("takes_out") or links_in.get("takes_out")):
            verb = "Uploads to"
        out.append(
            StageOut(
                key=stage.key,
                name=stage.name,
                column=stage.column,
                verb=verb,
                says=stage.says,
                layer=layer,
                coverage=coverage,
                findings=findings,
                links=links_in.get(stage.key, []),
                techniques=[
                    (t, attack.technique(t)[0]) for t in sorted(seen_techniques.get(stage.key, set()))
                ],
                suggestions=suggestions(stage.key),
                why=why,
                tasks_open=open_tasks,
                tasks_done=len(tasks) - len(open_tasks),
                call=call if call is not None and call.call is not StageCall.DISMISSED else None,
                items=items.get(stage.key, []),
            )
        )
    return Flow(out, dismissed, len(peers), group, sorted(every_seen))


# ---- reading the record -------------------------------------------------------------------------------


async def gather(db: AsyncSession, incident: Incident, names: dict[uuid.UUID, str]) -> Facts:
    """The snapshot from the database. `names` turns the deciders' ids into names."""
    findings = (
        await db.scalars(select(Finding).where(Finding.incident_id == incident.id).order_by(Finding.seq))
    ).all()
    links = (await db.scalars(select(AssetLink).where(AssetLink.incident_id == incident.id))).all()
    hosts = dict(
        (await db.execute(select(Asset.id, Asset.hostname).where(Asset.incident_id == incident.id)))
        .tuples()
        .all()
    )
    tasks = (
        await db.scalars(select(Task).where(Task.incident_id == incident.id, Task.stage.is_not(None)))
    ).all()
    calls = (await db.scalars(select(FlowStage).where(FlowStage.incident_id == incident.id))).all()
    observables = (
        await db.scalars(
            select(IncidentObservable).where(
                IncidentObservable.incident_id == incident.id,
                IncidentObservable.verdict.in_((Verdict.MALICIOUS, Verdict.SUSPICIOUS)),
            )
        )
    ).all()
    return Facts(
        findings=[
            FindingFact(f"FND-{f.seq}", f.title, f.status, tuple(f.techniques or ()), f.statement or "")
            for f in findings
        ],
        links=[
            LinkFact(
                stage=LINK_STAGE[link.kind],
                source=hosts.get(link.source_id, ""),
                target=hosts.get(link.target_id, ""),
                technique=link.technique,
            )
            for link in links
            if link.kind in LINK_STAGE
        ],
        tasks=[TaskFact(t.ref, t.title, t.stage or "", t.status in FINISHED) for t in tasks],
        calls=[CallFact(c.stage, c.call, c.note, names.get(c.by, "") if c.by else "", c.at) for c in calls],
        observables=[ObservableFact(o.observable.kind, o.observable.value) for o in observables],
        group=incident.attributed_group,
    )
