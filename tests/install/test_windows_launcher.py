"""desktop/windows/tests/test-launcher.ps1: Ostraka.ps1's logic (addresses, has_admin, first admin,
update flags) with the CLI stubbed, in PowerShell on Linux. Needs Docker; skipped without it."""

from __future__ import annotations

import pytest
from test_containers import PWSH_IMAGE, ROOT, docker, docker_ok, pwsh_image  # noqa: F401

pytestmark = pytest.mark.skipif(not docker_ok(), reason="needs a running Docker")


def test_windows_launcher():
    r = docker(
        "run", "--rm", "-v", f"{ROOT}:/src:ro", PWSH_IMAGE,
        "pwsh", "-NoProfile", "-File", "/src/desktop/windows/tests/test-launcher.ps1",
    )
    assert r.returncode == 0, r.stdout + r.stderr
