"""Which edition this installation runs, from a licence signed with ostraka's private key.

    Free       4 people who can sign in, one of them an admin or team lead; reports in Ostrakon
    Pro        the licence's seats; one report brand for the whole installation; webhooks
    Services   Pro, plus a brand per client, chosen per incident, and the right to run ostraka for
               clients as a managed service, which the Elastic License 2.0 otherwise forbids
               (COMMERCIAL-LICENSE.md)

This follows the licence format shared by ostraka, Shikari and Rowanholt (docs/LICENSING_MODEL.md).
A licence is one line, `ostraka-licence-1.<payload>.<signature>`: the payload is base64url of
canonical JSON (sorted keys, compact), and the signature is Ed25519 over the ASCII of everything
before the last dot, prefix included, so a licence for another product or format version can't be
passed off as this one. Only public keys ship, so a licence is checked offline and can't be forged
or edited. Each product has its own key; the private half stays with the licensor
(`scripts/admin/sign_licence.py`). Rotating it means adding the new public half to PUBLIC_KEYS.

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

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.models import InstalledLicence, User, utcnow

PRODUCT = "ostraka"
FORMAT = f"{PRODUCT}-licence-1"
FREE_SEATS = 4
GRACE = timedelta(days=30)
# Raw Ed25519 public keys, hex
TERMS = Path(__file__).parent / "legal" / "COMMERCIAL-LICENSE.md"
CODE_LICENCE = Path(__file__).parent / "legal" / "LICENSE"  # the Elastic License 2.0, as in the repo root
PUBLIC_KEYS: tuple[str, ...] = ("cf9e0496426f783c79180af19dd77d40d2b4ea7d7104191784c48b8d1a40cb1f",)


class Edition(StrEnum):
    FREE = "free"
    PRO = "pro"
    SERVICES = "services"


# Feature ids: short, kebab-case, stable. docs/LICENSING_MODEL.md says what each gates.
REPORT_BRANDING = "report-branding"
BRAND_LIBRARY = "brand-library"
AUTOMATION = "automation"  # webhooks, checked when one is sent (ostraka.webhooks)
MANAGED_SERVICE = "managed-service"  # a right in COMMERCIAL-LICENSE.md, not a switch in the code
FEATURES = (REPORT_BRANDING, BRAND_LIBRARY, AUTOMATION, MANAGED_SERVICE)
WILDCARD = "*"
EDITION_FEATURES: dict[Edition, frozenset[str]] = {
    Edition.FREE: frozenset(),
    Edition.PRO: frozenset({REPORT_BRANDING, AUTOMATION}),
    Edition.SERVICES: frozenset({REPORT_BRANDING, BRAND_LIBRARY, AUTOMATION, MANAGED_SERVICE}),
}


class LicenceProblem(ValueError):
    """A licence that can't be used, with a reason an admin can act on."""


@dataclass(frozen=True)
class Licence:
    licence_id: str
    licensee: str
    edition: Edition
    seats: int | None  # None: unlimited
    issued: date
    expires: date | None  # None: perpetual
    email: str = ""
    features: tuple[str, ...] = ()  # beyond the edition's own
    not_before: date | None = None
    product: str = PRODUCT


@dataclass(frozen=True)
class Standing:
    """What the installation may do now."""

    edition: Edition
    seats: int | None  # None: unlimited
    state: str  # "free" (no licence), "pending" (before not_before), "active", "grace", "lapsed"
    licence: Licence | None = None
    features: frozenset[str] = field(default_factory=frozenset)

    def has(self, feature: str) -> bool:
        return feature in self.features or WILDCARD in self.features

    @property
    def custom_branding(self) -> bool:
        return self.has(REPORT_BRANDING)

    @property
    def brand_library(self) -> bool:
        return self.has(BRAND_LIBRARY)

    @property
    def organisation(self) -> str:
        return self.licence.licensee if self.licence and self.state in ("active", "grace") else ""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def payload_of(licence: Licence) -> dict:
    """The signed fields. Optional ones are left out rather than sent empty."""
    out: dict = {
        "licence_id": licence.licence_id,
        "product": licence.product,
        "licensee": licence.licensee,
        "edition": licence.edition.value,
        "seats": licence.seats,
        "issued": licence.issued.isoformat(),
        "expires": licence.expires.isoformat() if licence.expires else None,
    }
    if licence.email:
        out["email"] = licence.email
    if licence.features:
        out["features"] = sorted(licence.features)
    if licence.not_before:
        out["not_before"] = licence.not_before.isoformat()
    return out


def sign(private_key: Ed25519PrivateKey, licence: Licence) -> str:
    """The licence as a line to send to the customer."""
    canonical = json.dumps(payload_of(licence), sort_keys=True, separators=(",", ":"))
    head = f"{licence.product}-licence-1.{_b64(canonical.encode())}"
    return f"{head}.{_b64(private_key.sign(head.encode()))}"


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse(text: str) -> Licence:
    """A licence, once its signature checks out against one of PUBLIC_KEYS. Raises LicenceProblem."""
    parts = text.strip().split(".")
    if len(parts) != 3 or not parts[0].endswith("-licence-1"):
        raise LicenceProblem("That isn't an Ostraka licence. Paste the whole line you were sent.")
    if parts[0] != FORMAT:
        product = parts[0].removesuffix("-licence-1")
        raise LicenceProblem(f"That's a licence for {product}, not Ostraka.")
    head = f"{parts[0]}.{parts[1]}"
    try:
        signature = _unb64(parts[2])
        payload = json.loads(_unb64(parts[1]))
    except (binascii.Error, ValueError) as exc:
        raise LicenceProblem("That licence is damaged. Paste the whole line you were sent.") from exc
    for key in PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(signature, head.encode())
            break
        except InvalidSignature:
            continue
    else:
        raise LicenceProblem("That licence wasn't issued for Ostraka, or has been altered.")
    try:
        licence = Licence(
            licence_id=str(payload["licence_id"]),
            product=str(payload["product"]),
            licensee=str(payload["licensee"]),
            email=str(payload.get("email", "")),
            edition=Edition(payload["edition"]),
            seats=None if payload["seats"] is None else int(payload["seats"]),
            features=tuple(str(f) for f in payload.get("features", [])),
            issued=date.fromisoformat(payload["issued"]),
            not_before=_day(payload.get("not_before")),
            expires=_day(payload["expires"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise LicenceProblem(
            "That licence is from a newer version of Ostraka. Update, then try again."
        ) from exc
    if licence.product != PRODUCT:
        raise LicenceProblem(f"That's a licence for {licence.product}, not Ostraka.")
    if licence.edition is Edition.FREE or (licence.seats is not None and licence.seats < 1):
        raise LicenceProblem("That licence grants nothing beyond the free edition.")
    return licence


def standing(licence: Licence | None, now: datetime | None = None) -> Standing:
    """The edition a licence (or none) gives at `now`. A licence runs from the start of its
    not_before day to the end of its expiry day."""
    free = Standing(Edition.FREE, FREE_SEATS, "free")
    if licence is None:
        return free
    now = now or datetime.now(UTC)
    if licence.not_before and now < datetime.combine(licence.not_before, datetime.min.time(), UTC):
        return Standing(Edition.FREE, FREE_SEATS, "pending", licence)
    granted = EDITION_FEATURES[licence.edition] | frozenset(licence.features)
    paid = Standing(licence.edition, licence.seats, "active", licence, granted)
    if licence.expires is None:
        return paid
    ends = datetime.combine(licence.expires + timedelta(days=1), datetime.min.time(), UTC)
    if now < ends:
        return paid
    if now < ends + GRACE:
        return Standing(licence.edition, licence.seats, "grace", licence, granted)
    return Standing(Edition.FREE, FREE_SEATS, "lapsed", licence)


def grace_ends(licence: Licence) -> date | None:
    return licence.expires + GRACE if licence.expires else None


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
    if row is None:
        return standing(None)
    try:
        return standing(parse(row.text), utcnow())
    except LicenceProblem:
        return standing(None)


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
