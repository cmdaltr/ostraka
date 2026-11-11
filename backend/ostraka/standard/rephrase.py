"""The check an AI rewording must pass before Ostraka will use it. Fixed rules, no model.

A rephrase may only reword. Line by line it must keep every reference (FND-12, ASM-4, OST-2026-014),
every number, every PHIA term with its range, every masked indicator, and the words that say how
strong a claim is: confirmed, lead, refuted, no evidence, and negation. It may not add likelihood
words outside the yardstick, certainty words or loaded adjectives. Any difference rejects the whole
rephrase, and the draft goes out as Ostraka wrote it.
"""

from __future__ import annotations

import re
from collections import Counter

from ostraka.standard import yardstick
from ostraka.standard.assessment import CERTAINTY, LOADED

REFS = re.compile(r"\b(?:(?:OST|HNT)-\d{4}-\d{3,}|(?:FND|AST|OBS|TSK|ASM|HYP|EVD|LSN|UPD)-\d+)\b", re.I)
NUMBERS = re.compile(r"\d+(?:[.,:]\d+)*")
MASKED = re.compile(r"\[[a-z0-9 ]+\]", re.I)
# Words that carry a claim's strength; their counts must survive the rewording
STRENGTH = {
    "confirmed": re.compile(r"\bconfirm(?:ed|s)?\b", re.I),
    "lead": re.compile(r"\bleads?\b", re.I),
    "refuted": re.compile(r"\b(?:refuted|ruled out)\b", re.I),
    "no evidence": re.compile(r"\bno evidence\b", re.I),
    "negation": re.compile(r"\b(?:not|no|never|none|nothing|without)\b", re.I),
}


def _yardstick_terms(text: str) -> Counter:
    found: Counter = Counter()
    for probability in yardstick.TERMS:
        phrase = f"{yardstick.words(probability)} ({yardstick.range_of(probability)})"
        found[probability.value] = text.lower().count(phrase.lower())
    return +found


def _without_terms(text: str) -> str:
    out = text
    for probability in yardstick.TERMS:
        out = re.sub(
            re.escape(f"{yardstick.words(probability)} ({yardstick.range_of(probability)})"),
            "",
            out,
            flags=re.I,
        )
    return out


def _norm(text: str) -> str:
    return re.sub(r"n't\b", " not", text.replace("’", "'"), flags=re.I)


def problems(original: list[str], reworded: list[str]) -> list[str]:
    """Why `reworded` can't stand in for `original`; empty when it can."""
    if len(original) != len(reworded):
        return [f"The rewording has {len(reworded)} lines where the draft has {len(original)}."]
    out: list[str] = []
    for n, (before, after) in enumerate(zip(original, reworded, strict=True), start=1):
        if not after.strip():
            out.append(f"Line {n} is empty.")
            continue
        a, b = _norm(before), _norm(after)
        checks = (
            (
                "references",
                Counter(r.upper() for r in REFS.findall(a)),
                Counter(r.upper() for r in REFS.findall(b)),
            ),
            ("numbers", Counter(NUMBERS.findall(a)), Counter(NUMBERS.findall(b))),
            ("probability terms", _yardstick_terms(a), _yardstick_terms(b)),
            (
                "masked indicators",
                Counter(m.lower() for m in MASKED.findall(a)),
                Counter(m.lower() for m in MASKED.findall(b)),
            ),
        )
        for what, was, now in checks:
            if was != now:
                out.append(f"Line {n} changed its {what}.")
        for word, pattern in STRENGTH.items():
            if len(pattern.findall(a)) != len(pattern.findall(b)):
                out.append(f"Line {n} changed how strongly it's put (“{word}”).")
        loose = len(yardstick.LOOSE_PROBABILITY.findall(_without_terms(b)))
        if loose > len(yardstick.LOOSE_PROBABILITY.findall(_without_terms(a))):
            out.append(f"Line {n} adds likelihood words outside the yardstick.")
        for name, pattern in (("certainty", CERTAINTY), ("loaded", LOADED)):
            if len(pattern.findall(b)) > len(pattern.findall(a)):
                out.append(f"Line {n} adds {name} wording.")
    return out
