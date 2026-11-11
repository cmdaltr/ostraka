"""Shikari's HTTP API and webhooks, as Shikari defines them.

Coverage is `GET /api/coverage?techniques=T1059,T1003.001` (what Shikari's MCP `technique_coverage`
tool calls): each technique comes back `covered` when one of Shikari's generated detections names
it. Sub-techniques count only for themselves, as Shikari counts them.

Shikari's webhooks (`shikari/webhooks.py`) are `{"event", "sent_at", "data"}` with `X-Shikari-Event`
and `X-Shikari-Signature: sha256=<hex HMAC-SHA256 of the raw body>`. Shikari has no
`detection.created` event: `run.completed` is the one that means new detections may exist, so it
makes Ostraka ask the coverage API again. A webhook never closes a gap by itself saying so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ostraka.integrations import base_url, token_of
from ostraka.integrations.http import get_json
from ostraka.integrations.signing import headers_for, sign, signature_ok
from ostraka.models import Integration

NAME = "Shikari"
EVENT, SIGNATURE = headers_for("shikari")
RECHECK_EVENTS = frozenset({"run.completed"})
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

    def coverage_link(self) -> str:
        return f"{self.url}/coverage"
