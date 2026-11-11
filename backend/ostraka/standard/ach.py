"""Analysis of Competing Hypotheses: the arithmetic, kept to plain sums so anyone can check it.

ACH looks for the hypothesis with the least evidence against it, not the most for it: evidence
consistent with everything proves nothing, while one credible inconsistency can sink a hypothesis.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass

from ostraka.models import Rating, Weight

WEIGHTS = {Weight.HIGH: 1.414, Weight.MEDIUM: 1.0, Weight.LOW: 0.707}
ALGORITHM = (
    "Weighted inconsistency (v1): each Inconsistent rating counts −1 × credibility × relevance "
    "(H 1.414, M 1, L 0.707); each analyst's sum is averaged across the analysts rating the matrix. "
    "The open hypothesis with the score nearest zero leads."
)
MAX_RATERS = 8  # the APP's suggested ceiling for a group rating one matrix

Cell = tuple[uuid.UUID, uuid.UUID]  # (hypothesis, evidence)


@dataclass(frozen=True)
class Evidence:
    id: uuid.UUID
    credibility: Weight
    relevance: Weight

    @property
    def weight(self) -> float:
        return WEIGHTS[self.credibility] * WEIGHTS[self.relevance]


@dataclass(frozen=True)
class Result:
    scores: dict[uuid.UUID, float]
    leading: uuid.UUID | None  # None when no open hypothesis or a tie at the top
    consensus: dict[Cell, Rating | None]  # None: the analysts disagree with no majority
    disputed: list[Cell]  # analysts gave different ratings
    non_diagnostic: list[uuid.UUID]  # evidence rated the same against every open hypothesis
    pivotal: dict[uuid.UUID, uuid.UUID | None]  # evidence whose removal changes the lead -> the new lead
    raters: int
    algorithm: str = ALGORITHM


def consensus(values: list[Rating]) -> Rating | None:
    """The majority rating for a cell, or None when the top ratings tie."""
    if not values:
        return None
    (first, n), *rest = Counter(values).most_common()
    return None if rest and rest[0][1] == n else first


def scores(
    hypotheses: list[uuid.UUID],
    evidence: list[Evidence],
    ratings: dict[Cell, dict[uuid.UUID, Rating]],
    *,
    leave_out: uuid.UUID | None = None,
) -> dict[uuid.UUID, float]:
    """Each hypothesis's weighted inconsistency, averaged across analysts. An analyst who didn't rate a
    cell adds nothing for it."""
    raters = {r for by_rater in ratings.values() for r in by_rater}
    totals = dict.fromkeys(hypotheses, 0.0)
    if not raters:
        return totals
    for h in hypotheses:
        for e in evidence:
            if e.id == leave_out:
                continue
            for value in ratings.get((h, e.id), {}).values():
                if value is Rating.INCONSISTENT:
                    totals[h] -= e.weight
        totals[h] = round(totals[h] / len(raters), 3)
    return totals


def leader(scores_by: dict[uuid.UUID, float], open_ids: list[uuid.UUID]) -> uuid.UUID | None:
    candidates = [(scores_by[h], h) for h in open_ids if h in scores_by]
    if not candidates:
        return None
    best = max(score for score, _ in candidates)
    top = [h for score, h in candidates if score == best]
    return top[0] if len(top) == 1 else None


def analyse(
    hypotheses: list[uuid.UUID],
    open_ids: list[uuid.UUID],
    evidence: list[Evidence],
    ratings: dict[Cell, dict[uuid.UUID, Rating]],
) -> Result:
    """Scores, the leading hypothesis, where analysts disagree, which evidence can't tell the
    hypotheses apart, and which single piece of evidence would change the lead if it were wrong."""
    by = scores(hypotheses, evidence, ratings)
    lead = leader(by, open_ids)
    agreed = {cell: consensus(list(values.values())) for cell, values in ratings.items()}
    disputed = [cell for cell, values in ratings.items() if len(set(values.values())) > 1]

    non_diagnostic = []
    if len(open_ids) > 1:
        for e in evidence:
            row = [agreed.get((h, e.id)) for h in open_ids]
            if all(v is not None for v in row) and len(set(row)) == 1:
                non_diagnostic.append(e.id)

    pivotal: dict[uuid.UUID, uuid.UUID | None] = {}
    for e in evidence:
        without = leader(scores(hypotheses, evidence, ratings, leave_out=e.id), open_ids)
        if without != lead:
            pivotal[e.id] = without

    raters = len({r for values in ratings.values() for r in values})
    return Result(by, lead, agreed, disputed, non_diagnostic, pivotal, raters)
