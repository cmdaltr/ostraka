"""Shared fixtures.

Tests run with OSTRAKA_ENV=test. Database tests (marked `db`) need OSTRAKA_TEST_DATABASE_URL, a Postgres
they may wipe; `make test-db` starts a throwaway one. The schema comes from Alembic, as in production.
"""

import importlib.util
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

os.environ.setdefault("OSTRAKA_ENV", "test")
os.environ.setdefault("OSTRAKA_REDIS_URL", "memory://")
os.environ.setdefault("OSTRAKA_SCHEDULER_ENABLED", "false")  # tests call scheduler.tick() themselves
os.environ.setdefault("OSTRAKA_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256-0123")
TEST_DB = os.environ.get("OSTRAKA_TEST_DATABASE_URL")
if TEST_DB:
    os.environ["OSTRAKA_DATABASE_URL"] = TEST_DB

import httpx  # noqa: E402
import pyotp  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from ostraka.auth.secrets import encrypt, hash_password  # noqa: E402
from ostraka.auth.totp import STEP_SECONDS  # noqa: E402
from ostraka.main import create_app  # noqa: E402
from ostraka.models import Role, Team, User  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple"
SEED_TEAMS = (
    "SOC",
    "Forensics",
    "Incident Response",
    "Incident Management",
    "Intelligence",
    "Threat Hunting",
    "Leadership",
)


def pytest_collection_modifyitems(config, items):
    if TEST_DB:
        return
    skip = pytest.mark.skip(reason="OSTRAKA_TEST_DATABASE_URL not set (make test-db)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated():
    env = {**os.environ, "OSTRAKA_DATABASE_URL": TEST_DB or ""}
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture
async def db(migrated):
    """A session on a database emptied of everything but the seed teams."""
    from ostraka.db import sessions

    async with sessions()() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, push_subscriptions, checklist_items, tasks, incident_playbooks, "
                "playbook_steps, playbooks, activity_events, mentions, comments, verifications, "
                "finding_observables, findings, "
                "sightings, asset_links, assets, incident_observables, observables, incident_members, "
                "incident_teams, incidents, incident_counters, tag_definitions, licences, report_brands, "
                "webhook_deliveries, webhooks, "
                "security_events, api_tokens, sessions, backup_codes, invitations, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.execute(text("DELETE FROM teams WHERE name <> ALL(:seed)"), {"seed": list(SEED_TEAMS)})
        await session.execute(text("UPDATE teams SET teams_webhook = ''"))
        await session.commit()
        yield session


@pytest.fixture(autouse=True)
def roomy_free_edition(monkeypatch):
    """Most tests build organisations bigger than the free edition allows; the licence tests put the
    real limit back."""
    from ostraka import licence

    monkeypatch.setattr(licence, "FREE_SEATS", 100)


@pytest.fixture
def signing_key(monkeypatch):
    """A key ostraka trusts for the test, in place of the real one, which never leaves its owner."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from ostraka import licence

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    monkeypatch.setattr(licence, "PUBLIC_KEYS", (public,))
    return key


@pytest.fixture
def licence_line(signing_key):
    """A signed licence line: licence_line("services", seats=10, expires=date(...))."""
    from datetime import UTC, datetime, timedelta

    from ostraka import licence

    a_year = object()

    def make(edition: str, seats: int | None = 10, expires=a_year, licensee="Northwind Ltd", **extra) -> str:
        """`expires` defaults to a year from today; None makes it perpetual."""
        today = datetime.now(UTC).date()
        return licence.sign(
            signing_key,
            licence.Licence(
                licence_id=licence.new_id(),
                licensee=licensee,
                edition=licence.Edition(edition),
                seats=seats,
                issued=today,
                expires=today + timedelta(days=365) if expires is a_year else expires,
                **extra,
            ),
        )

    return make


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://ostraka.test") as c:
        yield c
    from ostraka import notify, webhooks

    await notify.wait()  # deliveries still running would hold a transaction into the next test
    await webhooks.wait()


class Person:
    """A user created straight into the database, with what a test needs to sign in as them."""

    def __init__(self, user: User, secret: str):
        self.user = user
        self.secret = secret
        self.last_step = 0
        self.access: str | None = None

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    def code(self) -> str:
        """A valid TOTP code that isn't a replay: each call moves one step on, within the drift window."""
        now = int(time.time() // STEP_SECONDS)
        step = max(now, self.last_step + 1)  # the current window survives a clock tick before it's checked
        assert step <= now + 1, "too many codes in one window for this test"
        self.last_step = step
        return pyotp.TOTP(self.secret).at(step * STEP_SECONDS)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


@pytest.fixture
def make_person(db):
    async def make(
        email: str = "analyst@example.org",
        role: Role = Role.RESPONDER,
        team: str | None = "SOC",
        name="Analyst",
    ) -> Person:
        from sqlalchemy import select

        team_id = (await db.scalar(select(Team.id).where(Team.name == team))) if team else None
        secret = pyotp.random_base32()
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(PASSWORD),
            role=role,
            team_id=team_id,
            totp_secret=encrypt(secret),
            totp_last_step=0,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return Person(user, secret)

    return make


@pytest.fixture
def sign_in(client):
    async def go(person: Person) -> Person:
        r = await client.post("/api/auth/login", json={"email": person.user.email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        r = await client.post("/api/auth/mfa", json={"ticket": r.json()["ticket"], "code": person.code()})
        assert r.status_code == 200, r.text
        person.access = r.json()["access_token"]
        return person

    return go


class Outbox:
    """Everything ostraka tried to send outside itself: HTTP posts (push, Teams) and emails."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []
        self.emails: list = []
        self.status = 201

    def teams(self) -> list[dict]:
        import json

        return [json.loads(body) for url, body, _ in self.posts if "logic.azure.com" in url]

    def pushes(self) -> list[tuple[str, bytes, dict[str, str]]]:
        return [p for p in self.posts if "logic.azure.com" not in p[0] and "microsoft" not in p[0]]

    def graph_mail(self) -> list[dict]:
        import json

        return [json.loads(body) for url, body, _ in self.posts if url.endswith("/sendMail")]


@pytest.fixture
def outbox(monkeypatch):
    """Capture deliveries instead of making them. `await settle()` waits for background sending."""
    from ostraka.notify import channels

    box = Outbox()

    async def fetch(url, *, content, headers):
        box.posts.append((url, content, headers))
        if url.startswith("https://login.microsoftonline.com/"):
            return 200, b'{"access_token": "graph-token", "expires_in": 3599}'
        if url.startswith(channels.GRAPH):
            return 202, b""
        return box.status, b""

    monkeypatch.setattr(channels, "fetch", fetch)
    monkeypatch.setattr(channels, "_graph_token", ("", 0.0))
    monkeypatch.setattr(channels, "smtp", box.emails.append)
    return box


@pytest.fixture
def settle():
    from ostraka import notify

    return notify.wait


def _revision(name: str):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def seed_playbooks(db):
    """The seed playbooks as a migrated database has them: 0005's steps, on 0006's teams, rewritten as
    0007's workflows. (The `db` fixture empties the playbooks the migrations made.)"""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    seeds, teams, workflows = (
        _revision("0005_hunts_and_links.py"),
        _revision("0006_team_codes.py"),
        _revision("0007_workflows.py"),
    )
    moved = {**teams.RENAMED, **teams.MERGED}
    seeds.SEEDS = [
        (
            name,
            kind,
            tags,
            description,
            [
                (
                    title,
                    "Threat Hunting" if kind == "hunt" and team == "SOC" else moved.get(team, team),
                    due,
                    items,
                )
                for title, team, due, items in steps
            ],
        )
        for name, kind, tags, description, steps in seeds.SEEDS
    ]

    def seed(connection):
        with Operations.context(MigrationContext.configure(connection)):
            seeds._seed()
            workflows._rewrite()

    await (await db.connection()).run_sync(seed)
    await db.commit()
