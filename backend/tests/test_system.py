from ostraka import __version__, branding, busy
from ostraka.api import system


async def test_health_is_up_without_dependencies(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


async def test_busy_is_false_with_nothing_registered(client):
    r = await client.get("/api/busy")
    assert r.json() == {"busy": False}


async def test_busy_follows_registered_checks(client):
    busy.register("editing", lambda: True)
    try:
        assert (await client.get("/api/busy")).json() == {"busy": True}
    finally:
        busy.unregister("editing")
    assert (await client.get("/api/busy")).json() == {"busy": False}


async def test_busy_check_that_raises_counts_as_busy(client):
    def broken() -> bool:
        raise RuntimeError("redis gone")

    busy.register("broken", broken)
    try:
        assert (await client.get("/api/busy")).json() == {"busy": True}
    finally:
        busy.unregister("broken")


async def test_busy_never_leaks_reasons(client):
    busy.register("ben editing OST-2026-014", lambda: True)
    try:
        assert set((await client.get("/api/busy")).json()) == {"busy"}
    finally:
        busy.unregister("ben editing OST-2026-014")


async def test_ready_reports_503_when_a_dependency_is_down(client, monkeypatch):
    async def ok() -> str:
        return "ok"

    async def down() -> str:
        return "unavailable"

    monkeypatch.setattr(system, "_check_database", ok)
    monkeypatch.setattr(system, "_check_redis", down)
    r = await client.get("/api/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "checks": {"database": "ok", "redis": "unavailable"}}


async def test_ready_ok_when_everything_answers(client, monkeypatch):
    async def ok() -> str:
        return "ok"

    monkeypatch.setattr(system, "_check_database", ok)
    monkeypatch.setattr(system, "_check_redis", ok)
    r = await client.get("/api/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_meta_serves_branding(client):
    body = (await client.get("/api/meta")).json()
    assert body["name"] == branding.NAME
    assert body["default_theme"] == "cobalt"
    assert body["themes"] == ["cobalt", "ostrakon", "ledger"]
    assert body["phases"] == ["detect", "scope", "contain", "eradicate", "recover", "learn"]


async def test_the_licence_and_terms_are_readable_without_signing_in(client):
    body = (await client.get("/api/legal")).json()
    assert "Elastic License 2.0" in body["licence"] and "By using the software" in body["licence"]
    assert body["terms_version"] == "1.1" and body["terms"].startswith("# Ostraka Commercial Licence")


async def test_no_registration_or_cors(client):
    assert (await client.post("/api/v1/auth/register", json={})).status_code == 404
    r = await client.options(
        "/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in r.headers
