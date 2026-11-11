"""Calling a sibling server's API: short timeouts, no redirects, and failures as two kinds of error
an incident page can show (it couldn't be reached; it answered no)."""

from __future__ import annotations

from typing import Any

import httpx

from ostraka import __version__
from ostraka.log import get_logger

log = get_logger(__name__)

TIMEOUT = httpx.Timeout(10.0, connect=3.0)
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests put an httpx.MockTransport here
MAX_BODY = 8 * 1024 * 1024


class Unreachable(Exception):
    """The server didn't answer: down, a wrong address, or too slow."""


class Refused(Exception):
    """The server answered with an error."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        for key in ("detail", "error"):
            if isinstance(body.get(key), str):
                return body[key][:200]
    return ""


async def get_json(name: str, base: str, token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    """GET `path` from `base` as the token's owner and return the JSON. Raises Unreachable or Refused."""
    headers = {"Accept": "application/json", "User-Agent": f"ostraka/{__version__}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            base_url=base, timeout=TIMEOUT, follow_redirects=False, transport=TRANSPORT
        ) as client:
            response = await client.get(path, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise Unreachable(f"{name} didn't answer in time.") from exc
    except httpx.HTTPError as exc:
        log.info("%s unreachable: %s", name, exc)
        raise Unreachable(f"{name} can't be reached at {base}.") from exc
    if response.status_code == 401:
        raise Refused(401, f"{name} refused the API token. Check it hasn't expired or been revoked.")
    if response.status_code == 403:
        raise Refused(403, _detail(response) or f"{name}'s token isn't allowed to do that.")
    if response.status_code >= 300:
        raise Refused(response.status_code, _detail(response) or f"{name} answered {response.status_code}.")
    if len(response.content) > MAX_BODY:
        raise Refused(502, f"{name} sent more than Ostraka will read in one answer.")
    try:
        return response.json()
    except ValueError as exc:
        raise Refused(502, f"{name} didn't answer with JSON. Is that the right address?") from exc
