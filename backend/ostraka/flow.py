"""The attack flow: how the attack in a record unfolded, what it's likely to have done that nobody has
looked at yet, and how far the team has got in answering each stage.

A stage groups ATT&CK tactics in plain words (Gets in, Runs, Takes credentials…). The flow is built per
record, never from a template, in three layers:

- **Seen:** stages the record's own findings and movement show, each with this incident's techniques.
- **Expected:** stages it hasn't reached that the attack is likely to have, from ATT&CK: the groups whose
  known techniques overlap this record's, and what most of them go on to do. A record attributed to a
  group uses that group's techniques instead. Every expectation says why.
- **Analyst:** a stage someone expects that ATT&CK didn't suggest, or one they've ruled out, with a reason.

Each stage is then covered (answered: a confirmed finding there and no open task for it, or checked and
nothing found, naming where), focused (a lead or open work there) or not yet looked at. Covering every
stage is a measure of thoroughness, never a condition for closing the record. `build()` is a pure function
of a snapshot (`Facts`); nothing here is AI.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.models import (
    FINISHED,
    Asset,
    AssetLink,
    Finding,
    FindingStatus,
    FlowStage,
    Incident,
    LinkKind,
    StageCall,
    Task,
)
from ostraka.reports import attack


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    says: str  # what the attacker does here, in a few words
    tactics: tuple[str, ...]


STAGES: tuple[Stage, ...] = (
    Stage("prepares", "Prepares", "Scouts the target and readies infrastructure",
          ("reconnaissance", "resource-development")),
    Stage("gets_in", "Gets in", "First foothold", ("initial-access",)),
    Stage("runs", "Runs", "Executes its tools", ("execution",)),
    Stage("stays", "Stays and rises", "Keeps access and gains privilege",
          ("persistence", "privilege-escalation")),
    Stage("hides", "Hides", "Evades and impairs defences",
          ("stealth", "defense-impairment", "defense-evasion")),
    Stage("takes_credentials", "Takes credentials", "Steals logins and secrets", ("credential-access",)),
    Stage("moves", "Looks around and moves", "Discovers and moves laterally",
          ("discovery", "lateral-movement")),
    Stage("talks_home", "Talks home", "Command and control", ("command-and-control",)),
    Stage("takes", "Collects and takes", "Gathers and exfiltrates data", ("collection", "exfiltration")),
    Stage("impact", "Does damage", "Encrypts, destroys or disrupts", ("impact",)),
)  # fmt: skip
BY_KEY = {s.key: s for s in STAGES}
_OF_TACTIC = {t: s.key for s in STAGES for t in s.tactics}
LINK_STAGE = {
    LinkKind.LATERAL_MOVEMENT: "moves",
    LinkKind.C2: "talks_home",
    LinkKind.EXFILTRATION: "takes",
}

PEERS = 25  # the groups most like this record that expectations are drawn from
EXPECT_SHARE = 0.5  # a stage is expected when at least this share of those groups reach it
SUGGEST = 5  # techniques suggested per stage


def stages_of(technique: str) -> list[str]:
    """The stages a technique belongs to, by its tactics."""
    _, tactics = attack.technique(technique)
    found = {_OF_TACTIC[t] for t in tactics if t in _OF_TACTIC}
    return [s.key for s in STAGES if s.key in found]


def base(technique: str) -> str:
    return technique.strip().upper().split(".")[0]


# ---- the snapshot -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingFact:
    ref: str
    title: str
    status: FindingStatus
    techniques: tuple[str, ...]


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
    group: str | None = None  # attributed ATT&CK group, G0016


# ---- the flow -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    technique: str
    name: str
    groups: int  # how many of the peer groups use it


@dataclass
class StageOut:
    key: str
    name: str
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


def build(facts: Facts) -> Flow:
    """The flow for a record, stage by stage in ATT&CK's order."""
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
    links_in: dict[str, list[LinkFact]] = {}
    for link in facts.links:
        links_in.setdefault(link.stage, []).append(link)
        if link.technique:
            seen_techniques.setdefault(link.stage, set()).add(link.technique.upper())
    every_seen = {t for ts in seen_techniques.values() for t in ts}
    seen_base = {base(t) for t in every_seen}

    peers, group = _peers(every_seen, facts.group)
    # how many peers reach each stage, and with which techniques this record hasn't shown
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

    shown = ", ".join(sorted(every_seen)[:4]) + ("…" if len(every_seen) > 4 else "")
    out: list[StageOut] = []
    dismissed: list[tuple[Stage, CallFact]] = []
    for stage in STAGES:
        call = calls.get(stage.key)
        if call is not None and call.call is StageCall.DISMISSED:
            dismissed.append((stage, call))
            continue
        if stage.key in seen_in or stage.key in links_in:
            layer, why = "seen", ""
        elif group is not None and reach[stage.key]:
            layer, why = "expected", f"{group[1]} ({group[0]}) is known to reach this stage."
        elif peers and reach[stage.key] / len(peers) >= EXPECT_SHARE and stage.key != "prepares":
            layer = "expected"
            why = (
                f"{reach[stage.key]} of the {len(peers)} ATT&CK groups most like this record "
                f"(sharing {shown}) reach this stage."
            )
        elif call is not None:
            layer, why = "added", ""
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
        elif lead or open_tasks or confirmed or links_in.get(stage.key):
            coverage = "focused"
        else:
            coverage = "open"
        out.append(
            StageOut(
                key=stage.key,
                name=stage.name,
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
    return Facts(
        findings=[
            FindingFact(f"FND-{f.seq}", f.title, f.status, tuple(f.techniques or ())) for f in findings
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
        group=incident.attributed_group,
    )
