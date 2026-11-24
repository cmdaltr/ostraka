"""The executive summary: four sentences for a senior reader. What the record is, what's been confirmed
(two sentences), and the conclusion.

`draft()` writes it by fixed rules from a snapshot (`Facts`), with AI switched off. A model may write it
instead, reading the whole record: the summary, every finding with its status, the current assessment,
assets, lessons, the discussion and recent activity, indicators masked. `problems()` checks what comes
back without a model, and anything that fails is set aside for Ostraka's own draft: exactly four
sentences, the record's reference first, the confirmed findings cited, no lead or refuted finding called
confirmed, no reference the record doesn't hold, no certainty or loaded words, and no likelihood words
except the current assessment's yardstick term. The discussion is read for context; nothing said in it
counts as confirmed. The TLP guard is the stakeholder updates' (`updates.ai.provider_for`).

What's saved is always masked: an executive summary travels further than the record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.api.assessments import basis_findings, saved_draft
from ostraka.incidents.masking import mask_text
from ostraka.models import (
    FINISHED,
    ActivityEvent,
    Asset,
    Comment,
    Finding,
    FindingStatus,
    Incident,
    IncidentStatus,
    Lesson,
    Task,
)
from ostraka.standard import claims, yardstick
from ostraka.standard.assessment import LOADED, bottom_line
from ostraka.standard.rephrase import without_terms, yardstick_terms
from ostraka.updates import drafts

SENTENCES = 4
LISTED = 4  # confirmed findings named in a sentence; the rest are counted
LONGEST = 450  # characters in one sentence
SAVED_MAX = 3000
# what the model reads, at most
COMMENTS = 80
COMMENT_CHARS = 600
EVENTS = 40
LESSONS = 20
SUMMARY_CHARS = 6000

WORDED = ("ostraka", "ollama", "anthropic", "person")


@dataclass(frozen=True)
class FindingFact:
    ref: str
    title: str
    status: FindingStatus
    statement: str = ""
    techniques: tuple[str, ...] = ()


@dataclass
class Facts:
    """The record as the summary reads it. Text is unmasked here; `draft` and `prompt` mask it."""

    ref: str
    codename: str
    title: str
    kind: str  # incident or hunt
    severity: str
    tlp: str
    phase: str
    closed: bool
    declared_at: datetime
    closed_at: datetime | None
    summary: str = ""
    findings: list[FindingFact] = field(default_factory=list)
    assets: int = 0
    assessment_ref: str | None = None  # ASM-4
    assessment: str | None = None  # "We assess it is highly likely (80–90%) that …."
    open_tasks: int = 0
    done_tasks: int = 0
    lessons: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)  # "Dana Lead: …", oldest first
    events: list[str] = field(default_factory=list)  # "14 Nov 11:20 Dana Lead added FND-3", oldest first

    def of(self, status: FindingStatus) -> list[FindingFact]:
        return [x for x in self.findings if x.status is status]

    @property
    def confirmed(self) -> list[FindingFact]:
        return self.of(FindingStatus.CONFIRMED)


def _day(at: datetime) -> str:
    return f"{at.day} {at:%B %Y}"


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _sentence(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:] + ("" if text.endswith((".", "!", "?")) else ".")


# ---- Ostraka's own draft --------------------------------------------------------------------------------


def what_it_is(f: Facts) -> str:
    started = "started" if f.kind == "hunt" else "declared"
    kind = "hunt" if f.kind == "hunt" else f"{f.severity}-severity incident"
    when = f"{started} on {_day(f.declared_at)}"
    if f.closed and f.closed_at is not None:
        when += f" and closed on {_day(f.closed_at)}"
    else:
        when += f", still open in the {f.phase} phase"
    title = f.title.strip().rstrip(".")
    return f"{f.ref} {f.codename} is a {kind} at TLP:{f.tlp.upper()}, “{title}”, {when}."


def confirmed_findings(f: Facts) -> str:
    confirmed, leads = f.confirmed, f.of(FindingStatus.LEAD)
    if not confirmed:
        tail = f"; {_n(len(leads), 'lead is', 'leads are')} still being worked" if leads else ""
        return f"No finding has been confirmed{tail}."
    named = [f"{x.ref} ({x.title.strip().rstrip('.')})" for x in confirmed[:LISTED]]
    if len(confirmed) > LISTED:
        named.append(f"{len(confirmed) - LISTED} more")
    verb = "has" if len(confirmed) == 1 else "have"
    return f"{_n(len(confirmed), 'finding')} {verb} been confirmed: {_join(named)}."


def beside_them(f: Facts) -> str:
    confirmed = f.confirmed
    leads, refuted = f.of(FindingStatus.LEAD), f.of(FindingStatus.REFUTED)
    parts: list[str] = []
    techniques = sorted({t for x in confirmed for t in x.techniques})
    if techniques:
        shown = techniques[:6] + ([f"{len(techniques) - 6} more"] if len(techniques) > 6 else [])
        parts.append(f"the confirmed findings map to ATT&CK {_join(shown)}")
    if f.assets:
        parts.append(f"{_n(f.assets, 'asset is', 'assets are')} recorded")
    if confirmed and leads:
        parts.append(f"{_n(len(leads), 'lead remains', 'leads remain')} to be confirmed or refuted")
    if refuted:
        parts.append(f"{_n(len(refuted), 'finding was', 'findings were')} refuted")
    if not parts:
        return "Nothing else is recorded beside them yet." if confirmed else "No assets are recorded yet."
    return _sentence("; ".join(parts))


def conclusion(f: Facts) -> str:
    if f.assessment and f.assessment_ref:
        return f"{f.assessment.strip().rstrip('.')} ({f.assessment_ref})."
    if f.closed:
        return (
            f"The {f.kind} was closed without a current assessment, so this carries no judgement of what "
            "it means."
        )
    return "No assessment has been made yet, so this carries no judgement of what it means."


def draft(f: Facts) -> list[str]:
    """The four sentences by Ostraka's own rules, masked."""
    return [mask_text(s) for s in (what_it_is(f), confirmed_findings(f), beside_them(f), conclusion(f))]


# ---- a model's wording ----------------------------------------------------------------------------------

PROMPT = (
    "You write executive summaries inside Ostraka, an incident response platform. Write the executive "
    "summary of {ref} for senior leaders from the record below and nothing else.\n"
    "Return only a JSON array of exactly four strings, each one plain sentence in British English:\n"
    "1. What it is: start with {ref} and its code name, and say what kind of record it is, how severe, "
    "and whether it is open or closed.\n"
    "2 and 3. The confirmed findings: what has been confirmed, citing each by its reference (like FND-12). "
    "Only findings marked confirmed are confirmed. Leads are unconfirmed and refuted findings were ruled "
    "out; never call either confirmed. The discussion and activity are context: nothing said there is "
    "confirmed unless a confirmed finding says so.\n"
    "4. The conclusion: the current assessment, quoting its probability phrase with its percentage exactly "
    "and citing it (like ASM-4); with no current assessment, say no judgement has been made.\n"
    "Rules: cite only references that appear in the record. Use no likelihood words except the current "
    "assessment's phrase, no words of certainty such as definitely, clearly or proves, and no dramatic "
    "adjectives. Bracketed placeholders like [ip address] are masked indicators: keep them as they are. "
    "Keep each sentence under 60 words.\n\n"
    "Ostraka's own draft, to improve on:\n{draft}\n\nThe record:\n{record}\n"
)


def record_text(f: Facts) -> str:
    """The whole record as the model reads it, masked."""
    parts = [
        f"{f.ref} {f.codename}: {f.title}. {f.kind}, severity {f.severity}, TLP:{f.tlp.upper()}, phase "
        f"{f.phase}, {'closed' if f.closed else 'open'}. Declared {_day(f.declared_at)}"
        + (f", closed {_day(f.closed_at)}." if f.closed_at else "."),
        "",
        "Summary:",
        f.summary.strip()[:SUMMARY_CHARS] or "(none)",
        "",
        "Findings:",
    ]
    for x in f.findings:
        line = f"- {x.ref} ({x.status.value}) {x.title}"
        if x.statement.strip():
            line += f": {x.statement.strip()}"
        if x.techniques:
            line += f" [ATT&CK {', '.join(x.techniques)}]"
        parts.append(line)
    if not f.findings:
        parts.append("- none")
    parts += [
        "",
        f"Current assessment: {f.assessment_ref}: {f.assessment}"
        if f.assessment
        else "Current assessment: none",
        f"Assets recorded: {f.assets}. Tasks: {f.open_tasks} open, {f.done_tasks} done.",
        "",
        "Lessons:",
        *([f"- {x}" for x in f.lessons] or ["- none"]),
        "",
        "Discussion, oldest first:",
        *([f"- {x}" for x in f.comments] or ["- none"]),
        "",
        "Recent activity, oldest first:",
        *([f"- {x}" for x in f.events] or ["- none"]),
    ]
    return mask_text("\n".join(parts))


def prompt(f: Facts) -> str:
    return PROMPT.format(ref=f.ref, draft="\n".join(draft(f)), record=record_text(f))


def parse(reply: str) -> list[str] | None:
    """The JSON array of sentences in a model's reply, or None."""
    match = re.search(r"\[.*\]", reply, re.S)
    if not match:
        return None
    try:
        out = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(out, list) or not all(isinstance(x, str) for x in out):
        return None
    return [" ".join(x.split()) for x in out]


def known_refs(f: Facts) -> set[str]:
    return claims.refs_in(record_text(f)) | {f.ref.upper()}


def _unconfirmed(f: Facts) -> dict[str, str]:
    out = {x.ref.upper(): "lead" for x in f.of(FindingStatus.LEAD)}
    out.update({x.ref.upper(): "refuted" for x in f.of(FindingStatus.REFUTED)})
    return out


def text_problems(f: Facts, text: str) -> list[str]:
    """The rules any executive summary is held to, a person's included."""
    out = claims.problems(text, known_refs(f), _unconfirmed(f))
    if not f.confirmed:
        for sentence in claims.SENTENCE.split(text):
            if claims.CONFIRMED.search(sentence) and not re.search(
                r"\b(?:no|not|none|nothing|yet to be|un)\b|n't\b", sentence, re.I
            ):
                out.append("It says something is confirmed, but no finding is.")
                break
    allowed = yardstick_terms(f.assessment or "")
    used = yardstick_terms(text)
    if any(term not in allowed for term in used):
        out.append("It uses a probability term the current assessment doesn't.")
    if yardstick.LOOSE_PROBABILITY.search(without_terms(text)):
        out.append("It uses likelihood words outside the yardstick.")
    if LOADED.search(text):
        out.append("It uses loaded wording.")
    return out


def problems(f: Facts, sentences: list[str]) -> list[str]:
    """Why a model's four sentences can't stand; empty when they can. Fixed rules, no model."""
    if len(sentences) != SENTENCES:
        return [f"It wrote {len(sentences)} sentences, not {SENTENCES}."]
    out: list[str] = []
    for n, s in enumerate(sentences, start=1):
        if not s.strip():
            out.append(f"Sentence {n} is empty.")
        elif len(s) > LONGEST:
            out.append(f"Sentence {n} is too long.")
    if f.ref.upper() not in sentences[0].upper():
        out.append(f"The first sentence doesn't name {f.ref}.")
    confirmed = [x.ref.upper() for x in f.confirmed]
    cited = claims.refs_in(" ".join(sentences[1:3]))
    if confirmed:
        wanted = set(confirmed) if len(confirmed) <= LISTED else set()
        if not cited & set(confirmed) or not wanted <= cited:
            out.append("The findings sentences don't cite the confirmed findings.")
    if f.assessment_ref and f.assessment_ref.upper() not in sentences[3].upper():
        out.append(f"The conclusion doesn't cite {f.assessment_ref}.")
    return out + text_problems(f, " ".join(sentences))


# ---- reading the record ---------------------------------------------------------------------------------


async def gather(db: AsyncSession, incident: Incident) -> Facts:
    findings = (
        await db.scalars(select(Finding).where(Finding.incident_id == incident.id).order_by(Finding.seq))
    ).all()
    assets = (
        await db.scalar(select(func.count()).select_from(Asset).where(Asset.incident_id == incident.id))
    ) or 0
    tasks = (await db.scalars(select(Task.status).where(Task.incident_id == incident.id))).all()
    lessons = (
        await db.scalars(
            select(Lesson).where(Lesson.incident_id == incident.id).order_by(Lesson.seq).limit(LESSONS)
        )
    ).all()
    comments = (
        await db.scalars(
            select(Comment)
            .where(Comment.incident_id == incident.id, Comment.deleted_at.is_(None))
            .order_by(Comment.created_at.desc())
            .limit(COMMENTS)
        )
    ).all()
    events = (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.incident_id == incident.id)
            .order_by(ActivityEvent.position.desc())
            .limit(EVENTS)
        )
    ).all()
    current = await drafts.latest_assessment(db, incident.id)
    assessment = None
    if current is not None:
        assessment = bottom_line(saved_draft(current, await basis_findings(db, current)))
    return Facts(
        ref=incident.ref,
        codename=incident.codename,
        title=incident.title,
        kind=incident.kind.value,
        severity=incident.severity.value,
        tlp=incident.tlp.value,
        phase=incident.phase.value,
        closed=incident.status is IncidentStatus.CLOSED,
        declared_at=incident.declared_at,
        closed_at=incident.closed_at,
        summary=incident.summary,
        findings=[
            FindingFact(f"FND-{x.seq}", x.title, x.status, x.statement, tuple(x.techniques or ()))
            for x in findings
        ],
        assets=assets,
        assessment_ref=f"ASM-{current.seq}" if current is not None else None,
        assessment=assessment,
        open_tasks=sum(s not in FINISHED for s in tasks),
        done_tasks=sum(s in FINISHED for s in tasks),
        lessons=[
            x.observation.strip() + (f" ({x.recommendation.strip()})" if x.recommendation else "")
            for x in lessons
        ],
        comments=[
            f"{c.author.name if c.author else 'A former member'}: {c.body.strip()[:COMMENT_CHARS]}"
            for c in reversed(comments)
        ],
        events=[f"{e.at:%d %b %H:%M} {e.summary}" for e in reversed(events)],
    )
