"""An incident as a MISP event, and pushing it to the installation's MISP instance.

Built as plain JSON, without PyMISP, on Shikari's event shape (shikari/parsers/misp_export.py,
which its misp_import.py reads back): ATT&CK techniques as `mitre-attack-pattern` galaxy clusters
with `meta.external_id`, and the same `misp-galaxy:mitre-attack-pattern="Name - T1234"` tag so
simple parsers find them too. Differences, because this is incident data rather than a hunt:

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

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from ostraka.exports.record import CONFIRMED, LEAD, IntelObservable, IntelRecord
from ostraka.models import Severity
from ostraka.observables.detect import ObservableType as T
from ostraka.reports import attack

NAMESPACE = uuid.UUID("0c0e8a52-6f3b-4c8e-9a51-2d4b7f1e6a90")
CONFIDENCE_TAG = 'estimative-language:confidence-in-analytic-judgment="{}"'
CONFIDENCE = {CONFIRMED: "high", LEAD: "low"}
DISTRIBUTIONS = {0: "Your organisation only", 1: "This community only", 2: "Connected communities", 3: "All"}
THREAT_LEVEL = {Severity.CRITICAL: "1", Severity.HIGH: "1", Severity.MEDIUM: "2", Severity.LOW: "3"}

# Observable type -> (MISP attribute type, category). None: no faithful MISP type, left out.
ATTRIBUTE: dict[T, tuple[str, str] | None] = {
    T.IP_ADDRESS: ("ip-dst", "Network activity"),
    T.CIDR: ("ip-dst", "Network activity"),
    T.DOMAIN: ("domain", "Network activity"),
    T.URL: ("url", "Network activity"),
    T.EMAIL: ("email-src", "Payload delivery"),
    T.EMAIL_SUBJECT: ("email-subject", "Payload delivery"),
    T.FILE_HASH_MD5: ("md5", "Payload delivery"),
    T.FILE_HASH_SHA1: ("sha1", "Payload delivery"),
    T.FILE_HASH_SHA256: ("sha256", "Payload delivery"),
    T.FILE_NAME: ("filename", "Artifacts dropped"),
    T.FILE_PATH: ("filename", "Artifacts dropped"),
    T.PROCESS_NAME: ("filename", "Artifacts dropped"),
    T.PROCESS_COMMAND: ("text", "Artifacts dropped"),
    T.REGISTRY_KEY: ("regkey", "Persistence mechanism"),
    T.SERVICE_NAME: ("windows-service-name", "Artifacts dropped"),
    T.USER_ACCOUNT: ("target-user", "Targeting data"),
    T.PHONE_NUMBER: ("phone-number", "Other"),
    T.CVE: ("vulnerability", "External analysis"),
    T.MUTEX: ("mutex", "Artifacts dropped"),
    T.BITCOIN_ADDRESS: ("btc", "Financial fraud"),
    T.ASN: ("AS", "Network activity"),
    T.MAC_ADDRESS: ("mac-address", "Network activity"),
    T.USER_AGENT: ("user-agent", "Network activity"),
    T.CERTIFICATE: ("x509-fingerprint-sha1", "Network activity"),
    T.ALIAS: None,
    T.MITRE_TECHNIQUE: None,  # a galaxy cluster, not an attribute
    T.OTHER: None,
}
# Types an IDS can't match on, so never to_ids even when confirmed
NOT_FOR_IDS = frozenset({"text", "target-user", "phone-number", "vulnerability", "comment"})
_CERT_TYPES = {32: "x509-fingerprint-md5", 40: "x509-fingerprint-sha1", 64: "x509-fingerprint-sha256"}


def _uuid(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(p) for p in parts)))


def technique_label(tid: str) -> str:
    """MISP's cluster value for a technique, "PowerShell - T1059.001" (Shikari's form)."""
    name, _ = attack.technique(tid)
    return f"{name} - {tid}" if name else tid


def attribute(item: IntelObservable) -> dict[str, Any] | None:
    """One observable as a MISP attribute, or None when MISP has no faithful type for it."""
    mapped = ATTRIBUTE.get(item.kind)
    if mapped is None:
        return None
    kind, category = mapped
    value = item.value.strip()
    if item.kind is T.CERTIFICATE:
        bare = value.replace(":", "").lower()
        kind = _CERT_TYPES.get(len(bare), "text") if all(c in "0123456789abcdef" for c in bare) else "text"
        value = bare if kind != "text" else value
    if item.kind is T.CVE:
        value = value.upper()
    word = "Confirmed" if item.confirmed else "Lead, not confirmed"
    comment = f"{item.ref}. {word}; verdict {item.verdict.value}"
    if item.cited_by:
        comment += f"; cited by {', '.join(item.cited_by)}"
    if item.sightings:
        comment += f"; {len(item.sightings)} sighting{'' if len(item.sightings) == 1 else 's'}"
    return {
        "uuid": _uuid("attribute", item.id),
        "type": kind,
        "category": category,
        "value": value,
        "to_ids": item.confirmed and kind not in NOT_FOR_IDS,
        "comment": comment[:1000],
        "first_seen": item.first_seen.isoformat(),
        "last_seen": item.last_seen.isoformat(),
        "Tag": [{"name": CONFIDENCE_TAG.format(CONFIDENCE[item.standing])}],
    }


def event(record: IntelRecord, *, distribution: int = 0, now: datetime | None = None) -> dict[str, Any]:
    """The incident as a MISP event: {"Event": {...}}."""
    attributes = [a for a in (attribute(o) for o in record.observables) if a]
    for finding in record.findings:
        word = "Confirmed finding" if finding.standing == CONFIRMED else "Lead, not confirmed"
        techniques = f" Techniques: {', '.join(finding.techniques)}." if finding.techniques else ""
        attributes.append(
            {
                "uuid": _uuid("finding", finding.id),
                "type": "comment",
                "category": "Internal reference",
                "value": f"{finding.ref}: {finding.title}. {word}. {finding.statement}{techniques}".strip()[
                    :5000
                ],
                "to_ids": False,
                "comment": f"{finding.ref} ({finding.standing})",
                "Tag": [{"name": CONFIDENCE_TAG.format(CONFIDENCE[finding.standing])}],
            }
        )
    attributes.append(
        {
            "uuid": _uuid("link", record.id),
            "type": "link",
            "category": "Internal reference",
            "value": record.url,
            "to_ids": False,
            "comment": f"{record.ref} in Ostraka",
        }
    )

    confirmed = [tid for tid in record.techniques() if record.technique_standing(tid) == CONFIRMED]
    confirmed += [
        o.value.strip().upper()
        for o in record.observables
        if o.kind is T.MITRE_TECHNIQUE and o.confirmed and o.value.strip().upper() not in confirmed
    ]
    tags = [{"name": f"tlp:{record.tlp.value}"}]
    tags += [{"name": f'misp-galaxy:mitre-attack-pattern="{technique_label(t)}"'} for t in confirmed]
    galaxies = []
    if confirmed:
        galaxies.append(
            {
                "type": "mitre-attack-pattern",
                "name": "Attack Pattern",
                "GalaxyCluster": [
                    {
                        "type": "mitre-attack-pattern",
                        "value": technique_label(t),
                        "meta": {"external_id": [t]},
                    }
                    for t in confirmed
                ],
            }
        )
    return {
        "Event": {
            "uuid": _uuid("event", record.id),
            "info": f"{record.ref}: {record.title}",
            "date": record.declared_at.date().isoformat(),
            "threat_level_id": THREAT_LEVEL[record.severity],
            "analysis": "2" if record.closed_at else "1",  # completed, or ongoing
            "distribution": str(distribution),
            "published": False,
            **({"timestamp": str(int(now.timestamp()))} if now else {}),
            "Attribute": attributes,
            "Tag": tags,
            "Galaxy": galaxies,
        }
    }


# ---- pushing ------------------------------------------------------------------------------------


class MispProblem(Exception):
    """The MISP instance refused or couldn't be reached, with a reason an admin can act on."""


@dataclass(frozen=True)
class Pushed:
    action: str  # "added" or "updated"
    event_id: str
    uuid: str
    url: str


async def fetch(
    method: str, url: str, *, key: str, verify: bool, body: bytes | None = None
) -> tuple[int, bytes]:
    """One request to MISP with the API key. Replaced in tests."""
    headers = {
        "Authorization": key,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ostraka-misp/1",
    }
    async with httpx.AsyncClient(timeout=30, verify=verify, follow_redirects=False) as client:
        response = await client.request(method, url, content=body, headers=headers)
        return response.status_code, response.content


def _problem(status: int, body: bytes) -> MispProblem:
    try:
        message = json.loads(body).get("message") or json.loads(body).get("name") or ""
    except (ValueError, AttributeError):
        message = ""
    if status in (401, 403):
        return MispProblem(f"MISP refused the API key (HTTP {status}). {message}".strip())
    return MispProblem(f"MISP answered HTTP {status}. {message}".strip())


async def push(base_url: str, key: str, verify: bool, payload: dict[str, Any]) -> Pushed:
    """Add the event, or update it if MISP already has one with its uuid."""
    root = base_url.strip().rstrip("/")
    event_uuid = payload["Event"]["uuid"]
    body = json.dumps(payload).encode()
    try:
        status, _ = await fetch("GET", f"{root}/events/view/{event_uuid}", key=key, verify=verify)
        exists = status == 200
        # MISP answers a missing event with 404, or 403 on some versions; a bad key fails the add too
        if status not in (200, 403, 404):
            raise _problem(status, b"")
        path = f"/events/edit/{event_uuid}" if exists else "/events/add"
        status, answer = await fetch("POST", root + path, key=key, verify=verify, body=body)
    except httpx.HTTPError as exc:
        raise MispProblem(f"Couldn't reach MISP: {str(exc)[:200] or type(exc).__name__}") from exc
    if status >= 300:
        raise _problem(status, answer)
    try:
        made = json.loads(answer).get("Event", {})
    except (ValueError, AttributeError):
        made = {}
    event_id = str(made.get("id", ""))
    return Pushed(
        action="updated" if exists else "added",
        event_id=event_id,
        uuid=str(made.get("uuid", event_uuid)),
        url=f"{root}/events/view/{event_id or event_uuid}",
    )


async def check(base_url: str, key: str, verify: bool) -> str:
    """The MISP version, which proves the URL and key work. Raises MispProblem."""
    root = base_url.strip().rstrip("/")
    try:
        status, answer = await fetch("GET", f"{root}/servers/getVersion", key=key, verify=verify)
    except httpx.HTTPError as exc:
        raise MispProblem(f"Couldn't reach MISP: {str(exc)[:200] or type(exc).__name__}") from exc
    if status >= 300:
        raise _problem(status, answer)
    try:
        return str(json.loads(answer).get("version", ""))
    except (ValueError, AttributeError):
        return ""
