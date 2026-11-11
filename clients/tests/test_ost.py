"""`clients/ost` against a fake Ostraka: a small http.server in a thread that checks the bearer header
and records each request, so the tests see exactly what the client sent.

Run with backend/.venv/bin/pytest clients/tests. Each test runs once with jq and once with Python 3's
json, the two ways the client reads answers; on a Mac the Python run uses /bin/bash, Bash 3.2, which is
what a stock macOS runs the client with.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

OST = Path(__file__).resolve().parents[1] / "ost"
TOKEN = "ost_TestTokenAbcdef0123456789-_xyz"  # noqa: S105 (a fake token for the fake server)
INCIDENT_ID = "11111111-2222-3333-4444-555555555555"
ME = {
    "id": "aaaaaaaa-0000-0000-0000-000000000001",
    "name": "Ada Responder",
    "email": "ada@example.org",
    "role": "responder",
    "team": {"id": "t1", "name": "Blue"},
}


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: dict | None


@dataclass
class FakeOstraka:
    url: str
    requests: list[Seen] = field(default_factory=list)
    # (method, path) -> (status, body); a callable gets the parsed request body
    routes: dict[tuple[str, str], tuple[int, object] | Callable[[dict | None], tuple[int, object]]] = field(
        default_factory=dict
    )

    def sent(self, method: str, path: str) -> list[Seen]:
        return [r for r in self.requests if r.method == method and r.path == path]


def _handler(fake: FakeOstraka) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep pytest's output clean
            pass

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            fake.requests.append(Seen(self.command, self.path, dict(self.headers), body))
            if self.path != "/api/meta" and self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._reply(401, {"detail": "That API token isn't valid."})
                return
            route = fake.routes.get((self.command, self.path))
            if route is None:
                self._reply(404, {"detail": "Not Found"})
                return
            status, out = route(body) if callable(route) else route
            self._reply(status, out)

        def _reply(self, status: int, out: object) -> None:
            data = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = _serve

    return Handler


@pytest.fixture
def fake() -> Iterator[FakeOstraka]:
    fake = FakeOstraka(url="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    fake.routes.update(
        {
            ("GET", "/api/meta"): (200, {"name": "Ostraka", "version": "0.1.0"}),
            ("GET", "/api/users/me"): (200, ME),
            ("GET", "/api/incidents/by-ref/OST-2026-001"): (
                200,
                {"id": INCIDENT_ID, "ref": "OST-2026-001", "title": "Ransom note on FS01"},
            ),
        }
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake
    server.shutdown()
    server.server_close()


# (JSON tool, shell). /bin/bash is 3.2 on macOS, the oldest the client has to run on.
SETUPS = [
    (t, sh) for t, sh in (("jq", "bash"), ("python3", "/bin/bash")) if shutil.which(t) and shutil.which(sh)
]


@pytest.fixture(params=SETUPS, ids=lambda s: f"{s[0]}-{Path(s[1]).parent.name or 'path'}")
def run(
    request: pytest.FixtureRequest, fake: FakeOstraka, tmp_path: Path
) -> Callable[..., subprocess.CompletedProcess]:
    """Run ost with a clean HOME, the fake server and a curl shim that logs its arguments."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    argv_log = tmp_path / "curl-argv.log"
    real_curl = shutil.which("curl")
    shim = shim_dir / "curl"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{argv_log}"\nexec "{real_curl}" "$@"\n')
    shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()

    def _run(*args: str, stdin: str = "", env: dict[str, str] | None = None, token: bool = True):
        e = {
            "HOME": str(home),
            "PATH": f"{shim_dir}:{os.environ['PATH']}",
            "OSTRAKA_JSON": request.param[0],
            "OSTRAKA_URL": fake.url,
        }
        if token:
            e["OSTRAKA_TOKEN"] = TOKEN
        e.update(env or {})
        proc = subprocess.run(  # noqa: S603 (our own script)
            [request.param[1], str(OST), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=e,
            timeout=60,
            check=False,
        )
        # Wherever it ran, the token must never be printed or put on curl's command line.
        assert TOKEN not in proc.stdout
        assert TOKEN not in proc.stderr
        if argv_log.exists():
            assert TOKEN not in argv_log.read_text()
        return proc

    _run.home = home  # type: ignore[attr-defined]
    return _run


def test_declare_sends_title_severity_and_tlp(run, fake: FakeOstraka) -> None:
    fake.routes[("POST", "/api/incidents")] = lambda body: (201, {"id": INCIDENT_ID, "ref": "OST-2026-002"})
    proc = run(
        "declare", "Beacon", "from", "WS042", "--severity", "HIGH", "--tlp", "TLP:AMBER+STRICT", "--tag", "c2"
    )
    assert proc.returncode == 0, proc.stderr
    [req] = fake.sent("POST", "/api/incidents")
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["Content-Type"] == "application/json"
    assert req.body == {
        "kind": "incident",
        "title": "Beacon from WS042",
        "severity": "high",
        "tlp": "amber+strict",
        "summary": "",
        "tags": ["c2"],
    }
    assert "Declared OST-2026-002" in proc.stdout
    assert f"{fake.url}/incidents/OST-2026-002" in proc.stdout


def test_declare_needs_a_known_severity(run, fake: FakeOstraka) -> None:
    proc = run("declare", "Something odd", "--severity", "spicy")
    assert proc.returncode == 2
    assert "critical high medium low" in proc.stderr
    assert fake.requests == []


def test_ioc_add_from_stdin_one_per_line(run, fake: FakeOstraka) -> None:
    path = f"/api/incidents/{INCIDENT_ID}/observables"
    counter = iter(range(1, 100))

    def add(body: dict | None) -> tuple[int, object]:
        n = next(counter)
        return 201, {
            "id": f"o{n}",
            "ref": f"OBS-{n}",
            "kind": "domain",
            "value": body["value"] if body else "",
        }

    fake.routes[("POST", path)] = add
    pasted = "# from the proxy logs\nevil[.]example\n\n  198.51.100.7  \nhxxp://evil.example/x\n"
    proc = run("ioc", "add", "ost-2026-001", "--verdict", "malicious", stdin=pasted)
    assert proc.returncode == 0, proc.stderr
    assert len(fake.sent("GET", "/api/incidents/by-ref/OST-2026-001")) == 1
    sent = fake.sent("POST", path)
    assert [r.body["value"] for r in sent] == ["evil[.]example", "198.51.100.7", "hxxp://evil.example/x"]
    assert all(r.body["verdict"] == "malicious" for r in sent)
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in sent)
    assert "OBS-1" in proc.stdout and "OBS-3" in proc.stdout
    assert "Added 3 to OST-2026-001" in proc.stderr


def test_ioc_add_values_as_arguments_and_a_failure(run, fake: FakeOstraka) -> None:
    path = f"/api/incidents/{INCIDENT_ID}/observables"

    def add(body: dict | None) -> tuple[int, object]:
        if body and body["value"] == "x" * 3:
            return 422, {"detail": [{"loc": ["body", "value"], "msg": "not an indicator"}]}
        return 201, {"id": "o", "ref": "OBS-9", "kind": "ipv4", "value": "203.0.113.5"}

    fake.routes[("POST", path)] = add
    proc = run("ioc", "add", "OST-2026-001", "203.0.113.5", "xxx")
    assert proc.returncode == 1
    assert len(fake.sent("POST", path)) == 2
    assert "value: not an indicator" in proc.stderr
    assert "1 failed" in proc.stderr


def test_a_rejected_token_says_what_to_do(run, fake: FakeOstraka) -> None:
    proc = run("incidents", env={"OSTRAKA_TOKEN": "ost_RevokedRevokedRevoked"})
    assert proc.returncode == 3
    assert "didn't accept your token" in proc.stderr
    assert "Profile -> API tokens" in proc.stderr
    assert "ost_RevokedRevokedRevoked" not in proc.stderr + proc.stdout


def test_a_read_only_token_is_named(run, fake: FakeOstraka) -> None:
    fake.routes[("POST", f"/api/incidents/{INCIDENT_ID}/comments")] = (
        403,
        {"detail": "This token is read-only."},
    )
    proc = run("comment", "OST-2026-001", "Isolated", "the", "host")
    assert proc.returncode == 4
    assert "read-only (an MCP token)" in proc.stderr


def test_a_conflict_is_explained(run, fake: FakeOstraka) -> None:
    fake.routes[("POST", f"/api/incidents/{INCIDENT_ID}/comments")] = (
        409,
        {"detail": "Someone else changed this first."},
    )
    proc = run("comment", "OST-2026-001", "hello")
    assert proc.returncode == 5
    assert "Conflict: Someone else changed this first." in proc.stderr


def test_comment_from_stdin(run, fake: FakeOstraka) -> None:
    fake.routes[("POST", f"/api/incidents/{INCIDENT_ID}/comments")] = (201, {"id": "c1"})
    proc = run("comment", "#OST-2026-001", stdin='Line one\n"quoted" and \\ backslash\n')
    assert proc.returncode == 0, proc.stderr
    [req] = fake.sent("POST", f"/api/incidents/{INCIDENT_ID}/comments")
    assert req.body == {"body": 'Line one\n"quoted" and \\ backslash'}


def test_unknown_reference(run, fake: FakeOstraka) -> None:
    proc = run("comment", "OST-2026-404", "hello")
    assert proc.returncode == 1
    assert "No incident OST-2026-404" in proc.stderr
    assert run("comment", "TSK-4", "hello").returncode == 2


def test_host_add_with_given_values(run, fake: FakeOstraka) -> None:
    fake.routes[("POST", f"/api/incidents/{INCIDENT_ID}/assets")] = (201, {"id": "a1", "ref": "AST-7"})
    proc = run(
        "host",
        "add",
        "OST-2026-001",
        "--hostname",
        "FS01",
        "--ip",
        "10.0.0.5",
        "--os",
        "Windows Server 2022",
        "--user",
        "svc_backup",
        "--criticality",
        "high",
        "--patient-zero",
    )
    assert proc.returncode == 0, proc.stderr
    [req] = fake.sent("POST", f"/api/incidents/{INCIDENT_ID}/assets")
    assert req.body == {
        "hostname": "FS01",
        "ip": "10.0.0.5",
        "os": "Windows Server 2022",
        "primary_user": "svc_backup",
        "criticality": "high",
        "is_patient_zero": True,
        "notes": "",
    }
    assert "Added AST-7 to OST-2026-001: FS01" in proc.stdout


def test_host_add_reads_this_machine(run, fake: FakeOstraka) -> None:
    fake.routes[("POST", f"/api/incidents/{INCIDENT_ID}/assets")] = (201, {"id": "a1", "ref": "AST-8"})
    proc = run("host", "add", "OST-2026-001")
    assert proc.returncode == 0, proc.stderr
    [req] = fake.sent("POST", f"/api/incidents/{INCIDENT_ID}/assets")
    assert req.body["hostname"]
    assert req.body["os"]
    assert req.body["primary_user"]
    assert req.body["is_patient_zero"] is False
    assert "Added with ost from the host itself" in req.body["notes"]


def test_tasks_mine_drops_the_team_queue(run, fake: FakeOstraka) -> None:
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
            {
                "ref": "TSK-2",
                "status": "open",
                "title": "Image WS042",
                "due_at": None,
                "assignee": None,
                "team": {"name": "Blue"},
                "incident": {"ref": "OST-2026-001"},
            },
        ],
    )
    everything = run("tasks")
    assert everything.returncode == 0, everything.stderr
    assert "TSK-1" in everything.stdout and "TSK-2" in everything.stdout
    assert "2026-11-12 09:30" in everything.stdout
    mine = run("tasks", "--mine")
    assert "TSK-1" in mine.stdout and "TSK-2" not in mine.stdout


def test_incidents_and_whoami(run, fake: FakeOstraka) -> None:
    fake.routes[("GET", "/api/incidents?status=open&kind=incident")] = (
        200,
        [
            {
                "ref": "OST-2026-001",
                "severity": "high",
                "tlp": "amber",
                "phase": "contain",
                "commander": {"name": "Ada Responder"},
                "title": "Ransom note on FS01",
            },
        ],
    )
    listed = run("incidents")
    assert listed.returncode == 0, listed.stderr
    assert "OST-2026-001" in listed.stdout and "Ransom note on FS01" in listed.stdout
    who = run("whoami")
    assert "Ada Responder <ada@example.org>" in who.stdout
    assert "responder, Blue" in who.stdout


def test_login_saves_url_and_token_privately(run, fake: FakeOstraka) -> None:
    proc = run("login", fake.url, stdin=f"{TOKEN}\n", token=False, env={"OSTRAKA_URL": ""})
    assert proc.returncode == 0, proc.stderr
    saved = run.home / ".config" / "ostraka" / "client"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    assert saved.read_text() == f"url={fake.url}\ntoken={TOKEN}\n"
    # The saved settings are used when the environment doesn't give any.
    who = run("whoami", token=False, env={"OSTRAKA_URL": ""})
    assert who.returncode == 0, who.stderr
    assert fake.requests[-1].headers["Authorization"] == f"Bearer {TOKEN}"


def test_login_refuses_something_that_isnt_ostraka(run, fake: FakeOstraka) -> None:
    fake.routes[("GET", "/api/meta")] = (200, {"name": "Something else"})
    proc = run("login", fake.url, stdin=f"{TOKEN}\n", token=False)
    assert proc.returncode == 1
    assert "doesn't answer as an Ostraka server" in proc.stderr
    assert not (run.home / ".config" / "ostraka" / "client").exists()


def test_plain_http_to_another_machine_is_refused(run, fake: FakeOstraka) -> None:
    proc = run("whoami", env={"OSTRAKA_URL": "http://ostraka.example.org"})
    assert proc.returncode == 1
    assert "plain HTTP" in proc.stderr
    assert fake.requests == []


@pytest.mark.parametrize("allow_http", ["", "1"])
def test_user_info_in_a_plain_http_address_is_refused(run, fake: FakeOstraka, allow_http: str) -> None:
    # Passes a naive loopback check, but curl would connect to evil.example and send the token in clear.
    proc = run(
        "whoami", env={"OSTRAKA_URL": "http://127.0.0.1:1@evil.example/", "OSTRAKA_ALLOW_HTTP": allow_http}
    )
    assert proc.returncode == 1
    assert "user info" in proc.stderr
    assert not (run.home.parent / "curl-argv.log").exists()


def test_an_unreachable_server(run) -> None:
    proc = run("whoami", env={"OSTRAKA_URL": "http://127.0.0.1:9"})
    assert proc.returncode == 1
    assert "Couldn't reach http://127.0.0.1:9" in proc.stderr
