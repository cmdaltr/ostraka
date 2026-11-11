"""The installation itself: its licence, the report brands it may use (`ostraka.licence`) and its
webhooks (`ostraka.webhooks`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from ostraka.db import Base
from ostraka.models.people import utcnow


class InstalledLicence(Base):
    """A licence an admin installed. The newest not removed is the one in force; older rows stay as
    a record of what the installation was licensed for and when."""

    __tablename__ = "licences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    text: Mapped[str] = mapped_column(Text)
    installed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who accepted the commercial terms on the licensee's behalf, and which version. The name is
    # kept as written so the record stands if the account goes; the CLI has only the name.
    terms_version: Mapped[str] = mapped_column(String(16), default="")
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_by_name: Mapped[str] = mapped_column(String(200), default="")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportBrand(Base):
    """Colours and up to three logos for reports. Logos are stored as PNG that ostraka re-encoded
    itself, never the uploaded file. Pro has one brand, the default; MSSP keeps a library, and an
    incident may name one (`Incident.brand_id`)."""

    __tablename__ = "report_brands"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    primary: Mapped[str] = mapped_column(String(7))  # "#1f6feb"
    secondary: Mapped[str] = mapped_column(String(7), default="")
    prepared_for: Mapped[str] = mapped_column(String(160), default="")  # the cover's "Prepared for"
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    cover_logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    header_logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        Index("uq_report_brands_one_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )


class Webhook(Base):
    """A URL ostraka POSTs to when something happens (`ostraka.webhooks`). Kept on every edition;
    only an edition with `automation` sends to it."""

    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(2000))
    events: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    secret: Mapped[str] = mapped_column(Text, default="")  # encrypted, like a team's Teams webhook
    description: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class WebhookDelivery(Base):
    """One attempt to deliver an event, kept for the admin page (the newest few per webhook)."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(String(200), default="")
    tries: Mapped[int] = mapped_column(Integer, default=0)
