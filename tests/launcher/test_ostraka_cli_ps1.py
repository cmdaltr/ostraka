"""`desktop/windows/ostraka-cli.ps1` with a fake `docker`, the PowerShell counterpart of test_ostraka_cli.py.

PowerShell isn't on a stock Mac, so this runs where `pwsh` is on PATH, or in Microsoft's PowerShell image
when OSTRAKA_TEST_PWSH_DOCKER=1 (the native arm64 one on Apple silicon: the default image crashes there
under emulation). Otherwise it's skipped. /api/health is answered by a small HttpListener in pwsh, as the
image has no Python, on the port the CLI asks.

    OSTRAKA_TEST_PWSH_DOCKER=1 backend/.venv/bin/pytest tests/launcher -q
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IMAGE = (
    "mcr.microsoft.com/powershell:7.5-azurelinux-3.0-arm64"
    if platform.machine() in ("arm64", "aarch64")
    else "mcr.microsoft.com/powershell:latest"
)
USE_DOCKER = os.environ.get("OSTRAKA_TEST_PWSH_DOCKER") == "1" and bool(shutil.which("docker"))

pytestmark = pytest.mark.skipif(
    not shutil.which("pwsh") and not USE_DOCKER, reason="needs pwsh, or OSTRAKA_TEST_PWSH_DOCKER=1"
)

FAKE_DOCKER = r"""#!/bin/sh
printf '%s | project=%s\n' "$*" "${COMPOSE_PROJECT_NAME:-}" >> "$T/docker.log"
case "$1" in
    info) exit 0 ;;
    compose)
        shift
        case "$1" in
            version|up|stop|logs) exit 0 ;;
            ps) echo 0123abcd; exit 0 ;;
            exec)
                case "$*" in *" ostraka has-admin") echo false; exit 0 ;; esac
                [ -n "${FAKE_EXEC_OUT:-}" ] && printf '%s\n' "$FAKE_EXEC_OUT"
                [ -n "${FAKE_EXEC_ERR:-}" ] && printf '%s\n' "$FAKE_EXEC_ERR" >&2
                exit "${FAKE_EXEC_EXIT:-0}" ;;
        esac
        exit 0 ;;
esac
exit 0
"""

# Answers every request with FAKE_BODY as FAKE_TYPE, then writes $T/listening
SERVE = r"""
$l = New-Object Net.HttpListener
$l.Prefixes.Add('http://127.0.0.1:24747/')
$l.Start()
Set-Content -LiteralPath (Join-Path $env:T 'listening') -Value 'yes'
while ($true) {
    $c = $l.GetContext()
    $bytes = [Text.Encoding]::UTF8.GetBytes($env:FAKE_BODY)
    $c.Response.ContentType = $env:FAKE_TYPE
    $c.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $c.Response.Close()
}
"""

RUN = r"""#!/bin/bash
T="$(cd "$(dirname "$0")" && pwd)"
export T PATH="$T/bin:$PATH"
if [ -n "${FAKE_BODY:-}" ]; then
    pwsh -NoProfile -File "$T/serve.ps1" &
    for _ in $(seq 1 100); do [ -f "$T/listening" ] && break; sleep 0.1; done
fi
pwsh -NoProfile -File "$T/tree/desktop/windows/ostraka-cli.ps1" "$@"
"""


class Cli:
    def __init__(self, tmp: Path, unpacked: bool = True) -> None:
        self.dir = tmp
        self.root = tmp / "tree"
        (self.root / "desktop/windows").mkdir(parents=True)
        (self.root / "backend/ostraka").mkdir(parents=True)
        shutil.copy2(REPO / "desktop/windows/ostraka-cli.ps1", self.root / "desktop/windows/ostraka-cli.ps1")
        shutil.copy2(REPO / ".env.example", self.root / ".env.example")
        shutil.copy2(REPO / "backend/ostraka/__init__.py", self.root / "backend/ostraka/__init__.py")
        if unpacked:
            (self.root / ".ostraka-version").write_text("0.1.0\n")
        (tmp / "bin").mkdir()
        self._exe(tmp / "bin/docker", FAKE_DOCKER)
        self._exe(tmp / "run.sh", RUN)
        (tmp / "serve.ps1").write_text(SERVE)

    @staticmethod
    def _exe(path: Path, body: str) -> None:
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        if USE_DOCKER:
            cmd = ["docker", "run", "--rm", "-v", f"{self.dir}:/t"]
            for key, value in env.items():
                cmd += ["-e", f"{key}={value}"]
            cmd += [IMAGE, "bash", "/t/run.sh", *args]
            environ = None
        else:
            cmd = ["bash", str(self.dir / "run.sh"), *args]
            environ = {**os.environ, **env}
        return subprocess.run(  # noqa: S603 - the script under test, with a fake docker
            cmd, capture_output=True, text=True, timeout=300, check=False, env=environ
        )

    def docker_calls(self) -> list[str]:
        log = self.dir / "docker.log"
        return log.read_text().splitlines() if log.exists() else []


HEALTHY = {"FAKE_BODY": '{"status": "ok", "version": "0.1.0"}', "FAKE_TYPE": "application/json"}


# ---- the compose project name lives in .env -----------------------------------------------------


def test_start_in_an_unpacked_tree_names_the_project_in_env(tmp_path):
    cli = Cli(tmp_path)
    out = cli.run("start", **HEALTHY)
    assert out.returncode == 0, out.stderr
    env = cli.env_file.read_text()
    assert env.endswith("\nCOMPOSE_PROJECT_NAME=ostraka-app\n")
    assert "\r" not in env
    assert stat.S_IMODE(cli.env_file.stat().st_mode) == 0o600
    # Compose reads it from .env, so nothing is exported over it
    assert "compose up -d --build | project=" in cli.docker_calls()


def test_a_checkouts_env_has_no_project_name(tmp_path):
    cli = Cli(tmp_path, unpacked=False)
    assert cli.run("start", **HEALTHY).returncode == 0
    assert "COMPOSE_PROJECT_NAME" not in cli.env_file.read_text()
    assert "compose up -d --build | project=" in cli.docker_calls()


def test_an_unpacked_trees_older_env_gets_the_project_name(tmp_path):
    cli = Cli(tmp_path)
    cli.env_file.write_text("POSTGRES_PASSWORD=mine")  # no newline at the end
    assert cli.run("stop").returncode == 0
    assert cli.env_file.read_text() == "POSTGRES_PASSWORD=mine\nCOMPOSE_PROJECT_NAME=ostraka-app\n"
    assert cli.docker_calls()[-1] == "compose stop | project="


def test_a_servers_own_project_name_is_honoured(tmp_path):
    cli = Cli(tmp_path)
    cli.env_file.write_text("COMPOSE_PROJECT_NAME=ostraka\n")
    assert cli.run("stop").returncode == 0
    assert cli.env_file.read_text() == "COMPOSE_PROJECT_NAME=ostraka\n"
    assert cli.docker_calls()[-1] == "compose stop | project="  # not overridden from the environment


# ---- health, first-admin, server init and app -----------------------------------------------------


def test_status_treats_a_non_json_answer_as_stopped(tmp_path):
    cli = Cli(tmp_path)
    page = {"FAKE_BODY": "<html>something else</html>", "FAKE_TYPE": "text/html"}
    out = cli.run("status", "--json", **page)
    assert out.returncode == 3, out.stderr
    assert json.loads(out.stdout)["running"] is False
    text = cli.run("status", **{**page, "FAKE_TYPE": "text/plain", "FAKE_BODY": "hello"})
    assert text.returncode == 3 and "stopped" in text.stdout


def test_status_running(tmp_path):
    out = Cli(tmp_path).run("status", "--json", **HEALTHY)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["running"] is True


@pytest.mark.parametrize("code", ["5", "1"])
def test_first_admin_passes_the_backends_exit_on(tmp_path, code):
    out = Cli(tmp_path).run("first-admin", "a@example.org", FAKE_EXEC_EXIT=code, FAKE_EXEC_ERR="refused")
    assert out.returncode == int(code) and out.stdout == ""


def test_first_admin_prints_the_local_link(tmp_path):
    out = Cli(tmp_path).run(
        "first-admin", "a@example.org", FAKE_EXEC_OUT="https://x.example.org/invite/tok_1"
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "http://127.0.0.1:24747/invite/tok_1"


def test_server_init_runs_the_windows_service_script(tmp_path):
    cli = Cli(tmp_path)
    missing = cli.run("server", "init")
    assert missing.returncode == 1 and "install-service.ps1 is missing" in missing.stderr
    setup = cli.root / "deploy/windows/install-service.ps1"
    setup.parent.mkdir(parents=True)
    setup.write_text(
        "[CmdletBinding(PositionalBinding = $false)]\n"
        "param([string] $Url = '', [switch] $NoStart,\n"
        "      [Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest = @())\n"
        "Write-Output \"url=$Url nostart=$NoStart rest=$($Rest -join ',')\"\n"
    )
    out = cli.run("server", "init", "-Url", "ir.example.org", "-nostart", "--hostname", "x")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "url=ir.example.org nostart=True rest=--hostname,x"
    assert cli.run("server").returncode == 2


@pytest.mark.parametrize(("body", "code"), [("Write-Output 'done'", 0), ("exit 7", 7), ("throw 'no'", 1)])
def test_app_install_exits_with_the_installers_code(tmp_path, body, code):
    cli = Cli(tmp_path)
    installer = cli.root / "desktop/windows/install.ps1"
    installer.write_text(body + "\n")
    out = cli.run("app", "install")
    assert out.returncode == code, out.stderr
    assert "LASTEXITCODE" not in out.stderr  # StrictMode used to trip over it


def test_app_uninstall_passes_the_switch_by_name(tmp_path):
    cli = Cli(tmp_path)
    installer = cli.root / "desktop/windows/install.ps1"
    installer.write_text('param([switch] $Uninstall)\nWrite-Output "uninstall=$Uninstall"\n')
    out = cli.run("app", "uninstall")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "uninstall=True"
