"""Request and response shapes for the licence, report brands and webhooks."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from ostraka.licence import Edition
from ostraka.reports.theme import HEX


class LicenceText(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class LicenceIn(LicenceText):
    accept_terms: bool = False
    terms_version: str = Field(default="", max_length=16)  # the version the admin was shown


class LicencePreview(BaseModel):
    """What a pasted licence says, before it's installed."""

    licence_id: str
    licensee: str
    edition: Edition
    seats: int | None
    features: list[str]
    not_before: date | None
    expires: date | None
    state: str


class TermsOut(BaseModel):
    version: str
    text: str


class Acceptance(BaseModel):
    by: str
    at: datetime
    terms_version: str


class LicenceOut(BaseModel):
    edition: Edition
    state: str  # free, pending, active, grace, lapsed
    organisation: str  # the licensee
    seats: int | None  # None: unlimited
    features: list[str]
    places_held: int | None  # admins and team leads only
    not_before: date | None
    expires: date | None
    grace_ends: date | None
    custom_branding: bool
    brand_library: bool
    terms_version: str  # the version an install accepts now
    accepted: Acceptance | None  # for the installed licence, if recorded


def _colour(value: str | None) -> str | None:
    if value in (None, ""):
        return value
    if not HEX.match(value):
        raise ValueError("Give a colour as #rrggbb, for example #1f6feb.")
    return value.lower()


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    primary: str
    secondary: str = ""
    prepared_for: str = Field(default="", max_length=160)

    _colours = field_validator("primary", "secondary")(_colour)


class BrandPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=80)
    primary: str | None = None
    secondary: str | None = None
    prepared_for: str | None = Field(default=None, max_length=160)
    is_default: bool | None = None

    _colours = field_validator("primary", "secondary")(_colour)


class BrandOut(BaseModel):
    id: uuid.UUID
    name: str
    primary: str
    secondary: str
    prepared_for: str
    is_default: bool
    logos: list[str]  # the slots with a logo: cover, header, client
    palette: dict[str, str]  # the derived colours a report uses, as #rrggbb
    notes: list[str]  # colours that were adjusted to stay readable
    updated_at: datetime
    version: int


class WebhookIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(min_length=1)
    description: str = Field(default="", max_length=200)
    secret: str | None = Field(default=None, min_length=16, max_length=200)  # generated when not given


class WebhookPatch(BaseModel):
    version: int
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    events: list[str] | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class DeliveryOut(BaseModel):
    event: str
    at: datetime
    ok: bool
    status: int | None
    error: str
    tries: int


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    description: str
    enabled: bool
    has_secret: bool
    created_at: datetime
    version: int
    deliveries: list[DeliveryOut]
    secret: str | None = None  # only in the answer to creating it


class WebhooksOut(BaseModel):
    events: list[str]
    sending: bool  # False: the edition doesn't include automation, so nothing is sent
    webhooks: list[WebhookOut]
