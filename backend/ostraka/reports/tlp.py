"""Traffic Light Protocol 2.0 (FIRST): the label a report is shared under.

TLP:WHITE was renamed TLP:CLEAR in 2.0 and AMBER+STRICT was added; a 1.0 label is read as its 2.0
equivalent. The colours are FIRST's, shown on black as the standard asks, and match `--tlp-*` in
`frontend/src/styles/tokens.css`.
"""

from __future__ import annotations

DEFAULT = "TLP:AMBER"

LEVELS: dict[str, dict] = {
    "TLP:CLEAR": {
        "colour": (255, 255, 255),
        "rule": "Disclosure is not limited. Subject to standard copyright rules, it may be shared freely.",
    },
    "TLP:GREEN": {
        "colour": (51, 255, 0),
        "rule": "Limited disclosure: recipients may share it within their community, but not publicly.",
    },
    "TLP:AMBER": {
        "colour": (255, 192, 0),
        "rule": "Limited disclosure: recipients may share it on a need-to-know basis within their "
        "organisation and its clients.",
    },
    "TLP:AMBER+STRICT": {
        "colour": (255, 192, 0),
        "rule": "Limited disclosure: recipients may share it on a need-to-know basis within their "
        "organisation only.",
    },
    "TLP:RED": {
        "colour": (255, 43, 43),
        "rule": "For the eyes and ears of individual recipients only, no further disclosure.",
    },
}
_ALIASES = {"WHITE": "TLP:CLEAR", "AMBER STRICT": "TLP:AMBER+STRICT", "AMBER-STRICT": "TLP:AMBER+STRICT"}


def normalise(label: str | None) -> str:
    """A TLP label in its 2.0 form ("amber+strict" -> "TLP:AMBER+STRICT", "TLP:WHITE" -> "TLP:CLEAR").

    An empty label gives the default; anything that isn't a TLP label raises ValueError.
    """
    if not label or not str(label).strip():
        return DEFAULT
    word = str(label).strip().upper().replace("TLP:", "").replace("TLP ", "").strip()
    full = _ALIASES.get(word, f"TLP:{word}")
    if full not in LEVELS:
        raise ValueError(f"Not a TLP label: {label}. Use one of {', '.join(LEVELS)}")
    return full


def colour(label: str) -> tuple[int, int, int]:
    """The label's text colour, to be shown on black."""
    return LEVELS[normalise(label)]["colour"]


def rule(label: str) -> str:
    """What the label lets a recipient do with the information, in FIRST's terms."""
    return LEVELS[normalise(label)]["rule"]
