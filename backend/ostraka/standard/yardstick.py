"""The PHIA probability yardstick (College of Policing, Intelligence management APP, p. 37–38): the
closed list of terms an assessment may use, with the range each one stands for."""

from __future__ import annotations

import re

from ostraka.models import Probability

# Ascending, as the yardstick is printed
TERMS: dict[Probability, tuple[str, str]] = {
    Probability.REMOTE_CHANCE: ("remote chance", "≤5%"),
    Probability.HIGHLY_UNLIKELY: ("highly unlikely", "10–20%"),
    Probability.UNLIKELY: ("unlikely", "25–35%"),
    Probability.REALISTIC_POSSIBILITY: ("realistic possibility", "40–<50%"),
    Probability.LIKELY: ("likely or probably", "55–75%"),
    Probability.HIGHLY_LIKELY: ("highly likely", "80–90%"),
    Probability.ALMOST_CERTAIN: ("almost certain", "≥95%"),
}

# The two terms that read as a noun ("there is a ...") rather than an adjective ("it is ...")
_NOUNS = {Probability.REMOTE_CHANCE, Probability.REALISTIC_POSSIBILITY}

# Likelihood written into free text, where it escapes the closed list
LOOSE_PROBABILITY = re.compile(
    r"\b(likely|unlikely|likelihood|probabl[ey]|possibl[ey]|possibility|perhaps|chance|"
    r"almost certain(ly)?|may have|might have|could have)\b",
    re.IGNORECASE,
)


def words(probability: Probability) -> str:
    """The term as it's read in a sentence: "highly likely", "likely" (not "likely or probably")."""
    term = TERMS[probability][0]
    return "likely" if probability is Probability.LIKELY else term


def range_of(probability: Probability) -> str:
    return TERMS[probability][1]


def phrase(probability: Probability, judgement: str) -> str:
    """The judgement with its term: it is highly likely (80–90%) that ..., or there is a remote
    chance (≤5%) that ...."""
    term = f"{words(probability)} ({range_of(probability)})"
    lead = f"there is a {term}" if probability in _NOUNS else f"it is {term}"
    return f"{lead} that {judgement}"
