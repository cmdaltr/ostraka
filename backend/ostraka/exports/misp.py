"""An incident as a MISP event, and pushing it to the installation's MISP instance.

Assembled by `samarkand.misp`, shared with Shikari and Rowanholt: the event and attribute shapes,
the observable-to-MISP type table and the add-or-update push. ATT&CK techniques are
`mitre-attack-pattern` galaxy clusters with `meta.external_id`, and the same
`misp-galaxy:mitre-attack-pattern="Name - T1234"` tag so simple parsers find them too. What goes in
an event is Ostraka's, because this is incident data rather than a hunt:

- Only confirmed techniques become galaxy clusters and tags. A technique named only by leads is in
  that lead's comment attribute, never in the galaxy, which MISP users read as established.
- `to_ids` is true only for a confirmed indicator (malicious, and cited by a confirmed finding) of a
  type an IDS can match. Every attribute carries `estimative-language:confidence-in-analytic-judgment`
  ("high" when confirmed, "low" for a lead) and says which it is in its comment.
- The event's TLP is a `tlp:` tag from MISP's TLP 2.0 taxonomy; the event is never published by a
  push (an admin publishes in MISP), and its uuid comes from the incident, so pushing again
  updates the same event.

Pushing needs `automation` and an admin's settings (URL, encrypted API key, TLS verification); a
TLP:RED or TLP:AMBER+STRICT incident is never pushed (`policy.can_export_intel`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from samarkand import misp as shared
from samarkand.misp import (  # noqa: F401  (re-exported for the API and tests)
    CONFIDENCE_TAG,
    DISTRIBUTIONS,
    NOT_FOR_IDS,
    MispProblem,
    Pushed,
)

from ostraka.exports.record import CONFIRMED, LEAD, IntelObservable, IntelRecord
from ostraka.models import Severity
from ostraka.observables.detect import ObservableType as T
from ostraka.reports import attack

NAMESPACE = uuid.UUID("0c0e8a52-6f3b-4c8e-9a51-2d4b7f1e6a90")
CONFIDENCE = {CONFIRMED: "high", LEAD: "low"}
THREAT_LEVEL = {Severity.CRITICAL: "1", Severity.HIGH: "1", Severity.MEDIUM: "2", Severity.LOW: "3"}
USER_AGENT = "ostraka-misp/1"
TIMEOUT = 30.0
# Observable types Ostraka has and samarkand's neutral kinds don't; every other type is samarkand's
# table (`samarkand.misp.ATTRIBUTE_TYPES`). An alias, a technique (a galaxy cluster) and "other" have
# no faithful MISP type and are left out.
_OWN_TYPES = {T.PHONE_NUMBER: ("phone-number", "Other"), T.BITCOIN_ADDRESS: ("btc", "Financial fraud")}


def _uuid(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(p) for p in parts)))


def technique_label(tid: str) -> str:
    """MISP's cluster value for a technique, "PowerShell - T1059.001" (Shikari's form)."""
    return shared.technique_label(tid, attack.technique(tid)[0])


def attribute(item: IntelObservable) -> dict[str, Any] | None:
    """One observable as a MISP attribute, or None when MISP has no faithful type for it."""
    word = "Confirmed" if item.confirmed else "Lead, not confirmed"
    comment = f"{item.ref}. {word}; verdict {item.verdict.value}"
    if item.cited_by:
        comment += f"; cited by {', '.join(item.cited_by)}"
    if item.sightings:
        comment += f"; {len(item.sightings)} sighting{'' if len(item.sightings) == 1 else 's'}"
    fields: dict[str, Any] = {
        "uuid": _uuid("attribute", item.id),
        "to_ids": item.confirmed,
        "comment": comment,
        "first_seen": item.first_seen.isoformat(),
        "last_seen": item.last_seen.isoformat(),
        "tags": (CONFIDENCE_TAG.format(CONFIDENCE[item.standing]),),
    }
    if item.kind in _OWN_TYPES:
        kind, category = _OWN_TYPES[item.kind]
        fields["to_ids"] = item.confirmed and kind not in NOT_FOR_IDS
        fields["comment"] = comment[: shared.COMMENT_LIMIT]
        made: shared.Attribute | None = shared.Attribute(kind, category, item.value.strip(), **fields)
    else:
        made = shared.attribute(item.kind, item.value, **fields)
    return made.as_dict() if made else None


def event(record: IntelRecord, *, distribution: int = 0, now: datetime | None = None) -> dict[str, Any]:
    """The incident as a MISP event: {"Event": {...}}."""
    attributes = [a for a in (attribute(o) for o in record.observables) if a]
    for finding in record.findings:
        word = "Confirmed finding" if finding.standing == CONFIRMED else "Lead, not confirmed"
        techniques = f" Techniques: {', '.join(finding.techniques)}." if finding.techniques else ""
        value = f"{finding.ref}: {finding.title}. {word}. {finding.statement}{techniques}".strip()[:5000]
        attributes.append(
            shared.Attribute(
                "comment",
                "Internal reference",
                value,
                to_ids=False,
                comment=f"{finding.ref} ({finding.standing})",
                uuid=_uuid("finding", finding.id),
                tags=(CONFIDENCE_TAG.format(CONFIDENCE[finding.standing]),),
            ).as_dict()
        )
    attributes.append(
        shared.Attribute(
            "link",
            "Internal reference",
            record.url,
            to_ids=False,
            comment=f"{record.ref} in Ostraka",
            uuid=_uuid("link", record.id),
        ).as_dict()
    )

    confirmed = [tid for tid in record.techniques() if record.technique_standing(tid) == CONFIRMED]
    confirmed += [
        o.value.strip().upper()
        for o in record.observables
        if o.kind is T.MITRE_TECHNIQUE and o.confirmed and o.value.strip().upper() not in confirmed
    ]
    return shared.event(
        info=f"{record.ref}: {record.title}",
        date=record.declared_at.date().isoformat(),
        attributes=attributes,
        techniques=[(t, attack.technique(t)[0]) for t in confirmed],
        tlp=record.tlp.value,
        uuid=_uuid("event", record.id),
        threat_level_id=THREAT_LEVEL[record.severity],
        analysis="2" if record.closed_at else "1",  # completed, or ongoing
        distribution=distribution,
        published=False,
        timestamp=int(now.timestamp()) if now else None,
    )


# ---- pushing ------------------------------------------------------------------------------------


async def fetch(
    method: str, url: str, *, key: str, verify: bool, body: bytes | None = None
) -> tuple[int, bytes]:
    """One request to MISP with the API key: (status, body). Replaced in tests."""
    headers = {
        "Authorization": key,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=verify, follow_redirects=False) as client:
        response = await client.request(method, url, content=body, headers=headers)
        return response.status_code, response.content


@dataclass(frozen=True)
class _Answer:
    status: int
    body: bytes


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,  # noqa: ASYNC109 (samarkand passes it; fetch has its own TIMEOUT)
    verify: bool,
    body: bytes | None = None,
) -> _Answer:
    # samarkand's request, made through fetch, so fetch is the one place that talks to MISP
    status, answer = await fetch(method, url, key=headers["Authorization"], verify=verify, body=body)
    return _Answer(status, answer)


async def push(base_url: str, key: str, verify: bool, payload: dict[str, Any]) -> Pushed:
    """Add the event, or update it if MISP already has one with its uuid. Raises MispProblem."""
    return await shared.push_async(
        base_url, key, payload, verify=verify, timeout=TIMEOUT, user_agent=USER_AGENT, request=_request
    )


async def check(base_url: str, key: str, verify: bool) -> str:
    """The MISP version, which proves the URL and key work. Raises MispProblem."""
    # samarkand's version() is synchronous only: the answer is fetched here and handed to it, so its
    # reading of the answer and its refusals are the ones used
    root = base_url.strip().rstrip("/")
    try:
        answer = await _request(
            "GET",
            f"{root}/servers/getVersion",
            headers={"Authorization": key},
            timeout=TIMEOUT,
            verify=verify,
        )
    except httpx.HTTPError as exc:
        raise MispProblem(f"Couldn't reach MISP: {str(exc)[:200] or type(exc).__name__}") from exc
    return shared.version(root, key, request=lambda *a, **k: answer)
