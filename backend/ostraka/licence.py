"""Which edition this installation runs, from a licence signed with ostraka's private key.

    Free       4 people who can sign in, one of them an admin or team lead; reports in Ostrakon
    Pro        the licence's seats; one report brand for the whole installation; webhooks
    Services   Pro, plus a brand per client, chosen per incident, and the right to run ostraka for
               clients as a managed service, which the Elastic License 2.0 otherwise forbids
               (COMMERCIAL-LICENSE.md)

The line, its signature, editions, features, validity and grace are samarkand's (`samarkand.licence`),
the format shared by ostraka, Shikari and Rowanholt (docs/LICENSING_MODEL.md). What is Ostraka's own
is here: its public keys, its free seats, where the installed licence is kept and the commercial
terms an admin accepts. Only public keys ship, so a licence is checked offline and can't be forged
or edited; the private half stays with the licensor (`scripts/admin/sign_licence.py`). Rotating it
means adding the new public half to PUBLIC_KEYS.

What a licence grants is its edition's features (EDITION_FEATURES) plus any it names; "*" grants
every feature. Features are checked where they're used, never at configuration time.

Installing a licence needs an admin to accept the commercial terms (COMMERCIAL-LICENSE.md, a copy
shipped in ostraka/legal/) on the licensee's behalf; the version accepted, who and when are kept
with the installed licence. A new version of the terms is accepted when the next key is installed.

When a licence runs out, its edition carries on for GRACE with a warning, then the installation is
Free again. Nothing is taken away: accounts stay active and brands stay stored, but no new account
can be added past the free limit and reports go back to Ostrakon. An incident team mustn't be locked
out mid-incident because a renewal was late. A licence with no expiry is perpetual.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, fields
from datetime import datetime
from functools import cache, lru_cache
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from samarkand import licence as shared
from samarkand.licence import (  # noqa: F401  (re-exported: the rest of Ostraka imports them from here)
    AUTOMATION,
    BRAND_LIBRARY,
    EDITION_FEATURES,
    FEATURES,
    GRACE,
    MANAGED_SERVICE,
    REPORT_BRANDING,
    WILDCARD,
    Edition,
    LicenceProblem,
    Standing,
    check_installable,
    grace_ends,
    head_of,
    line_of,
    payload_of,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.models import InstalledLicence, User, utcnow

PRODUCT = "ostraka"
NAME = "Ostraka"
FORMAT = shared.format_of(PRODUCT)
FREE_SEATS = 4
TERMS = Path(__file__).parent / "legal" / "COMMERCIAL-LICENSE.md"
CODE_LICENCE = Path(__file__).parent / "legal" / "LICENSE"  # the Elastic License 2.0, as in the repo root
# Raw Ed25519 public keys, hex
PUBLIC_KEYS: tuple[str, ...] = ("cf9e0496426f783c79180af19dd77d40d2b4ea7d7104191784c48b8d1a40cb1f",)


@dataclass(frozen=True)
class Licence(shared.Licence):
    """samarkand's licence, naming ostraka unless told otherwise."""

    product: str = PRODUCT


@lru_cache(maxsize=8)
def _rules(public_keys: tuple[str, ...], free_seats: int | None) -> shared.Rules:
    return shared.Rules(PRODUCT, public_keys, free_seats, name=NAME)


def rules() -> shared.Rules:
    """Ostraka's licence rules: its id, PUBLIC_KEYS and FREE_SEATS, read when called so a test (or a
    key rotation) that changes them is honoured."""
    return _rules(tuple(PUBLIC_KEYS), FREE_SEATS)


def _ours(lic: shared.Licence) -> Licence:
    return lic if isinstance(lic, Licence) else Licence(**{f.name: getattr(lic, f.name) for f in fields(lic)})


def sign(private_key: Ed25519PrivateKey, licence: shared.Licence) -> str:
    """The licence as a line to send to the customer, as the licensor's admin script makes it."""
    head = head_of(licence)
    return line_of(head, private_key.sign(head.encode()))


def parse(text: str) -> Licence:
    """A licence, once its signature checks out against one of PUBLIC_KEYS. Raises LicenceProblem."""
    return _ours(rules().parse(text))


def standing(licence: shared.Licence | None, now: datetime | None = None) -> Standing:
    """The edition a licence (or none) gives at `now`. A licence runs from the start of its
    not_before day to the end of its expiry day."""
    return rules().standing(licence, now)


async def installed(db: AsyncSession) -> InstalledLicence | None:
    return await db.scalar(
        select(InstalledLicence)
        .where(InstalledLicence.removed_at.is_(None))
        .order_by(InstalledLicence.installed_at.desc())
        .limit(1)
    )


async def current(db: AsyncSession) -> Standing:
    """The installation's standing from its installed licence. A stored licence that no longer
    verifies (say, after a key was withdrawn) counts as none."""
    row = await installed(db)
    return rules().current(row.text if row else None, utcnow())


@cache
def terms() -> tuple[str, str]:
    """The commercial terms an admin accepts: (version, text). The version is the one the text states."""
    text = TERMS.read_text()
    found = re.search(r"\*\*Version (\S+)\*\*", text)
    if not found:
        raise RuntimeError(f"{TERMS} doesn't state its version")
    return found[1], text


@cache
def code_licence() -> str:
    """The Elastic License 2.0 text Ostraka is distributed under, for Help → Licence."""
    return CODE_LICENCE.read_text()


async def record_install(
    db: AsyncSession, text: str, *, accepted_by: User | None, accepted_by_name: str
) -> InstalledLicence:
    """Put a licence line (already parsed) in place of the current one, with who accepted which
    version of the terms. The caller audits and commits."""
    now = utcnow()
    if old := await installed(db):
        old.removed_at = now
    row = InstalledLicence(
        text=text.strip(),
        installed_by=accepted_by.id if accepted_by else None,
        installed_at=now,
        terms_version=terms()[0],
        accepted_by=accepted_by.id if accepted_by else None,
        accepted_by_name=accepted_by_name,
        accepted_at=now,
    )
    db.add(row)
    return row


def new_id() -> str:
    return f"lic_{uuid.uuid4().hex[:12]}"
