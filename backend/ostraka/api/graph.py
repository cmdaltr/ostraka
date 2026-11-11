"""The incident graph (`ostraka/graph.py`): nodes and edges, masked for the viewer."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import graph
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.evidence_schemas import GraphOut
from ostraka.incidents import access
from ostraka.models import User

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["graph"])


@router.get("/graph", response_model=GraphOut)
async def incident_graph(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> GraphOut:
    """Who can open the incident can read its graph; observers get indicators masked. `restricted`
    says the record is TLP:RED or TLP:AMBER+STRICT, so anything exporting it outward keeps it in."""
    incident = await access.load(db, user, incident_id)
    return await graph.incident_graph(db, user, incident)
