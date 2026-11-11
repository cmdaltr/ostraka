"""The installation's licence and its report brands.

Anyone signed in may read the edition (the interface hides what it doesn't include) and the brands
(a Services incident's commander picks one). Only admins install a licence or change a brand, and each
change is in the security log. Brands are kept when a licence lapses, but can't be changed until
one is in force again.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import accounts, audit, licence
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents.views import check_version
from ostraka.installation_schemas import (
    Acceptance,
    BrandIn,
    BrandOut,
    BrandPatch,
    LicenceIn,
    LicenceOut,
    LicencePreview,
    LicenceText,
    TermsOut,
)
from ostraka.models import ReportBrand, Role, User, utcnow
from ostraka.reports.brands import UPLOAD_LIMIT, LogoProblem, clean_logo
from ostraka.reports.theme import derive, to_hex

router = APIRouter(prefix="/api", tags=["installation"])

Slot = Literal["cover", "header", "client"]


def _admin(user: User) -> None:
    if problem := policy.can_manage_licence(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)


async def _licence_out(db: AsyncSession, user: User) -> LicenceOut:
    standing = await licence.current(db)
    held = None
    if user.role in policy.LEADERS:
        held, _ = await accounts.places_held(db)
    lic = standing.licence
    row = await licence.installed(db) if lic else None
    accepted = (
        Acceptance(by=row.accepted_by_name, at=row.accepted_at, terms_version=row.terms_version)
        if row and row.accepted_at
        else None
    )
    return LicenceOut(
        edition=standing.edition,
        state=standing.state,
        organisation=lic.licensee if lic else "",
        seats=standing.seats,
        features=sorted(standing.features),
        places_held=held,
        not_before=lic.not_before if lic else None,
        expires=lic.expires if lic else None,
        grace_ends=licence.grace_ends(lic) if lic else None,
        custom_branding=standing.custom_branding,
        brand_library=standing.brand_library,
        terms_version=licence.terms()[0],
        accepted=accepted,
    )


@router.get("/licence", response_model=LicenceOut)
async def get_licence(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> LicenceOut:
    return await _licence_out(db, user)


@router.get("/licence/terms", response_model=TermsOut)
async def get_terms(user: User = Depends(current_user)) -> TermsOut:
    """The commercial terms an admin accepts when installing a licence."""
    version, text = licence.terms()
    return TermsOut(version=version, text=text)


def _parsed(text: str) -> licence.Licence:
    try:
        return licence.check_installable(licence.parse(text))
    except licence.LicenceProblem as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/licence/check", response_model=LicencePreview)
async def check_licence(body: LicenceText, user: User = Depends(current_user)) -> LicencePreview:
    """What a licence says, so the admin knows whom they're accepting the terms for. Installs nothing."""
    _admin(user)
    lic = _parsed(body.text)
    return LicencePreview(
        licence_id=lic.licence_id,
        licensee=lic.licensee,
        edition=lic.edition,
        seats=lic.seats,
        features=sorted(licence.EDITION_FEATURES[lic.edition] | set(lic.features)),
        not_before=lic.not_before,
        expires=lic.expires,
        state=licence.standing(lic).state,
    )


@router.put("/licence", response_model=LicenceOut)
async def install_licence(
    body: LicenceIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LicenceOut:
    """Install a licence. The admin must have accepted the current commercial terms on the
    licensee's behalf; who, when and which version are kept with it."""
    _admin(user)
    lic = _parsed(body.text)
    version = licence.terms()[0]
    if not body.accept_terms:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Accept the commercial licence terms for {lic.licensee} to install this licence.",
        )
    if body.terms_version != version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The commercial licence terms are now version {version}. Read them, then accept again.",
        )
    await licence.record_install(
        db, body.text, accepted_by=user, accepted_by_name=f"{user.name} <{user.email}>"
    )
    audit.record(
        db,
        "licence_installed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        licence=lic.licence_id,
        edition=lic.edition.value,
        licensee=lic.licensee,
        seats=lic.seats,
        expires=lic.expires.isoformat() if lic.expires else "perpetual",
        terms_version=version,
    )
    await db.commit()
    return await _licence_out(db, user)


@router.delete("/licence", response_model=LicenceOut)
async def remove_licence(
    request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> LicenceOut:
    """Back to the free edition. Nobody is deactivated; past the free limit, nobody new can join."""
    _admin(user)
    row = await licence.installed(db)
    if row is not None:
        row.removed_at = utcnow()
        audit.record(db, "licence_removed", actor_id=user.id, ip=sessions.client_ip(request))
        await db.commit()
    return await _licence_out(db, user)


# ── Report brands ─────────────────────────────────────────────────────────────


def _brand_out(brand: ReportBrand) -> BrandOut:
    palette, notes = derive(brand.primary, brand.secondary)
    return BrandOut(
        id=brand.id,
        name=brand.name,
        primary=brand.primary,
        secondary=brand.secondary,
        prepared_for=brand.prepared_for,
        is_default=brand.is_default,
        logos=[slot for slot in ("cover", "header", "client") if getattr(brand, f"{slot}_logo")],
        palette={name: to_hex(rgb) for name, rgb in vars(palette).items()},
        notes=notes,
        updated_at=brand.updated_at,
        version=brand.version,
    )


async def _writable_brand(
    db: AsyncSession, user: User, brand_id: uuid.UUID | None = None
) -> ReportBrand | None:
    """Refuse unless the actor is an admin and the licence includes custom branding; the brand, if
    one is named."""
    _admin(user)
    if not (await licence.current(db)).custom_branding:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED, "Report branding is part of the Pro and Services editions."
        )
    if brand_id is None:
        return None
    brand = await db.get(ReportBrand, brand_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report brand.")
    return brand


async def _name_free(db: AsyncSession, name: str, brand_id: uuid.UUID | None = None) -> None:
    query = select(ReportBrand.id).where(func.lower(ReportBrand.name) == name.strip().lower())
    if brand_id:
        query = query.where(ReportBrand.id != brand_id)
    if await db.scalar(query):
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already a brand with that name.")


@router.get("/brands", response_model=list[BrandOut])
async def list_brands(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[BrandOut]:
    """The default first, then by name. Observers don't export, so they don't need the list."""
    if user.role is Role.OBSERVER:
        return []
    rows = await db.scalars(select(ReportBrand).order_by(ReportBrand.is_default.desc(), ReportBrand.name))
    return [_brand_out(b) for b in rows]


@router.post("/brands", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    body: BrandIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> BrandOut:
    """Pro keeps one brand; Services keeps one per client. The first is the default."""
    await _writable_brand(db, user)
    count = await db.scalar(select(func.count()).select_from(ReportBrand)) or 0
    if count and not (await licence.current(db)).brand_library:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Pro has one report brand: change it instead. A brand per client is part of Services.",
        )
    await _name_free(db, body.name)
    brand = ReportBrand(
        name=body.name.strip(),
        primary=body.primary,
        secondary=body.secondary,
        prepared_for=body.prepared_for.strip(),
        is_default=count == 0,
        created_by=user.id,
    )
    db.add(brand)
    await db.flush()
    audit.record(db, "brand_created", actor_id=user.id, ip=sessions.client_ip(request), brand=brand.name)
    await db.commit()
    await db.refresh(brand)
    return _brand_out(brand)


@router.patch("/brands/{brand_id}", response_model=BrandOut)
async def change_brand(
    brand_id: uuid.UUID,
    body: BrandPatch,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> BrandOut:
    brand = await _writable_brand(db, user, brand_id)
    assert brand is not None
    check_version(brand.version, body.version, "brand")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if changes.get("name"):
        await _name_free(db, changes["name"], brand.id)
        changes["name"] = changes["name"].strip()
    if changes.get("primary") == "":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A brand needs a primary colour.")
    if changes.pop("is_default", None):
        await db.execute(update(ReportBrand).where(ReportBrand.id != brand.id).values(is_default=False))
        await db.flush()
        brand.is_default = True
        changes["is_default"] = True
    for field, value in changes.items():
        if value is not None:
            setattr(brand, field, value.strip() if isinstance(value, str) else value)
    brand.version += 1
    audit.record(
        db,
        "brand_changed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        brand=brand.name,
        fields=sorted(changes),
    )
    await db.commit()
    await db.refresh(brand)
    return _brand_out(brand)


@router.delete("/brands/{brand_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_brand(
    brand_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Incidents that used it go back to the default. If it was the default, the next by name is."""
    brand = await _writable_brand(db, user, brand_id)
    assert brand is not None
    was_default, name = brand.is_default, brand.name
    await db.delete(brand)
    await db.flush()
    if was_default:
        heir = await db.scalar(select(ReportBrand).order_by(ReportBrand.name).limit(1))
        if heir is not None:
            heir.is_default = True
    audit.record(db, "brand_deleted", actor_id=user.id, ip=sessions.client_ip(request), brand=name)
    await db.commit()


@router.put("/brands/{brand_id}/logos/{slot}", response_model=BrandOut)
async def upload_logo(
    brand_id: uuid.UUID,
    slot: Slot,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> BrandOut:
    """The request body is the image itself (PNG or JPEG), redrawn by ostraka before it's stored."""
    brand = await _writable_brand(db, user, brand_id)
    assert brand is not None
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > UPLOAD_LIMIT:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Keep a logo under 2 MB.")
    try:
        cleaned = clean_logo(bytes(data))
    except LogoProblem as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    setattr(brand, f"{slot}_logo", cleaned)
    brand.version += 1
    audit.record(
        db, "brand_logo_set", actor_id=user.id, ip=sessions.client_ip(request), brand=brand.name, slot=slot
    )
    await db.commit()
    await db.refresh(brand)
    return _brand_out(brand)


@router.delete("/brands/{brand_id}/logos/{slot}", response_model=BrandOut)
async def remove_logo(
    brand_id: uuid.UUID,
    slot: Slot,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> BrandOut:
    brand = await _writable_brand(db, user, brand_id)
    assert brand is not None
    setattr(brand, f"{slot}_logo", None)
    brand.version += 1
    audit.record(
        db,
        "brand_logo_removed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        brand=brand.name,
        slot=slot,
    )
    await db.commit()
    await db.refresh(brand)
    return _brand_out(brand)


@router.get("/brands/{brand_id}/logos/{slot}", response_class=Response)
async def get_logo(
    brand_id: uuid.UUID,
    slot: Slot,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    brand = await db.get(ReportBrand, brand_id) if user.role is not Role.OBSERVER else None
    logo = getattr(brand, f"{slot}_logo") if brand else None
    if not logo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such logo.")
    return Response(
        logo,
        media_type="image/png",
        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )
