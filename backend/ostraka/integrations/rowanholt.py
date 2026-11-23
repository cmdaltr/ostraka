"""Rowanholt's HTTP API, as its own clients use it (Smith's MCP server, `src/smith_mcp/server.py`).

A Rowanholt "case" is a job (`/api/jobs/{id}`), and several jobs can share a case number
(`/api/validate/case-id/{case}`). A row is cited as (host, artefact path, row reference, row hash):
the reference is the line number for JSON Lines and CSV, `#<index>` for a JSON array, and the hash is
SHA-256 over the row's canonical JSON. A Rowanholt finding is `lead`, `confirmed` or `benign`; in
Ostraka every one of them arrives as a lead.

Pairing (`integrations.pairing`) gives Ostraka a token with Rowanholt's `ostraka` scope: it reads cases
and starts jobs (`POST /api/jobs` with the record's reference as `external_ref`), and nothing else. A
read-only `smith-mcp` token (`aule-mcp` and `eru-mcp` before it) still works for everything but starting
jobs. Rowanholt's UI has no address for a file or a row, so a deep link opens the
job's case browser; the host, path and row ride along as query parameters for the day the case
browser reads them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from samarkand.webhooks import headers_for

from ostraka.integrations import base_url, token_of
from ostraka.integrations.http import get_json, post_json
from ostraka.models import Integration

NAME = "Rowanholt"
# Rowanholt's webhooks (`webhooks.py` there): signed as Shikari's are, with its own header names
EVENT, SIGNATURE = headers_for("X-Rowanholt")
JOB_EVENTS = frozenset({"job.completed", "job.failed"})
FINDING_EVENTS = frozenset({"finding.confirmed"})
# The analysis switches Ostraka offers when it starts a job (Rowanholt's AnalysisOptions); the rest keep
# Rowanholt's defaults
OPTIONS = ("analysis", "extract_iocs", "timeline", "memory", "yara", "vss", "navigator", "threat_intel")
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

    async def start_job(
        self, case_number: str, source_paths: list[str], options: dict[str, bool], external_ref: str
    ) -> dict:
        """Start a job on evidence already on the Rowanholt server, for an Ostraka record. Needs a token
        with the `ostraka` scope (or a full one)."""
        body = {
            "case_number": case_number,
            "source_paths": source_paths,
            "options": {k: bool(v) for k, v in options.items() if k in OPTIONS},
            "external_ref": external_ref,
        }
        return await post_json(NAME, self.url, self.token, "/api/jobs", body)

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
