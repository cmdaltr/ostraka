"""Editions against Postgres: the free edition's four places, installing a licence, and report brands
under Pro and Services, down to the exported report."""

import io

import pytest
from docx import Document
from PIL import Image
from sqlalchemy import select

from ostraka import licence
from ostraka.accounts import UNUSABLE_PASSWORD
from ostraka.models import Role, SecurityEvent, User
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db


@pytest.fixture
def free_edition(monkeypatch):
    monkeypatch.setattr(licence, "FREE_SEATS", 4)


async def invite(client, admin, email, role="responder", team="SOC"):
    from ostraka.db import sessions
    from tests.test_incidents import team_id

    async with sessions()() as db:
        team = await team_id(db, team)
    return await client.post(
        "/api/invitations", json={"email": email, "role": role, "team_id": team}, headers=admin.headers
    )


def accepted(line: str) -> dict:
    """An install body whose admin accepted the current terms."""
    return {"text": line, "accept_terms": True, "terms_version": licence.terms()[0]}


def logo(colour=(20, 60, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (300, 90), (*colour, 255)).save(out, "PNG")
    return out.getvalue()


# ---- the free edition ---------------------------------------------------------------------------


async def test_the_free_edition_has_four_places_and_invitations_hold_them(
    db, client, make_person, sign_in, free_edition
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    await make_person("lead@example.org", Role.TEAM_LEAD, "SOC")
    assert (await invite(client, admin, "one@example.org")).status_code == 201
    third = (await invite(client, admin, "two@example.org")).json()

    r = await invite(client, admin, "three@example.org")
    assert r.status_code == 402 and "free edition is for 4" in r.json()["detail"]
    # Withdrawing an invitation frees its place
    assert (await client.delete(f"/api/invitations/{third['id']}", headers=admin.headers)).status_code == 204
    assert (await invite(client, admin, "three@example.org")).status_code == 201

    r = await client.get("/api/licence", headers=admin.headers)
    assert r.json() | {"expires": None} == {
        "edition": "free",
        "state": "free",
        "organisation": "",
        "seats": 4,
        "features": [],
        "places_held": 4,
        "not_before": None,
        "expires": None,
        "grace_ends": None,
        "custom_branding": False,
        "brand_library": False,
        "terms_version": "1.1",
        "accepted": None,
    }


async def test_imported_people_hold_no_place_until_they_are_given_a_way_in(
    db, client, make_person, sign_in, free_edition
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    for i in range(6):  # the importer's placeholders: names on records, nobody can sign in as them
        imported = await make_person(f"imported{i}@example.org", Role.RESPONDER, "SOC")
        imported.user.password_hash = UNUSABLE_PASSWORD
    await db.commit()
    await invite(client, admin, "a@example.org")
    await invite(client, admin, "b@example.org")
    first = await db.scalar(select(User).where(User.email == "imported0@example.org"))
    r = await client.post(f"/api/users/{first.id}/reset-access", headers=admin.headers)
    assert r.status_code == 201  # the fourth place
    second = await db.scalar(select(User).where(User.email == "imported1@example.org"))
    r = await client.post(f"/api/users/{second.id}/reset-access", headers=admin.headers)
    assert r.status_code == 402


async def test_reactivating_someone_needs_a_free_place(db, client, make_person, sign_in, free_edition):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    gone = await make_person("gone@example.org", Role.RESPONDER, "SOC")
    r = await client.patch(
        f"/api/users/{gone.id}", json={"version": 1, "is_active": False}, headers=admin.headers
    )
    assert r.status_code == 200
    for email in ("a@example.org", "b@example.org", "c@example.org"):
        assert (await invite(client, admin, email)).status_code == 201
    r = await client.patch(
        f"/api/users/{gone.id}", json={"version": 2, "is_active": True}, headers=admin.headers
    )
    assert r.status_code == 402


# ---- installing a licence -----------------------------------------------------------------------


async def test_an_admin_installs_a_licence_for_more_places(
    db, client, make_person, sign_in, free_edition, licence_line
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    lead = await sign_in(await make_person("lead@example.org", Role.TEAM_LEAD, "SOC"))
    line = licence_line("pro", seats=6)

    r = await client.put("/api/licence", json=accepted(line), headers=lead.headers)
    assert r.status_code == 403
    r = await client.put("/api/licence", json=accepted(line[:-4] + "AAAA"), headers=admin.headers)
    assert r.status_code == 422 and "altered" in r.json()["detail"]
    r = await client.put("/api/licence", json=accepted(line), headers=admin.headers)
    assert r.status_code == 200, r.text
    assert (r.json()["edition"], r.json()["seats"], r.json()["organisation"]) == ("pro", 6, "Northwind Ltd")

    for email in ("a@example.org", "b@example.org", "c@example.org", "d@example.org"):
        assert (await invite(client, admin, email)).status_code == 201
    r = await invite(client, admin, "e@example.org")
    assert r.status_code == 402 and "licence covers 6" in r.json()["detail"]

    # Removing it goes back to free: nobody is deactivated, but nobody new joins
    r = await client.delete("/api/licence", headers=admin.headers)
    assert r.json()["edition"] == "free" and r.json()["places_held"] == 6
    actions = (await db.scalars(select(SecurityEvent.action).order_by(SecurityEvent.at))).all()
    assert "licence_installed" in actions and "licence_removed" in actions


async def test_a_licence_that_has_run_out_isnt_installed(db, client, make_person, sign_in, licence_line):
    from datetime import date

    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    r = await client.put(
        "/api/licence", json={"text": licence_line("pro", expires=date(2026, 1, 1))}, headers=admin.headers
    )
    assert r.status_code == 422 and "ran out" in r.json()["detail"]


# ---- report brands ------------------------------------------------------------------------------


async def test_pro_has_one_brand_and_its_logos_are_cleaned(db, client, make_person, sign_in, licence_line):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    body = {"name": "Northwind", "primary": "#1F6FEB", "prepared_for": "Northwind Ltd"}
    assert (await client.post("/api/brands", json=body, headers=admin.headers)).status_code == 402

    await client.put("/api/licence", json=accepted(licence_line("pro")), headers=admin.headers)
    r = await client.post("/api/brands", json=body, headers=admin.headers)
    assert r.status_code == 201, r.text
    brand = r.json()
    assert brand["is_default"] and brand["primary"] == "#1f6feb" and brand["palette"]["clay"].startswith("#")
    r = await client.post("/api/brands", json={**body, "name": "Second"}, headers=admin.headers)
    assert r.status_code == 402 and "Services" in r.json()["detail"]

    url = f"/api/brands/{brand['id']}/logos/cover"
    r = await client.put(url, content=logo(), headers={**admin.headers, "Content-Type": "image/png"})
    assert r.status_code == 200 and r.json()["logos"] == ["cover"]
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    r = await client.put(url, content=svg, headers={**admin.headers, "Content-Type": "image/svg+xml"})
    assert r.status_code == 422
    r = await client.get(url, headers=admin.headers)
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"

    r = await client.patch(
        f"/api/brands/{brand['id']}", json={"version": 1, "secondary": "#00bcd4"}, headers=admin.headers
    )
    assert r.status_code == 409  # the logo upload moved it on
    r = await client.patch(
        f"/api/brands/{brand['id']}", json={"version": 2, "secondary": "#00bcd4"}, headers=admin.headers
    )
    assert r.status_code == 200 and r.json()["secondary"] == "#00bcd4"


async def test_services_brands_each_incident_and_the_report_carries_it(
    db,
    client,
    people,  # noqa: F811
    licence_line,
):
    p = await people()
    admin, soc = p["admin"], p["soc"]
    await client.put("/api/licence", json=accepted(licence_line("services")), headers=admin.headers)
    house = (
        await client.post("/api/brands", json={"name": "House", "primary": "#1a3d8f"}, headers=admin.headers)
    ).json()
    contoso = (
        await client.post(
            "/api/brands",
            json={"name": "Contoso", "primary": "#107c41", "prepared_for": "Contoso Bank plc"},
            headers=admin.headers,
        )
    ).json()
    assert house["is_default"] and not contoso["is_default"]
    names = [b["name"] for b in (await client.get("/api/brands", headers=soc.headers)).json()]
    assert names == ["House", "Contoso"]
    assert (await client.get("/api/brands", headers=p["exec"].headers)).json() == []

    incident = await declare(client, soc)
    url = f"/api/incidents/{incident['id']}"
    r = await client.patch(
        url, json={"version": incident["version"], "brand_id": contoso["id"]}, headers=soc.headers
    )
    assert r.status_code == 200 and r.json()["brand_id"] == contoso["id"]

    r = await client.get(
        f"{url}/export", params={"variant": "briefing", "format": "docx"}, headers=soc.headers
    )
    assert r.status_code == 200
    text = "\n".join(par.text for par in Document(io.BytesIO(r.content)).paragraphs)
    assert "Prepared for Contoso Bank plc" in text
    exported = await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "report_exported"))
    assert exported.detail["brand"] == "Contoso"

    # Deleting the brand puts the incident back on the default
    await client.delete(f"/api/brands/{contoso['id']}", headers=admin.headers)
    assert (await client.get(url, headers=soc.headers)).json()["brand_id"] is None


async def test_pro_incidents_use_the_installation_brand_and_cant_choose(
    db,
    client,
    people,  # noqa: F811
    licence_line,
):
    p = await people()
    await client.put("/api/licence", json=accepted(licence_line("pro")), headers=p["admin"].headers)
    brand = (
        await client.post(
            "/api/brands", json={"name": "House", "primary": "#1a3d8f"}, headers=p["admin"].headers
        )
    ).json()
    incident = await declare(client, p["soc"])
    r = await client.patch(
        f"/api/incidents/{incident['id']}",
        json={"version": incident["version"], "brand_id": brand["id"]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 402
    r = await client.get(f"/api/incidents/{incident['id']}/export", headers=p["soc"].headers)
    assert r.status_code == 200
    exported = await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "report_exported"))
    assert exported.detail["brand"] == "House"


async def test_the_server_command_installs_and_removes_a_licence(db, tmp_path, licence_line, capsys):
    from ostraka import cli

    path = tmp_path / "northwind.licence"
    path.write_text(licence_line("services", seats=None, expires=None) + "\n")
    assert await cli._licence_install(path, "") == 1  # the terms weren't accepted
    assert "--accept-terms" in capsys.readouterr().err
    assert await cli._licence_install(path, "Ben Smith <ben@example.org>") == 0
    assert "services for Northwind Ltd: unlimited seats to no end date" in capsys.readouterr().out
    assert (await licence.current(db)).brand_library

    path.write_text("not a licence")
    assert await cli._licence_install(path, "Ben Smith <ben@example.org>") == 1
    assert await cli._licence_remove() == 0
    assert (await licence.current(db)).edition is licence.Edition.FREE
    actions = (await db.scalars(select(SecurityEvent.action).order_by(SecurityEvent.at))).all()
    assert actions == ["licence_installed", "licence_removed"]


async def test_installing_needs_the_current_terms_accepted_and_records_who_did(
    db, client, make_person, sign_in, licence_line
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin"))
    line = licence_line("pro", licensee="Contoso Bank plc")

    r = await client.post("/api/licence/check", json={"text": line}, headers=admin.headers)
    assert r.json()["licensee"] == "Contoso Bank plc"
    assert r.json()["features"] == ["automation", "report-branding"]
    r = await client.get("/api/licence/terms", headers=admin.headers)
    version = r.json()["version"]
    assert version == "1.1" and "Ostraka Commercial Licence" in r.json()["text"]

    r = await client.put("/api/licence", json={"text": line}, headers=admin.headers)
    assert r.status_code == 422 and "Accept the commercial licence terms for Contoso Bank plc" in r.text
    r = await client.put(
        "/api/licence",
        json={"text": line, "accept_terms": True, "terms_version": "0.9"},
        headers=admin.headers,
    )
    assert r.status_code == 409 and "version 1.1" in r.text
    r = await client.put("/api/licence", json=accepted(line), headers=admin.headers)
    assert r.json()["accepted"]["by"] == "Ada Admin <admin@example.org>"
    assert r.json()["accepted"]["terms_version"] == "1.1"
    event = await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "licence_installed"))
    assert event.detail["terms_version"] == "1.1"
