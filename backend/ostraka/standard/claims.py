"""Checks on prose a model wrote about a record, shared by Pnyx and the executive summary. Fixed rules,
no model: it may cite only references the record holds, may not call a lead or a refuted finding
confirmed, and may not assert certainty in words."""

from __future__ import annotations

import re

from ostraka.standard.assessment import CERTAINTY
from ostraka.standard.rephrase import REFS

SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
CONFIRMED = re.compile(r"\bconfirm(?:ed|s)?\b", re.I)
UNCONFIRMED = re.compile(r"\b(?:not|un|yet to be|still to be|isn't|aren't|hasn't|haven't)\b", re.I)


def refs_in(text: str) -> set[str]:
    return {m.upper() for m in REFS.findall(text)}


def problems(text: str, known: set[str], unconfirmed: dict[str, str]) -> list[str]:
    """Why `text` can't be shown; empty when it can. `unconfirmed` maps each finding that isn't confirmed
    to what it is (lead or refuted)."""
    out: list[str] = []
    unknown = sorted(refs_in(text) - {k.upper() for k in known})
    if unknown:
        out.append(f"It cites {', '.join(unknown)}, which the record doesn't hold.")
    called: dict[str, set[str]] = {}
    for sentence in SENTENCE.split(text):
        if not CONFIRMED.search(sentence) or UNCONFIRMED.search(sentence):
            continue
        for ref in refs_in(sentence):
            if ref in unconfirmed:
                called.setdefault(unconfirmed[ref], set()).add(ref)
    for status, refs in sorted(called.items()):
        names = sorted(refs)
        if status == "lead":
            still = "they are still leads" if len(names) > 1 else "it is still a lead"
        else:
            still = f"{'they were' if len(names) > 1 else 'it was'} {status}"
        out.append(f"It calls {', '.join(names)} confirmed, but {still}.")
    if CERTAINTY.search(text):
        out.append("It asserts certainty in words, which only the evidence may.")
    return out
