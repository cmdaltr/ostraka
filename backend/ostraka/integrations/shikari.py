"""Shikari's HTTP API and webhooks, as Shikari defines them.

Coverage is `GET /api/coverage?techniques=T1059,T1003.001` (what Shikari's MCP `technique_coverage`
tool calls): each technique comes back `covered` when one of Shikari's generated detections names
it. Sub-techniques count only for themselves, as Shikari counts them.

Shikari's webhooks (`shikari/webhooks.py`) are `{"event", "sent_at", "data"}` with `X-Shikari-Event`
and `X-Shikari-Signature: sha256=<hex HMAC-SHA256 of the raw body>`. Shikari has no
`detection.created` event: `run.completed` is the one that means new detections may exist, so it
makes Ostraka ask the coverage API again. A webhook never closes a gap by itself saying so. Checking
the signature and reading the body are samarkand's (`samarkand.webhooks.verify` and `parse`).

Hunt runs started from a record (`POST /api/runs`) have a stable id; `run.completed`, `run.failed`
and `run.executed` carry it, and Ostraka reads the run with `GET /api/runs/{id}`: its detections and,
once its queries have run, a count per query, never a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from samarkand.webhooks import headers_for, sign
from samarkand.webhooks import verify as signature_ok

from ostraka.integrations import base_url, token_of
from ostraka.integrations.http import get_json, post_json
from ostraka.models import Integration

NAME = "Shikari"
EVENT, SIGNATURE = headers_for("X-Shikari")
RECHECK_EVENTS = frozenset({"run.completed"})
RUN_EVENTS = frozenset({"run.completed", "run.failed", "run.executed"})
KINDS = ("group", "cve", "techniques")
FINISHED = frozenset({"executed", "failed"})  # a run that executes queries finishes on executed
CHUNK = 100
__all__ = ["EVENT", "SIGNATURE", "Covered", "Shikari", "sign", "signature_ok"]


@dataclass(frozen=True)
class Covered:
    covered: bool
    name: str


@dataclass(frozen=True)
class Shikari:
    url: str
    token: str

    @classmethod
    def of(cls, row: Integration) -> Shikari:
        return cls(base_url(row.url), token_of(row))

    async def _get(self, endpoint: str, **params: Any) -> Any:
        return await get_json(NAME, self.url, self.token, endpoint, params or None)

    async def health(self) -> dict:
        return await self._get("/health")

    async def coverage(self, techniques: list[str]) -> dict[str, Covered]:
        """Whether Shikari has a detection for each technique. A technique Shikari didn't answer
        for is left out, so it's treated as not covered."""
        out: dict[str, Covered] = {}
        wanted = sorted({t.upper() for t in techniques})
        for i in range(0, len(wanted), CHUNK):
            answer = await self._get("/api/coverage", techniques=",".join(wanted[i : i + CHUNK]))
            for item in answer.get("techniques", []) if isinstance(answer, dict) else []:
                tid = str(item.get("technique_id") or "").upper()
                if tid:
                    out[tid] = Covered(item.get("covered") is True, str(item.get("name") or "")[:200])
        return out

    async def start_run(
        self, kind: str, target: str, external_ref: str, *, execute: bool, window_days: int
    ) -> dict:
        """Start a hunt run for an Ostraka record (`POST /api/runs`): the `ostraka` scope or a full token."""
        body = {
            "kind": kind,
            "target": target,
            "external_ref": external_ref,
            "execute": execute,
            "window_days": window_days,
        }
        return await post_json(NAME, self.url, self.token, "/api/runs", body)

    async def run(self, run_id: str) -> dict:
        """A hunt run's status, detections and counts. Shikari never returns result rows."""
        return await self._get(f"/api/runs/{quote(run_id, safe='')}")

    def run_link(self, group_name: str) -> str:
        return f"{self.url}/results/{quote(group_name, safe='')}" if group_name else self.url

    def coverage_link(self) -> str:
        return f"{self.url}/coverage"
