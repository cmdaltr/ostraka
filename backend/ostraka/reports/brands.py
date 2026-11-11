"""Report brands: cleaning an uploaded logo, and the theme an incident's report is drawn in.

An uploaded logo is untrusted, so it's never stored or served as sent. Pillow reads only PNG and JPEG
(no SVG, which can carry script and external references), refuses anything with more pixels than a
logo needs before decoding it, and the result is drawn afresh as a PNG at most LOGO_MAX pixels a
side. Metadata, colour profiles and anything appended to the file are left behind.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import licence
from ostraka.models import Incident, ReportBrand
from ostraka.reports.theme import OSTRAKON_THEME, Theme, derive

SLOTS = ("cover", "header", "client")
UPLOAD_LIMIT = 2 * 1024 * 1024  # bytes
LOGO_MAX = 1200  # pixels a side, stored
PIXEL_LIMIT = 4096 * 4096  # refused before decoding: a decompression bomb is small on disk


class LogoProblem(ValueError):
    """An upload that isn't usable as a logo, with a reason to show."""


def clean_logo(data: bytes) -> bytes:
    """The upload redrawn as a PNG with a transparent ground, or LogoProblem."""
    if len(data) > UPLOAD_LIMIT:
        raise LogoProblem("That file is over 2 MB. A logo needs far less: export it smaller.")
    try:
        img = Image.open(io.BytesIO(data), formats=["PNG", "JPEG"])
    except (UnidentifiedImageError, OSError) as exc:
        raise LogoProblem("Upload a PNG or JPEG. SVG and other formats aren't accepted.") from exc
    if img.width * img.height > PIXEL_LIMIT:
        raise LogoProblem("That image is too large: keep a logo under 4096 pixels a side.")
    try:
        img.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise LogoProblem("That image couldn't be read. Export it again as a PNG.") from exc
    img = img.convert("RGBA")
    if box := img.getbbox():  # trim transparent margins so sizing on the page is predictable
        img = img.crop(box)
    img.thumbnail((LOGO_MAX, LOGO_MAX), Image.LANCZOS)
    out = io.BytesIO()
    Image.frombytes("RGBA", img.size, img.tobytes()).save(out, format="PNG", optimize=True)
    return out.getvalue()


def theme_of(brand: ReportBrand, licensed_to: str = "") -> Theme:
    palette, notes = derive(brand.primary, brand.secondary)
    return Theme(
        palette=palette,
        cover_logo=brand.cover_logo,
        header_logo=brand.header_logo,
        client_logo=brand.client_logo,
        licensed_to=licensed_to,
        ostrakon=False,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class Styling:
    """How an incident's report is dressed: the theme, and who the cover says it's prepared for
    (empty to keep the installation's own)."""

    theme: Theme
    prepared_for: str = ""
    brand: str = ""  # the brand's name, for the export's audit entry


async def styling_for(db: AsyncSession, incident: Incident) -> Styling:
    """The incident's own brand under Services, else the installation's default brand under Pro or
    Services, else Ostrakon."""
    standing = await licence.current(db)
    if not standing.custom_branding:
        return Styling(OSTRAKON_THEME)
    brand = None
    if standing.brand_library and incident.brand_id:
        brand = await db.get(ReportBrand, incident.brand_id)
    if brand is None:
        brand = await db.scalar(select(ReportBrand).where(ReportBrand.is_default.is_(True)))
    if brand is None:
        return Styling(OSTRAKON_THEME)
    return Styling(theme_of(brand, standing.organisation), brand.prepared_for, brand.name)
