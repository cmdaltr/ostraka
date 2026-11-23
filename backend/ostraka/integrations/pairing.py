"""Pairing: a Rowanholt or Shikari admin makes a one-use link, and an Ostraka admin pastes it here.

The link is the sibling's public origin with a code after `#` (`https://rowanholt.example.org/pair#pair_…`),
as in docs/suite/INTEGRATION.md. Ostraka asks the origin's health endpoint which product it is, then
claims the code there with the URL that product's webhooks should reach. The claim answers with an API
token in the sibling's `ostraka` scope and the secret its webhooks will be signed with; the sibling
created both in the same step. Nothing about Ostraka's records goes with the claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ostraka import __version__
from ostraka.integrations import NAMES, url_problem
from ostraka.integrations.http import Refused, Unreachable, get_json, post_json
from ostraka.models import Product

CODE = re.compile(r"^pair_[A-Za-z0-9_-]{20,100}$")
EVENTS = {
    Product.ROWANHOLT: ("job.completed", "job.failed", "finding.confirmed"),
    Product.SHIKARI: ("run.completed", "run.failed", "run.executed"),
}
NOT_A_LINK = "Paste the whole pairing link from Rowanholt or Shikari: its address, # and the code after it."


class PairingError(Exception):
    """Why pairing didn't happen, in words an admin can act on."""


@dataclass(frozen=True)
class Link:
    origin: str
    code: str


@dataclass(frozen=True)
class Paired:
    product: Product
    origin: str
    token: str
    webhook_secret: str
    version: str
    events: list[str]


def parse(link: str) -> Link:
    """The sibling's origin and the code from a pasted pairing link. Pure."""
    parts = urlsplit(link.strip())
    code = parts.fragment.strip()
    if not parts.scheme or not parts.hostname or not CODE.match(code):
        raise PairingError(NOT_A_LINK)
    origin = f"{parts.scheme}://{parts.netloc}"
    if problem := url_problem(origin):
        raise PairingError(problem)
    return Link(origin, code)


def identify(health: object, *, rowanholt: bool) -> Product | None:
    """Which product answered a health check: Shikari says `app: shikari` at /health, Rowanholt
    `status: healthy` at /api/health. Pure."""
    if not isinstance(health, dict):
        return None
    if not rowanholt and health.get("app") == "shikari":
        return Product.SHIKARI
    if rowanholt and health.get("status") == "healthy":
        return Product.ROWANHOLT
    return None


async def which(origin: str) -> Product:
    """Ask the origin which of the two it is."""
    for path, is_rowanholt in (("/health", False), ("/api/health", True)):
        try:
            found = identify(await get_json("The server", origin, "", path), rowanholt=is_rowanholt)
        except (Unreachable, Refused):  # not this one; the other path may answer
            continue
        if found is not None:
            return found
    raise PairingError(f"{origin} doesn't answer as Rowanholt or Shikari. Check the link's address.")


async def claim(link: Link, product: Product, webhook_url: str) -> Paired:
    """Claim the code. The sibling makes the token and the webhook; a code works once."""
    name = NAMES[product]
    try:
        answer = await post_json(
            name,
            link.origin,
            "",
            "/api/pairing/claim",
            {
                "code": link.code,
                "product": "ostraka",
                "name": "Ostraka",
                "version": __version__,
                "webhook_url": webhook_url,
            },
        )
    except Refused as exc:
        if exc.status in (404, 410, 422):
            raise PairingError(str(exc)) from exc
        raise
    if not isinstance(answer, dict) or answer.get("product") != product.value or not answer.get("token"):
        raise PairingError(f"{name} didn't answer the pairing as the suite's contract says.")
    return Paired(
        product=product,
        origin=link.origin,
        token=str(answer["token"]),
        webhook_secret=str(answer.get("webhook_secret") or ""),
        version=str(answer.get("version") or ""),
        events=[str(e) for e in answer.get("events") or []],
    )
