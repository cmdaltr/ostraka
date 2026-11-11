"""The FastAPI application. Run with `uvicorn ostraka.main:app`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Route

from ostraka import __version__, branding, live, mcp_server, notify, scheduler, webhooks
from ostraka.api import (
    ach,
    assessments,
    assets,
    auth,
    clocks,
    comments,
    findings,
    incidents,
    installation,
    invitations,
    lessons,
    links,
    notifications,
    observables,
    playbooks,
    reports,
    system,
    tags,
    tasks,
    teams,
    tokens,
    users,
)
from ostraka.api import live as live_api
from ostraka.api import (
    webhooks as webhooks_api,
)
from ostraka.config import get_settings
from ostraka.log import configure


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await live.startup()
    notify.startup()
    webhooks.startup()
    scheduler.startup()
    try:
        yield
    finally:
        await scheduler.shutdown()
        await live.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    configure(settings.log_level)
    # No CORS middleware: the browser only ever talks to ostraka through its own origin (nginx in
    # production, Vite's proxy in development), so cross-origin requests have nothing to reach.
    app = FastAPI(
        title=branding.NAME,
        description=branding.TAGLINE,
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    for module in (
        system,
        auth,
        invitations,
        users,
        teams,
        tokens,
        incidents,
        observables,
        assets,
        findings,
        assessments,
        ach,
        lessons,
        clocks,
        reports,
        installation,
        comments,
        links,
        tasks,
        playbooks,
        notifications,
        tags,
        webhooks_api,
        live_api,
    ):
        app.include_router(module.router)
    for extra in (incidents.refs_router, tasks.mine_router, playbooks.incident_router, clocks.mine_router):
        app.include_router(extra)
    # MCP over HTTP, as the bearer token's owner (ostraka/mcp_server.py)
    app.router.routes.append(Route("/mcp", endpoint=mcp_server.app, methods=["GET", "POST", "DELETE"]))
    return app


app = create_app()
