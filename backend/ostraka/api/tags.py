"""Tags: the controlled vocabulary admins maintain, plus every free tag in use, for autocomplete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth.deps import current_user, require_role
from ostraka.db import get_session
from ostraka.incident_schemas import TAG
from ostraka.models import Asset, Finding, Incident, IncidentObservable, Role, TagDefinition, User

router = APIRouter(prefix="/api/tags", tags=["tags"])
admin_only = require_role(Role.ADMIN)


class TagOut(BaseModel):
    name: str
    description: str = ""
    controlled: bool


class TagDefinitionIn(BaseModel):
    name: str = Field(pattern=TAG.pattern)
    description: str = Field(default="", max_length=500)


@router.get("", response_model=list[TagOut])
async def list_tags(_: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> list[TagOut]:
    controlled = {t.name: t.description for t in (await db.scalars(select(TagDefinition))).all()}
    in_use = union(
        *(select(func.unnest(m.tags).label("tag")) for m in (Incident, IncidentObservable, Asset, Finding))
    )
    free = set((await db.scalars(select(in_use.subquery().c.tag))).all()) - set(controlled)
    return [TagOut(name=n, description=d, controlled=True) for n, d in sorted(controlled.items())] + [
        TagOut(name=n, controlled=False) for n in sorted(free)
    ]


@router.put("/{name}", response_model=TagOut)
async def define_tag(
    name: str,
    body: TagDefinitionIn,
    _: User = Depends(admin_only),
    db: AsyncSession = Depends(get_session),
) -> TagOut:
    if body.name != name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The name in the path and body differ.")
    tag = await db.get(TagDefinition, name) or TagDefinition(name=name)
    tag.description = body.description
    db.add(tag)
    await db.commit()
    return TagOut(name=tag.name, description=tag.description, controlled=True)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def undefine_tag(
    name: str, _: User = Depends(admin_only), db: AsyncSession = Depends(get_session)
) -> None:
    """Remove a tag from the controlled vocabulary. Records already tagged keep it as a free tag."""
    tag = await db.get(TagDefinition, name)
    if tag is not None:
        await db.delete(tag)
        await db.commit()
