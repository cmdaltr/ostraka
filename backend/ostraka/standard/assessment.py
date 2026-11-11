"""An assessment's parts, the linter that checks them, and the sentences ostraka writes from them.

The author supplies the claim, a yardstick term, the findings it rests on and the searches that came
back empty. ostraka writes "We assess it is highly likely (80–90%) that ...", the basis in order of
strength, and "There is no evidence of ..." with the search that looked. The author never writes
those sentences, so they can't drift from the parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from ostraka.models import FindingStatus, Probability
from ostraka.standard import yardstick

# Words the APP warns against: they tell the reader how to feel rather than what's known
LOADED = re.compile(
    r"\b(huge(ly)?|massive(ly)?|enormous(ly)?|catastrophic(ally)?|devastating|disastrous|lacklustre|"
    r"lackluster|terrible|dreadful|shocking|alarming(ly)?|unprecedented|extremely|incredibly|"
    r"staggering|egregious|blatant(ly)?)\b",
    re.IGNORECASE,
)
# Certainty asserted in words instead of earned through the yardstick and the evidence
CERTAINTY = re.compile(
    r"\b(definitely|definitive(ly)?|certainly|undoubtedly|unquestionably|clearly|obviously|"
    r"conclusively|proves?|proven|without (a )?doubt|no doubt|beyond doubt)\b",
    re.IGNORECASE,
)
# The author writing the frame ostraka adds itself
SELF_FRAMED = re.compile(r"^\s*(we|i)\s+(assess|judge|believe|think|consider)\b", re.IGNORECASE)

STRONG = (Probability.HIGHLY_LIKELY, Probability.ALMOST_CERTAIN)


@dataclass(frozen=True)
class BasisItem:
    ref: str  # FND-12
    title: str
    status: FindingStatus


@dataclass(frozen=True)
class Absence:
    absent: str  # "data leaving the network"
    platform: str
    scope: str
    query: str = ""
    time_from: datetime | None = None
    time_to: datetime | None = None


@dataclass(frozen=True)
class Draft:
    judgement: str
    probability: Probability
    basis: tuple[BasisItem, ...] = ()
    absences: tuple[Absence, ...] = ()
    so_what: str = ""
    what_next: str = ""


@dataclass(frozen=True)
class Flag:
    rule: str
    severity: Literal["error", "warning"]
    field: str
    message: str
    words: list[str] = field(default_factory=list)


def lint(draft: Draft) -> list[Flag]:
    """Every problem with a draft. Errors stop it being saved; warnings are shown and saved with it."""
    flags: list[Flag] = []
    statuses = [b.status for b in draft.basis]
    confirmed = FindingStatus.CONFIRMED in statuses

    if not draft.basis and not draft.absences:
        flags.append(
            Flag(
                "no-basis",
                "error",
                "basis",
                "Cite the findings this rests on, or the searches that found nothing.",
            )
        )
    if FindingStatus.REFUTED in statuses:
        flags.append(
            Flag("refuted-basis", "error", "basis", "A refuted finding can't support an assessment.")
        )
    if draft.basis and not confirmed:
        if draft.probability is Probability.ALMOST_CERTAIN:
            flags.append(
                Flag(
                    "unbacked-certainty",
                    "error",
                    "probability",
                    "“Almost certain” needs at least one confirmed finding; this rests on leads only.",
                )
            )
        else:
            flags.append(
                Flag(
                    "leads-only",
                    "warning",
                    "basis",
                    "Every finding cited is still a lead. Say so in the judgement, or confirm one first.",
                )
            )
    if not draft.basis and draft.absences and draft.probability in STRONG:
        flags.append(
            Flag(
                "unbacked-certainty",
                "warning",
                "probability",
                f"“{yardstick.words(draft.probability)}” from absent evidence alone: an empty search shows "
                "where nobody found it, not that it didn't happen.",
            )
        )

    if SELF_FRAMED.search(draft.judgement):
        flags.append(
            Flag(
                "self-framed",
                "warning",
                "judgement",
                "Write the claim only (“the actor copied the payroll export”); Ostraka adds “We assess”.",
            )
        )
    if found := _all(yardstick.LOOSE_PROBABILITY, draft.judgement):
        flags.append(
            Flag(
                "probability-in-text",
                "warning",
                "judgement",
                "Likelihood belongs in the yardstick term, not the judgement's wording.",
                found,
            )
        )
    for name, text in (
        ("judgement", draft.judgement),
        ("so_what", draft.so_what),
        ("what_next", draft.what_next),
    ):
        if found := _all(CERTAINTY, text):
            flags.append(
                Flag(
                    "certainty-words",
                    "warning",
                    name,
                    "Certainty is carried by the yardstick and the evidence, not by words like these.",
                    found,
                )
            )
        if found := _all(LOADED, text):
            flags.append(
                Flag(
                    "loaded-adjective",
                    "warning",
                    name,
                    "Loaded wording: describe what was found instead.",
                    found,
                )
            )
    return flags


def _all(pattern: re.Pattern[str], text: str) -> list[str]:
    seen: list[str] = []
    for match in pattern.finditer(text or ""):
        word = match.group(0).lower()
        if word not in seen:
            seen.append(word)
    return seen


def _when(moment: datetime | None) -> str:
    return moment.strftime("%d %b %Y %H:%M UTC") if moment else ""


def _window(absence: Absence) -> str:
    if absence.time_from and absence.time_to:
        return f" between {_when(absence.time_from)} and {_when(absence.time_to)}"
    if absence.time_from:
        return f" from {_when(absence.time_from)}"
    if absence.time_to:
        return f" up to {_when(absence.time_to)}"
    return ""


_STRENGTH = {FindingStatus.CONFIRMED: 0, FindingStatus.LEAD: 1, FindingStatus.REFUTED: 2}


def ordered_basis(basis: tuple[BasisItem, ...]) -> list[BasisItem]:
    """Strongest first: confirmed findings, then leads, each kept in the author's order."""
    return sorted(basis, key=lambda b: _STRENGTH[b.status])


def bottom_line(draft: Draft) -> str:
    claim = draft.judgement.strip().rstrip(".")
    return f"We assess {yardstick.phrase(draft.probability, claim)}."


def sentences(draft: Draft) -> list[str]:
    """The assessment written out, bottom line up front: the judgement, the evidence from strongest to
    weakest, what wasn't found and where, then so what and what next."""
    out = [bottom_line(draft)]
    if draft.basis:
        cited = [f"{b.ref} ({b.status.value}: {b.title})" for b in ordered_basis(draft.basis)]
        out.append(f"This is based on {_join(cited)}.")
    for absence in draft.absences:
        out.append(
            f"There is no evidence of {absence.absent.strip().rstrip('.')}: a {absence.platform} search of "
            f"{absence.scope}{_window(absence)} found nothing."
        )
    if draft.so_what.strip():
        out.append(f"So what: {draft.so_what.strip()}")
    if draft.what_next.strip():
        out.append(f"What next: {draft.what_next.strip()}")
    return out


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"
