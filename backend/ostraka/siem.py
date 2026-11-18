"""SIEM queries: running a query against a connected Splunk, Elasticsearch, Defender XDR or Google SecOps,
and the repository of every query run, found again by any part of its text.

The clients are samarkand's (`samarkand.siem`, Shikari's moved there). What's Ostraka's is here: which
settings each kind of connection takes and which of them are secret, building a client from a stored
connection, and the rule that only the query is kept. Its rows go back to the person who ran it, in
the response, and nowhere else: not the database, the log, the activity record or a live message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from samarkand import siem

from ostraka.auth.secrets import decrypt
from ostraka.integrations import url_problem
from ostraka.models import SiemConnection, SiemKind

MAX_ROWS = 1000  # rows shown for one run
TIMEOUT = 240.0  # seconds; nginx gives /api/siem/queries five minutes


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    hint: str = ""


@dataclass(frozen=True)
class Kind:
    name: str
    language: str
    fields: tuple[Field, ...]
    window: str  # how the search window is applied, for the form


KINDS: dict[SiemKind, Kind] = {
    SiemKind.SPLUNK: Kind(
        "Splunk",
        "SPL",
        (
            Field("url", "Management URL", hint="https://splunk.example.org:8089"),
            Field("token", "Token", secret=True, required=False, hint="Settings > Tokens"),
            Field("username", "User name", required=False, hint="Instead of a token"),
            Field("password", "Password", secret=True, required=False),
        ),
        "The window chosen above, unless the SPL sets earliest= or latest=.",
    ),
    SiemKind.ELASTIC: Kind(
        "Elasticsearch",
        "ES|QL",
        (
            Field("url", "URL", hint="https://elastic.example.org:9200"),
            Field("api_key", "API key", secret=True, required=False, hint="The encoded key Kibana gives"),
            Field("username", "User name", required=False, hint="Instead of an API key"),
            Field("password", "Password", secret=True, required=False),
        ),
        "@timestamp within the window chosen above, as well as the ES|QL's own WHERE.",
    ),
    SiemKind.DEFENDER: Kind(
        "Defender XDR",
        "KQL",
        (
            Field("tenant_id", "Tenant ID"),
            Field("client_id", "Application (client) ID"),
            Field("client_secret", "Client secret", secret=True, hint="Needs ThreatHunting.Read.All"),
        ),
        "The KQL's own (| where Timestamp > ago(1d)); Advanced Hunting looks back 30 days otherwise.",
    ),
    SiemKind.SECOPS: Kind(
        "Google SecOps",
        "UDM search",
        (
            Field("project", "Google Cloud project"),
            Field("instance", "Customer ID"),
            Field("region", "Region", hint="us, europe, europe-west2…"),
            Field("service_account", "Service account key (JSON)", secret=True, hint="Chronicle API Viewer"),
        ),
        "The window chosen above.",
    ),
}


class Unusable(ValueError):
    """A connection's settings that can't make a client, with what to fix."""


def split(kind: SiemKind, values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """What a form sent, as (settings, secrets), each field trimmed and only the kind's own."""
    settings, secrets = {}, {}
    for f in KINDS[kind].fields:
        value = (values.get(f.key) or "").strip()
        if value:
            (secrets if f.secret else settings)[f.key] = value
    return settings, secrets


def problem(kind: SiemKind, settings: dict[str, str], secrets: dict[str, str]) -> str | None:
    """Why these settings can't make a client, or None."""
    for f in KINDS[kind].fields:
        if f.required and not (settings.get(f.key) or secrets.get(f.key)):
            return f"{KINDS[kind].name} needs its {f.label[0].lower()}{f.label[1:]}."
    if "url" in settings and (reason := url_problem(settings["url"])):
        return reason
    try:
        client(kind, settings, secrets, verify=True)
    except (ValueError, KeyError, TypeError) as exc:
        return str(exc) or "These settings don't make a connection."
    return None


def client(
    kind: SiemKind, settings: dict[str, str], secrets: dict[str, str], *, verify: bool
) -> siem._Client:
    """samarkand's client for a connection. Raises ValueError when the settings won't do."""
    s, k = settings, secrets
    if kind is SiemKind.SPLUNK:
        return siem.Splunk(
            s.get("url", ""), token=k.get("token", ""), username=s.get("username", ""),
            password=k.get("password", ""), verify=verify,
        )  # fmt: skip
    if kind is SiemKind.ELASTIC:
        return siem.Elastic(
            s.get("url", ""), api_key=k.get("api_key", ""), username=s.get("username", ""),
            password=k.get("password", ""), verify=verify,
        )  # fmt: skip
    if kind is SiemKind.DEFENDER:
        return siem.Defender(
            s.get("tenant_id", ""), s.get("client_id", ""), k.get("client_secret", ""), verify=verify
        )
    try:
        account = json.loads(k.get("service_account") or "{}")
    except ValueError as exc:
        raise ValueError("The service account key isn't JSON: paste the whole key file.") from exc
    return siem.SecOps(
        account, s.get("project", ""), s.get("instance", ""), region=s.get("region") or "us", verify=verify
    )


def secrets_of(row: SiemConnection) -> dict[str, str]:
    return json.loads(decrypt(row.secret)) if row.secret else {}


def client_for(row: SiemConnection) -> siem._Client:
    """The client for a stored connection."""
    return client(row.kind, dict(row.settings or {}), secrets_of(row), verify=row.verify_tls)


def like(term: str) -> str:
    """A search term as an ILIKE pattern, its own % and _ taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
