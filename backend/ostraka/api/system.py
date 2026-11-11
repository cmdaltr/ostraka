"""Endpoints about ostraka itself: liveness, readiness, busy, branding and its licence. None need a login."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from redis.asyncio import Redis

from ostraka import __version__, branding, busy, db, licence
from ostraka.config import get_settings
from ostraka.log import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """The process is up. Says nothing about the database; that's `/api/ready`."""
    return {"status": "ok", "version": __version__}


async def _check_database() -> str:
    try:
        await db.ping()
        return "ok"
    except Exception as exc:
        log.warning("readiness: database unavailable: %s", exc)
        return "unavailable"


async def _check_redis() -> str:
    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    try:
        await client.ping()
        return "ok"
    except Exception as exc:
        log.warning("readiness: redis unavailable: %s", exc)
        return "unavailable"
    finally:
        await client.aclose()


@router.get("/ready")
async def ready(response: Response) -> dict[str, object]:
    """Everything ostraka depends on answers. 503 while anything doesn't."""
    checks = {"database": await _check_database(), "redis": await _check_redis()}
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/busy")
async def is_busy() -> dict[str, bool]:
    """The homelab contract: `{"busy": true}` holds back a rebuild. Reasons stay in the log."""
    reasons = busy.busy_reasons()
    if reasons:
        log.info("busy: %s", ", ".join(reasons))
    return {"busy": bool(reasons)}


@router.get("/meta")
async def meta() -> dict[str, object]:
    return {"version": __version__, **branding.as_dict()}


@router.get("/legal")
async def legal() -> dict[str, str]:
    """What Help → Licence shows: the code licence and the current commercial terms. Public, so the
    page opens before sign-in and from the desktop app's Help menu."""
    version, terms = licence.terms()
    return {"licence": licence.code_licence(), "terms_version": version, "terms": terms}
