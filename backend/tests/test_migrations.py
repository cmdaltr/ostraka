"""Alembic upgrades and downgrades cleanly against a real Postgres. Skipped without one."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

URL = os.environ.get("OSTRAKA_TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.db, pytest.mark.skipif(not URL, reason="OSTRAKA_TEST_DATABASE_URL not set")]


def _alembic(*args: str) -> None:
    env = {**os.environ, "OSTRAKA_ENV": "test", "OSTRAKA_DATABASE_URL": URL or ""}
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True)  # noqa: S603


def test_upgrade_downgrade_upgrade():
    _alembic("upgrade", "head")
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
