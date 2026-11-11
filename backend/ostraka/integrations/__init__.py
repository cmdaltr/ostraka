"""Ostraka's links to the rest of the suite: Rowanholt (evidence and analysis) and Shikari (detection).

Ostraka doesn't duplicate either. It keeps the incident's record of what they hold: a Rowanholt job
or case linked to an incident, the rows a finding cites, and, for each confirmed ATT&CK technique,
whether Shikari has a detection for it (`gaps`). Both are reached with their own API tokens, stored
encrypted, over their existing HTTP APIs (`http`, `rowanholt`, `shikari`).

The TLP guard: a TLP:RED or TLP:AMBER+STRICT incident only exchanges anything with a sibling server
on the organisation's own network (loopback, a private or Tailscale address, or a `.ts.net`,
`.internal`, `.lan` or `.home.arpa` name), as such an incident's AI work stays on local Ollama.
Requests never carry an incident's own content in any case, only the sibling's identifiers (job,
host, artefact, row) and ATT&CK technique ids.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify
from ostraka.auth.secrets import decrypt
from ostraka.models import Incident, Integration, Product

NAMES = {Product.ROWANHOLT: "Rowanholt", Product.SHIKARI: "Shikari"}

# RFC 1918, and Tailscale's CGNAT range
_OWN = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
_OWN_SUFFIXES = (".ts.net", ".internal", ".lan", ".home.arpa", ".localhost")


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def on_own_network(url: str) -> bool:
    """Whether a server's URL is on the organisation's own network, for the TLP guard."""
    host = (urlsplit(url.strip()).hostname or "").lower()
    if host == "localhost" or host.endswith(_OWN_SUFFIXES):
        return True
    address = _address(host)
    return address is not None and (address.is_loopback or any(address in n for n in _OWN))


def url_problem(url: str) -> str | None:
    """Why a sibling server can't be reached at this URL, or None: https, or http to this machine or
    a private network. Never link-local (cloud metadata), and nothing but scheme, host, port and path."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return "The URL must start with https://."
    if parts.username or parts.password or parts.query or parts.fragment:
        return "Give the server's address only, without a user name, query or fragment."
    address = _address(host)
    if address is not None and address.is_link_local:
        return "Ostraka doesn't connect to link-local addresses."
    if parts.scheme == "http" and not on_own_network(url):
        return "Use https://. Plain http:// is only allowed to this machine or a private network."
    return None


def base_url(url: str) -> str:
    return url.strip().rstrip("/")


def token_of(row: Integration) -> str:
    return decrypt(row.token) if row.token else ""


async def configured(db: AsyncSession, product: Product) -> Integration | None:
    """The server for `product`, if an admin connected one and it's switched on."""
    row = await db.get(Integration, product)
    return row if row is not None and row.enabled and row.url else None


def tlp_refusal(product: Product, incident: Incident, row: Integration) -> str | None:
    """Why this incident mustn't exchange anything with this server, or None."""
    if notify.restricted(incident) and not on_own_network(row.url):
        return (
            f"{incident.ref} is TLP:{incident.tlp.value.upper()}, so Ostraka only exchanges its work with "
            f"a {NAMES[product]} server on your own network, and this one isn't."
        )
    return None
