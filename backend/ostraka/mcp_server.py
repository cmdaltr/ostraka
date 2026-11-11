"""ostraka as an MCP server, at /mcp over streamable HTTP.

Each request acts as the owner of its bearer token, and each tool calls ostraka's own API in-process
with that token, so access rules, IOC masking and the read-only scope apply exactly as they do in the
browser. A read-only (`mcp`) token is offered only the read tools; a full token also gets the two
that write: a comment, and a lead (never a confirmed finding, which only an analyst makes).

TLP:RED and TLP:AMBER+STRICT records don't reach an MCP client, whose model may be anyone's: they
are listed by reference only, the same rule as `notify.outward`.

  claude mcp add --transport http ostraka https://ostraka.example.org/mcp \\
    --header "Authorization: Bearer ost_..."
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from ostraka import __version__, branding
from ostraka.auth.deps import _from_api_token
from ostraka.db import sessions
from ostraka.log import get_logger
from ostraka.models import TokenScope

log = get_logger(__name__)

RESTRICTED = {"red", "amber+strict"}

INSTRUCTIONS = f"""{branding.NAME} is the record of an incident or a hunt: its people, tasks, findings, \
observables, assets, decisions and timeline. Records are named OST-YYYY-NNN (incidents) and \
HNT-YYYY-NNN (hunts); start from list_records, then get_record. Cite the reference (OST-…, FND-…, \
TSK-…, AST-…, OBS-…) for every claim, and don't state what no record shows. A lead is not a \
confirmed finding: only an analyst confirms one, after a verification, so add_lead never confirms. \
Records at TLP:RED or TLP:AMBER+STRICT are kept out of MCP clients: send the person to {branding.NAME} \
for those."""


class ToolError(Exception):
    """A message for the client, shown as the tool's error."""


class Api:
    """ostraka's own API, in-process, as the token's owner."""

    def __init__(self, app: Any, token: str) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ostraka.mcp",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, list):  # a validation error: say which field
                detail = "; ".join(
                    f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail
                )
            raise ToolError(detail or f"{branding.NAME} answered {response.status_code}.")
        return response.json() if response.content else None

    async def get(self, path: str, **params: Any) -> Any:
        return await self.call("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def record(self, ref: str) -> dict:
        """A record the client may see, by its reference."""
        record = await self.get(f"/api/incidents/by-ref/{ref.strip().lstrip('#').upper()}")
        if record["tlp"] in RESTRICTED:
            raise ToolError(
                f"{record['ref']} is TLP:{record['tlp'].upper()}: {branding.NAME} keeps it out of MCP "
                f"clients. Open it in {branding.NAME}."
            )
        return record

    async def close(self) -> None:
        await self.client.aclose()


def _brief(record: dict) -> dict:
    """A record as list_records shows it; a restricted one by reference only."""
    keys = ("ref", "kind", "severity", "tlp", "phase", "status")
    if record["tlp"] in RESTRICTED:
        return {k: record[k] for k in keys} | {
            "title": f"(TLP:{record['tlp'].upper()}: open {branding.NAME})"
        }
    return {k: record[k] for k in keys} | {
        "title": record["title"],
        "commander": (record.get("commander") or {}).get("name"),
        "teams": [t["name"] for t in record.get("teams", [])],
        "tags": record.get("tags", []),
        "updated_at": record.get("updated_at"),
    }


Handler = Callable[..., Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    properties: dict[str, dict]
    required: tuple[str, ...]
    run: Handler
    writes: bool = False

    def definition(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
            annotations=types.ToolAnnotations(
                readOnlyHint=not self.writes,
                destructiveHint=False,
                idempotentHint=not self.writes,
                openWorldHint=False,
            ),
        )


REF = {"type": "string", "description": "The record's reference, e.g. OST-2026-004 or HNT-2026-002"}


async def whoami(api: Api) -> Any:
    me = await api.get("/api/users/me")
    return {k: me.get(k) for k in ("name", "email", "role", "team")}


async def list_records(api: Api, status: str = "open", kind: str | None = None) -> Any:
    return [_brief(r) for r in await api.get("/api/incidents", status=status, kind=kind)]


async def get_record(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    links = await api.get(f"/api/incidents/{record['id']}/links")
    for group in links.values():
        if not isinstance(group, list):
            continue
        for linked in group:
            if isinstance(linked, dict) and "ref" in linked:
                try:
                    await api.record(linked["ref"])
                except ToolError:
                    linked["title"] = "(restricted, or not one you can see)"
    return record | {"links": links}


async def findings(api: Api, ref: str, status: str | None = None) -> Any:
    record = await api.record(ref)
    return await api.get(f"/api/incidents/{record['id']}/findings", status=status)


async def observables(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    return await api.get(f"/api/incidents/{record['id']}/observables")


async def assets(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    return {
        "assets": await api.get(f"/api/incidents/{record['id']}/assets"),
        "connections": await api.get(f"/api/incidents/{record['id']}/asset-links"),
    }


async def tasks(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    return {
        "tasks": await api.get(f"/api/incidents/{record['id']}/tasks"),
        "playbooks": await api.get(f"/api/incidents/{record['id']}/playbooks"),
    }


async def timeline(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    return await api.get(f"/api/incidents/{record['id']}/timeline")


async def comments(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    return await api.get(f"/api/incidents/{record['id']}/comments")


async def activity(api: Api, ref: str) -> Any:
    record = await api.record(ref)
    return {
        "chain": await api.get(f"/api/incidents/{record['id']}/activity/verify"),
        "events": await api.get(f"/api/incidents/{record['id']}/activity"),
    }


async def my_tasks(api: Api) -> Any:
    out = []
    for task in await api.get("/api/tasks/mine"):
        brief = task["incident"]
        if brief["tlp"] in RESTRICTED:
            out.append(
                {
                    "ref": task["ref"],
                    "status": task["status"],
                    "incident": brief["ref"],
                    "title": f"(TLP:{brief['tlp'].upper()}: open {branding.NAME})",
                }
            )
        else:
            out.append(task)
    return out


async def look_up(api: Api, ref: str) -> Any:
    found = await api.get(f"/api/refs/{ref.strip().lstrip('#').upper()}")
    await api.record(found["incident_ref"])
    return found


async def add_comment(api: Api, ref: str, body: str) -> Any:
    record = await api.record(ref)
    return await api.call("POST", f"/api/incidents/{record['id']}/comments", json={"body": body})


async def add_lead(
    api: Api,
    ref: str,
    kind: str,
    title: str,
    statement: str = "",
    techniques: list[str] | None = None,
    source_reliability: str | None = None,
    information_credibility: str | None = None,
) -> Any:
    record = await api.record(ref)
    body = {
        "kind": kind,
        "title": title,
        "statement": statement,
        "techniques": techniques or [],
        "source_reliability": source_reliability,
        "information_credibility": information_credibility,
    }
    return await api.call("POST", f"/api/incidents/{record['id']}/findings", json=body)


TOOLS = [
    Tool("whoami", "Who the token belongs to: name, role and team.", {}, (), whoami),
    Tool(
        "list_records",
        "Incidents and hunts you can see, most recently changed first.",
        {
            "status": {"type": "string", "enum": ["open", "closed"], "default": "open"},
            "kind": {"type": "string", "enum": ["incident", "hunt"]},
        },
        (),
        list_records,
    ),
    Tool(
        "get_record",
        "One record: summary, severity, TLP, phase, people, teams, counts and linked records.",
        {"ref": REF},
        ("ref",),
        get_record,
    ),
    Tool(
        "findings",
        "A record's findings with their verifications. A lead is unconfirmed; confirmed and refuted ones "
        "were decided by an analyst.",
        {"ref": REF, "status": {"type": "string", "enum": ["lead", "confirmed", "refuted"]}},
        ("ref",),
        findings,
    ),
    Tool(
        "observables",
        "A record's observables (IOCs) and their sightings, masked as they are for you.",
        {"ref": REF},
        ("ref",),
        observables,
    ),
    Tool(
        "assets",
        "A record's assets and the connections between them (lateral movement, C2, exfiltration).",
        {"ref": REF},
        ("ref",),
        assets,
    ),
    Tool(
        "tasks",
        "A record's tasks, and its playbooks' workflows: which steps are waiting, open, done or "
        "skipped, and which outcome each decision took.",
        {"ref": REF},
        ("ref",),
        tasks,
    ),
    Tool("timeline", "A record's timeline of what happened, in order.", {"ref": REF}, ("ref",), timeline),
    Tool("comments", "A record's comment threads.", {"ref": REF}, ("ref",), comments),
    Tool(
        "activity",
        "A record's activity log, and whether its hash chain verifies.",
        {"ref": REF},
        ("ref",),
        activity,
    ),
    Tool("my_tasks", "Tasks assigned to you, and those waiting in your team's queue.", {}, (), my_tasks),
    Tool(
        "look_up",
        "Where any reference points (OST-, HNT-, FND-, TSK-, AST- or OBS-).",
        {"ref": {"type": "string"}},
        ("ref",),
        look_up,
    ),
    Tool(
        "add_comment",
        "Comment on a record as the token's owner. @mentions and #references work as they do in Ostraka.",
        {"ref": REF, "body": {"type": "string", "minLength": 1, "maxLength": 20000}},
        ("ref", "body"),
        add_comment,
        writes=True,
    ),
    Tool(
        "add_lead",
        "Add a lead to a record: an unconfirmed finding for an analyst to verify. It is never confirmed "
        "here.",
        {
            "ref": REF,
            "kind": {"type": "string", "enum": ["evidence", "intelligence", "potential"]},
            "title": {"type": "string", "minLength": 3, "maxLength": 200},
            "statement": {"type": "string", "description": "What was seen, and where (cite it)"},
            "techniques": {"type": "array", "items": {"type": "string"}, "description": "ATT&CK IDs"},
            "source_reliability": {"type": "string", "enum": list("ABCDEF")},
            "information_credibility": {"type": "string", "enum": list("123456")},
        },
        ("ref", "kind", "title"),
        add_lead,
        writes=True,
    ),
]
BY_NAME = {t.name: t for t in TOOLS}


def _offered(read_only: bool) -> list[Tool]:
    return [t for t in TOOLS if not (read_only and t.writes)]


def build_server() -> Server:
    server = Server(branding.NAME, version=__version__, instructions=INSTRUCTIONS)

    def _caller() -> dict:
        return server.request_context.request.scope["ostraka.mcp"]

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [t.definition() for t in _offered(_caller()["read_only"])]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        caller = _caller()
        tool = BY_NAME.get(name)
        if tool is None or tool not in _offered(caller["read_only"]):
            raise ValueError(f"No tool named {name}.")
        api = Api(caller["app"], caller["token"])
        try:
            output = await tool.run(api, **(arguments or {}))
        except ToolError as exc:
            raise ValueError(str(exc)) from exc
        finally:
            await api.close()
        return [types.TextContent(type="text", text=json.dumps(output, indent=1, default=str))]

    return server


_server: Server | None = None


async def _refuse(message: str, scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse({"detail": message}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    await response(scope, receive, send)


async def serve(scope: Scope, receive: Receive, send: Send) -> None:
    """MCP over streamable HTTP, stateless with JSON responses: a transport per request, so a
    restart loses nothing. Only an API token gets in; a browser session's JWT does not."""
    global _server
    headers = dict(scope.get("headers") or [])
    raw = headers.get(b"authorization", b"").decode("latin-1")
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
    if not token.startswith(branding.TOKEN_PREFIX):
        await _refuse(
            f"Send an API token: Authorization: Bearer {branding.TOKEN_PREFIX}… " "(Settings → API tokens)",
            scope,
            receive,
            send,
        )
        return
    async with sessions()() as db:
        try:
            caller = await _from_api_token(db, token)
        except Exception as exc:  # noqa: BLE001 (any refusal is the same answer)
            await _refuse(getattr(exc, "detail", "That API token isn't valid."), scope, receive, send)
            return
    scope["ostraka.mcp"] = {
        "app": scope["app"],
        "token": token,
        "read_only": caller.token is not None and caller.token.scope is TokenScope.MCP,
    }
    if _server is None:
        _server = build_server()
    transport = StreamableHTTPServerTransport(mcp_session_id=None, is_json_response_enabled=True)

    async def run(*, task_status: anyio.abc.TaskStatus = anyio.TASK_STATUS_IGNORED) -> None:
        async with transport.connect() as (read, write):
            task_status.started()
            await _server.run(read, write, _server.create_initialization_options(), stateless=True)

    async with anyio.create_task_group() as group:
        await group.start(run)
        try:
            await transport.handle_request(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await transport.terminate()


class App:
    """An ASGI app rather than a function, so the route at /mcp hands it the raw request."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await serve(scope, receive, send)


app = App()
