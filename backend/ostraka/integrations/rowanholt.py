"""Rowanholt's HTTP API, as its own clients use it (Smith's MCP server, `src/smith_mcp/server.py`).

A Rowanholt "case" is a job (`/api/jobs/{id}`), and several jobs can share a case number
(`/api/validate/case-id/{case}`). A row is cited as (host, artefact path, row reference, row hash):
the reference is the line number for JSON Lines and CSV, `#<index>` for a JSON array, and the hash is
SHA-256 over the row's canonical JSON. A Rowanholt finding is `lead`, `confirmed` or `benign`; in
Ostraka every one of them arrives as a lead.

The best token is Rowanholt's read-only `smith-mcp` scope (`aule-mcp` and `eru-mcp` before it):
Ostraka only ever reads. Rowanholt's UI has no address for a file or a row, so a deep link opens the
job's case browser; the host, path and row ride along as query parameters for the day the case
browser reads them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from ostraka.integrations import base_url, token_of
from ostraka.integrations.http import get_json
from ostraka.models import Integration

NAME = "Rowanholt"
STATUSES = (
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "archived",
    "awaiting_confirmation",
)
FINDING_STATUSES = ("lead", "confirmed", "benign")
CASE_HOST = "(case)"
JOB_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_WINDOW = 60


def cite(host: str, artefact: str, row_ref: str, row_hash: str) -> str:
    """A row's citation, written as Smith writes it."""
    return f"{host}: {artefact} row {row_ref}" + (f" (sha256 {row_hash[:16]})" if row_hash else "")


@dataclass(frozen=True)
class Rowanholt:
    url: str
    token: str

    @classmethod
    def of(cls, row: Integration) -> Rowanholt:
        return cls(base_url(row.url), token_of(row))

    async def _get(self, endpoint: str, **params: Any) -> Any:
        return await get_json(NAME, self.url, self.token, endpoint, params or None)

    async def health(self) -> dict:
        return await self._get("/api/health")

    async def me(self) -> dict:
        return await self._get("/api/auth/me")

    async def job(self, job_id: str) -> dict:
        return await self._get(f"/api/jobs/{quote(job_id, safe='')}", log_limit=0)

    async def case_jobs(self, case_number: str) -> dict:
        return await self._get(f"/api/validate/case-id/{quote(case_number, safe='')}")

    async def case(self, job_id: str) -> dict:
        return await self._get(f"/api/jobs/{quote(job_id, safe='')}/case")

    async def rows(
        self, job_id: str, host: str, path: str, q: str = "", offset: int = 0, limit: int = 50
    ) -> dict:
        return await self._get(
            f"/api/jobs/{quote(job_id, safe='')}/case/rows",
            host=host,
            path=path,
            q=q,
            offset=offset,
            limit=limit,
        )

    async def row(self, job_id: str, host: str, path: str, row_ref: str) -> dict | None:
        """One row by its reference, or None. Rowanholt reads rows by page, so this reads the page
        around where the reference falls (a line number is close to the row's position)."""
        digits = row_ref.lstrip("#")
        start = max(0, int(digits) - _WINDOW // 2) if digits.isdigit() else 0
        page = await self.rows(job_id, host, path, offset=start, limit=_WINDOW)
        return next((r for r in page.get("rows", []) if str(r.get("ref")) == row_ref), None)

    async def findings(self, job_id: str) -> list[dict]:
        found = await self._get(f"/api/jobs/{quote(job_id, safe='')}/case/findings", verify="true")
        return found if isinstance(found, list) else []

    def job_link(self, job_id: str) -> str:
        return f"{self.url}/jobs/{quote(job_id, safe='')}/case"

    def row_link(self, job_id: str, host: str, artefact: str, row_ref: str) -> str:
        return f"{self.job_link(job_id)}?{urlencode({'host': host, 'path': artefact, 'row': row_ref})}"


def job_summary(job: dict) -> tuple[str, int | None]:
    status = str(job.get("status") or "")
    progress = job.get("progress")
    return status, int(progress) if isinstance(progress, int | float) else None


def case_status(jobs: list[dict]) -> str:
    """A case's status from its jobs: the newest job's."""
    if not jobs:
        return ""
    newest = max(jobs, key=lambda j: str(j.get("created_at") or ""))
    return str(newest.get("status") or "")
