"""`clients/ost.ps1` against the same fake Ostraka as test_ost.py.

PowerShell isn't on a stock Mac, so this runs where `pwsh` is on PATH, or in the
mcr.microsoft.com/powershell image when OSTRAKA_TEST_PWSH_DOCKER=1 (natively on Apple silicon, from the
arm64 tag; the default one crashes there under emulation). Otherwise it's skipped.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from test_ost import INCIDENT_ID, ME, TOKEN, FakeOstraka, _handler

CLIENTS = Path(__file__).resolve().parents[1]
ARM = platform.machine() in ("arm64", "aarch64")
IMAGE = "mcr.microsoft.com/powershell:" + ("7.5-azurelinux-3.0-arm64" if ARM else "latest")
USE_DOCKER = os.environ.get("OSTRAKA_TEST_PWSH_DOCKER") == "1" and shutil.which("docker")

pytestmark = pytest.mark.skipif(
    not shutil.which("pwsh") and not USE_DOCKER, reason="needs pwsh, or OSTRAKA_TEST_PWSH_DOCKER=1"
)


@pytest.fixture
def fake() -> Iterator[FakeOstraka]:
    fake = FakeOstraka(url="")
    # On every interface, so a container can reach it through host.docker.internal.
    server = ThreadingHTTPServer(("0.0.0.0" if USE_DOCKER else "127.0.0.1", 0), _handler(fake))  # noqa: S104
    host = "host.docker.internal" if USE_DOCKER else "127.0.0.1"
    fake.url = f"http://{host}:{server.server_address[1]}"
    fake.routes.update(
        {
            ("GET", "/api/users/me"): (200, ME),
            ("GET", "/api/incidents/by-ref/OST-2026-001"): (200, {"id": INCIDENT_ID, "ref": "OST-2026-001"}),
        }
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield fake
    server.shutdown()
    server.server_close()


def pwsh(
    fake: FakeOstraka, *args: str, stdin: str = "", token: str = TOKEN, url: str = ""
) -> subprocess.CompletedProcess:
    env = {"OSTRAKA_URL": url or fake.url, "OSTRAKA_TOKEN": token, "OSTRAKA_ALLOW_HTTP": "1"}
    if USE_DOCKER:
        cmd = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--platform",
            "linux/arm64" if ARM else "linux/amd64",
            "-e",
            "DOTNET_EnableWriteXorExecute=0",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{CLIENTS}:/c:ro",
        ]
        for k in env:
            cmd += ["-e", k]
        cmd += [IMAGE, "pwsh", "-NoProfile", "-File", "/c/ost.ps1", *args]
    else:
        cmd = ["pwsh", "-NoProfile", "-File", str(CLIENTS / "ost.ps1"), *args]
    proc = subprocess.run(  # noqa: S603
        cmd, input=stdin, capture_output=True, text=True, timeout=600, check=False, env={**os.environ, **env}
    )
    assert token not in proc.stdout and token not in proc.stderr
    return proc


def test_declare(fake: FakeOstraka) -> None:
    fake.routes[("POST", "/api/incidents")] = (201, {"id": INCIDENT_ID, "ref": "OST-2026-002"})
    proc = pwsh(fake, "declare", "Beacon from WS042", "--severity", "High", "--tlp", "TLP:AMBER+STRICT")
    assert proc.returncode == 0, proc.stderr
    [req] = fake.sent("POST", "/api/incidents")
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.body == {
        "kind": "incident",
        "title": "Beacon from WS042",
        "severity": "high",
        "tlp": "amber+strict",
        "summary": "",
        "tags": [],
    }
    assert "Declared OST-2026-002" in proc.stdout


def test_ioc_add_from_stdin_and_a_single_task_row(fake: FakeOstraka) -> None:
    path = f"/api/incidents/{INCIDENT_ID}/observables"
    fake.routes[("POST", path)] = lambda b: (
        201,
        {"id": "o", "ref": "OBS-1", "kind": "domain", "value": b["value"]},
    )
    proc = pwsh(fake, "ioc", "add", "OST-2026-001", stdin="# list\nevil[.]example\n\n198.51.100.7\n")
    assert proc.returncode == 0, proc.stderr
    assert [r.body["value"] for r in fake.sent("POST", path)] == ["evil[.]example", "198.51.100.7"]

    fake.routes[("GET", "/api/tasks/mine")] = (
        200,
        [
            {
                "ref": "TSK-1",
                "status": "open",
                "title": "Pull the proxy logs",
                "due_at": "2026-11-12T09:30:00Z",
                "assignee": {"id": ME["id"], "name": ME["name"]},
                "team": {"name": "Blue"},
                "incident": {"ref": "OST-2026-001"},
            },
        ],
    )
    tasks = pwsh(fake, "tasks", "--mine")
    assert tasks.returncode == 0, tasks.stderr
    assert (
        "TSK-1" in tasks.stdout
        and "Pull the proxy logs" in tasks.stdout
        and "2026-11-12 09:30" in tasks.stdout
    )


def test_a_rejected_token(fake: FakeOstraka) -> None:
    proc = pwsh(fake, "whoami", token="ost_RevokedRevokedRevoked")  # noqa: S106 (fake)
    assert proc.returncode == 3
    assert "didn't accept your token" in proc.stderr


def test_user_info_in_a_plain_http_address_is_refused(fake: FakeOstraka) -> None:
    # Refused even with OSTRAKA_ALLOW_HTTP=1, which pwsh() sets: the request would go to evil.example.
    proc = pwsh(fake, "whoami", url="http://127.0.0.1:1@evil.example/")
    assert proc.returncode == 1
    assert "user info" in proc.stderr
    assert fake.requests == []
