"""The installation's MISP instance, for pushing an incident as a MISP event (`ostraka.exports.misp`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ostraka.db import Base
from ostraka.models.people import utcnow


class MispSettings(Base):
    """One row at most: where pushes go. The API key is encrypted like a webhook's secret. Kept on
    every edition; only an edition with `automation` pushes."""

    __tablename__ = "misp_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        CheckConstraint("distribution BETWEEN 0 AND 3", name="distribution"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    url: Mapped[str] = mapped_column(String(2000))
    api_key: Mapped[str] = mapped_column(Text)  # encrypted
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    distribution: Mapped[int] = mapped_column(Integer, default=0)  # MISP's: 0 your organisation only
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
