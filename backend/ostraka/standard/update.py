"""A stakeholder update, assembled from the record by fixed templates and checked by fixed rules.

Bottom line up front: the latest assessment's sentences (judgement, then its evidence strongest
first, so what and what next), then what changed since the last update from strongest to weakest
(confirmed findings, refuted ones, containment, phase and severity, finished and blocked work, and
leads last, labelled as leads), then where things stand, comms' note, and when the next one comes.

Only a confirmed finding is stated as fact. A lead always carries "Lead, not yet confirmed".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ostraka.standard import yardstick
from ostraka.standard.assessment import CERTAINTY, LOADED, Draft, Flag, _all, lint

# Strongest first; the order lines appear in under "what changed"
KINDS = (
    "confirmed",
    "refuted",
    "containment",
    "phase",
    "severity",
    "task_done",
    "task_blocked",
    "lead",
    "nothing",
)
SECTIONS = ("assessment", "what", "position", "note", "next")
CONTAINED = ("contained", "eradicated", "recovered", "clean")


@dataclass(frozen=True)
class Change:
    kind: str  # one of KINDS
    ref: str  # FND-12, AST-3, TSK-9, or "" for the incident itself
    title: str = ""
    detail: str = ""  # an asset's status, a phase, "high → critical", a task's outcome or why it's blocked


@dataclass(frozen=True)
class Position:
    phase: str
    severity: str
    assets: int = 0
    contained: int = 0
    tasks_open: int = 0
    tasks_blocked: int = 0


@dataclass(frozen=True)
class Composition:
    assessment_ref: str | None
    assessment_sentences: tuple[str, ...]  # written from the assessment's parts when the draft was built
    assessment_new: bool
    changes: tuple[Change, ...]
    position: Position
    next_line: str
    previous: str | None = None  # the last update sent: UPD-6
    note: str = ""


@dataclass(frozen=True)
class Line:
    section: str
    text: str
    kind: str = ""
    ref: str = ""

    def json(self) -> dict:
        return {"section": self.section, "text": self.text, "kind": self.kind, "ref": self.ref}


@dataclass
class Check:
    flags: list[Flag] = field(default_factory=list)

    @property
    def errors(self) -> list[Flag]:
        return [f for f in self.flags if f.severity == "error"]


def _clean(text: str) -> str:
    return " ".join(text.split()).rstrip(".")


def change_text(change: Change) -> str:
    ref, title = change.ref, _clean(change.title)
    match change.kind:
        case "confirmed":
            return f"Confirmed: {ref} {title}."
        case "refuted":
            return f"Ruled out: {ref} {title} was checked and refuted."
        case "containment":
            return f"{ref} {title} is now {change.detail}."
        case "phase":
            return f"The response moved to the {change.detail} phase."
        case "severity":
            return f"Severity changed: {change.detail}."
        case "task_done":
            outcome = f": decided {_clean(change.detail)}" if change.detail else ""
            return f"Done: {ref} {title}{outcome}."
        case "task_blocked":
            why = f": {_clean(change.detail)}" if change.detail else ""
            return f"Blocked: {ref} {title}{why}."
        case "lead":
            return f"Lead, not yet confirmed: {ref} {title}."
    raise ValueError(f"not a kind of change: {change.kind}")


def position_text(p: Position) -> str:
    parts = [f"the response is in the {p.phase} phase at {p.severity} severity"]
    if p.assets:
        parts.append(f"{p.contained} of {p.assets} affected assets contained")
    work = f"{p.tasks_open} tasks open"
    if p.tasks_blocked:
        work += f", {p.tasks_blocked} blocked"
    parts.append(work)
    return f"Where things stand: {'; '.join(parts)}."


def compose(c: Composition) -> list[Line]:
    """The update's lines, in order. Nothing here is written by a model."""
    lines: list[Line] = []
    if c.assessment_ref:
        for i, sentence in enumerate(c.assessment_sentences):
            lines.append(Line("assessment", sentence, "assessment" if i == 0 else "", c.assessment_ref or ""))
        if not c.assessment_new and c.previous:
            lines.append(Line("assessment", f"The assessment is unchanged since {c.previous}.", "unchanged"))
    ordered = sorted(c.changes, key=lambda ch: KINDS.index(ch.kind))
    for change in ordered:
        lines.append(Line("what", change_text(change), change.kind, change.ref))
    if not ordered:
        since = f" since {c.previous}" if c.previous else ""
        lines.append(Line("what", f"Nothing material has changed{since}.", "nothing"))
    lines.append(Line("position", position_text(c.position), "position"))
    if c.note.strip():
        lines.append(Line("note", c.note.strip(), "note"))
    lines.append(Line("next", c.next_line, "next"))
    return lines


def check(
    c: Composition,
    lines: list[Line],
    assessment: Draft | None,
    *,
    superseded: bool = False,
    rephrased: list[str] | None = None,
    use_rephrased: bool = False,
) -> Check:
    """Every problem with an update. An error stops it being approved. `assessment` is the cited
    assessment as it stands now, so a basis refuted since the draft was built still stops it."""
    out = Check()
    if assessment is None or not c.assessment_ref:
        out.flags.append(
            Flag(
                "no-assessment",
                "error",
                "assessment",
                "Every update carries an assessment. Make one on the Assessments tab, "
                "then rebuild the draft.",
            )
        )
    else:
        for f in lint(assessment):
            out.flags.append(
                Flag(f.rule, f.severity, "assessment", f"{c.assessment_ref}: {f.message}", f.words)
            )
        if superseded:
            out.flags.append(
                Flag(
                    "superseded-assessment",
                    "error",
                    "assessment",
                    f"{c.assessment_ref} has been superseded since this draft was built. Rebuild it.",
                )
            )

    texts = [line.text for line in lines]
    if use_rephrased:
        if rephrased is None or len(rephrased) != len(lines):
            out.flags.append(
                Flag(
                    "stale-rephrase",
                    "error",
                    "rephrase",
                    "The reworded text no longer matches the draft. Rephrase again or send the original.",
                )
            )
        else:
            texts = rephrased

    for name, text in (("note", c.note), ("update", " ".join(texts))):
        if found := _all(CERTAINTY, text):
            out.flags.append(
                Flag(
                    "certainty-words",
                    "warning",
                    name,
                    "Certainty is carried by the yardstick and the evidence, not by words like these.",
                    found,
                )
            )
        if found := _all(LOADED, text):
            out.flags.append(
                Flag(
                    "loaded-adjective",
                    "warning",
                    name,
                    "Loaded wording: describe what was found instead.",
                    found,
                )
            )
    if found := _all(yardstick.LOOSE_PROBABILITY, c.note):
        out.flags.append(
            Flag(
                "probability-in-text",
                "warning",
                "note",
                "Likelihood belongs in the assessment's yardstick term, not the note.",
                found,
            )
        )
    what = sum(line.section == "what" and line.kind != "nothing" for line in lines)
    if what > 4:
        out.flags.append(
            Flag(
                "four-three-three",
                "warning",
                "what",
                f"{what} points under what changed. 4-3-3 suggests four: consider a shorter rhythm.",
            )
        )
    return out


def render(lines: list[Line], texts: list[str] | None = None) -> str:
    """Plain text for email and Teams: a heading per section, each change as a bullet."""
    headings = {
        "assessment": "Assessment",
        "what": "What has changed",
        "position": "",
        "note": "From the communications lead",
        "next": "",
    }
    words = texts or [line.text for line in lines]
    blocks: list[str] = []
    current = None
    for line, text in zip(lines, words, strict=True):
        if line.section != current:
            current = line.section
            heading = headings.get(line.section, "")
            blocks.append(f"{heading}\n" if heading else "")
        bullet = "- " if line.section == "what" else ""
        blocks[-1] += f"{bullet}{text}\n"
    return "\n".join(b.rstrip("\n") + "\n" for b in blocks).strip()
