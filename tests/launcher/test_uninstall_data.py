"""`desktop/uninstall.sh --data` and `uninstall.ps1 -Data`: which compose project they delete, and
which folders they refuse to.

uninstall.sh runs as on Linux with no package installed, with fake `docker` and `uname` on a PATH of
links, so a real Docker is never reached. uninstall.ps1 runs where `pwsh` is, or in Microsoft's image
with OSTRAKA_TEST_PWSH_DOCKER=1 (see test_ostraka_cli_ps1.py).

    backend/.venv/bin/pytest tests/launcher -q
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_ostraka_cli_ps1 import IMAGE, USE_DOCKER

REPO = Path(__file__).resolve().parents[2]
TOOLS = "sed tail head tr basename dirname du cut rm rmdir id date cat sleep mkdir".split()

# Volumes exist for projects ostraka (a checkout, or a server) and ostraka-app (an unpacked tree)
FAKE_DOCKER = r"""#!/bin/sh
printf '%s | project=%s\n' "$*" "${COMPOSE_PROJECT_NAME:-}" >> "$FAKE_LOG"
case "$*" in
    info) exit 0 ;;
    *"label=com.docker.compose.project=ostraka") echo ostraka_pgdata; echo ostraka_redis ;;
    *"label=com.docker.compose.project=ostraka-app") echo ostraka-app_pgdata ;;
esac
exit 0
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _tree(folder: Path, *, marker: bool = True, env: str | None = None, git: bool = False) -> Path:
    folder.mkdir(parents=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    if marker:
        (folder / ".ostraka-version").write_text("0.1.0\n")
    if env is not None:
        (folder / ".env").write_text(env)
    if git:
        (folder / ".git").mkdir()
    return folder


class Uninstaller:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        (self.bin / "sh").symlink_to(shutil.which("sh"))
        for tool in TOOLS:
            found = shutil.which(tool)
            assert found, f"{tool} is needed for these tests"
            (self.bin / tool).symlink_to(found)
        _exe(self.bin / "docker", FAKE_DOCKER)
        _exe(self.bin / "uname", "#!/bin/sh\necho Linux\n")
        self.log = tmp / "docker.log"
        self.home = tmp / "home"
        self.home.mkdir()

    def run(self, ostraka_home: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        environ = {
            "PATH": str(self.bin),
            "HOME": str(self.home),
            "OSTRAKA_HOME": str(ostraka_home),
            "FAKE_LOG": str(self.log),
            **env,
        }
        return subprocess.run(  # noqa: S603 - the script under test, with fakes on PATH
            [str(self.bin / "sh"), str(REPO / "desktop/uninstall.sh"), *args],
            env=environ,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def uninstaller(tmp_path: Path) -> Uninstaller:
    return Uninstaller(tmp_path)


def _down(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("compose down")]


def test_an_unpacked_tree_is_project_ostraka_app_even_in_a_folder_called_ostraka(uninstaller, tmp_path):
    # An unpacked tree from before .env carried the name: the marker is the fallback, not the folder
    home = _tree(tmp_path / "ostraka", env="POSTGRES_PASSWORD=x\n")
    out = uninstaller.run(home, "--data", "--yes")
    assert out.returncode == 0, out.stderr
    calls = uninstaller.calls()
    assert "volume ls -q --filter label=com.docker.compose.project=ostraka-app | project=ostraka-app" in calls
    assert _down(calls) == ["compose down -v --rmi local --remove-orphans | project=ostraka-app"]
    assert "ostraka-app_pgdata" in out.stdout and "ostraka_pgdata" not in out.stdout
    assert not home.exists()


def test_the_project_comes_from_env_first(uninstaller, tmp_path):
    home = _tree(tmp_path / "Ostraka", env='COMPOSE_PROJECT_NAME="ostraka"\n')  # a server's own name
    assert uninstaller.run(home, "--data", "--yes").returncode == 0
    assert _down(uninstaller.calls()) == ["compose down -v --rmi local --remove-orphans | project=ostraka"]


def test_a_stray_project_in_the_shell_is_ignored(uninstaller, tmp_path):
    home = _tree(tmp_path / "Ostraka", env="COMPOSE_PROJECT_NAME=ostraka-app\n")
    assert uninstaller.run(home, "--data", "--yes", COMPOSE_PROJECT_NAME="somebody-else").returncode == 0
    assert all("somebody-else" not in c for c in uninstaller.calls())


def test_a_folder_without_the_marker_is_refused(uninstaller):
    # OSTRAKA_HOME=$HOME: never deleted, and its volumes aren't touched either
    (uninstaller.home / "docker-compose.yml").write_text("services: {}\n")
    (uninstaller.home / "notes.txt").write_text("mine\n")
    out = uninstaller.run(uninstaller.home, "--data", "--yes")
    assert out.returncode == 1
    assert ".ostraka-version" in out.stderr
    assert (uninstaller.home / "notes.txt").exists()
    assert not _down(uninstaller.calls())


def test_without_data_a_folder_without_the_marker_is_only_reported(uninstaller):
    out = uninstaller.run(uninstaller.home)
    assert out.returncode == 0, out.stderr
    assert uninstaller.home.exists() and not _down(uninstaller.calls())


def test_a_checkout_keeps_its_folder_and_loses_only_its_volumes(uninstaller, tmp_path):
    home = _tree(tmp_path / "ostraka", marker=False, git=True)
    out = uninstaller.run(home, "--data", "--yes")
    assert out.returncode == 0, out.stderr
    assert _down(uninstaller.calls()) == ["compose down -v --rmi local --remove-orphans | project=ostraka"]
    assert home.exists()


def test_nothing_there_is_nothing_to_delete(uninstaller, tmp_path):
    out = uninstaller.run(tmp_path / "absent", "--data", "--yes")
    assert out.returncode == 0 and "Nothing to delete" in out.stdout


# ---- uninstall.ps1 ------------------------------------------------------------------------------

needs_pwsh = pytest.mark.skipif(
    not shutil.which("pwsh") and not USE_DOCKER, reason="needs pwsh, or OSTRAKA_TEST_PWSH_DOCKER=1"
)

RUN_PS1 = r"""#!/bin/bash
T="$(cd "$(dirname "$0")" && pwd)"
export FAKE_LOG="$T/docker.log" PATH="$T/bin:$PATH" APPDATA="$T/appdata" TEMP="$T" OSTRAKA_HOME="$T/$1"
shift
pwsh -NoProfile -File "$T/uninstall.ps1" "$@"
"""


def _ps1(tmp: Path, folder: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    shutil.copy2(REPO / "desktop/uninstall.ps1", tmp / "uninstall.ps1")
    (tmp / "bin").mkdir(exist_ok=True)
    _exe(tmp / "bin/docker", FAKE_DOCKER)
    _exe(tmp / "run.sh", RUN_PS1)
    if USE_DOCKER:
        cmd = ["docker", "run", "--rm", "-v", f"{tmp}:/t"]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [IMAGE, "bash", "/t/run.sh", folder, *args]
        environ = None
    else:
        cmd = ["bash", str(tmp / "run.sh"), folder, *args]
        environ = {**os.environ, **env}
    return subprocess.run(  # noqa: S603 - the script under test, with a fake docker
        cmd, capture_output=True, text=True, timeout=300, check=False, env=environ, stdin=subprocess.DEVNULL
    )


@needs_pwsh
def test_ps1_an_unpacked_tree_is_project_ostraka_app(tmp_path):
    home = _tree(tmp_path / "ostraka", env="POSTGRES_PASSWORD=x\n")
    out = _ps1(tmp_path, "ostraka", "-Data", "-Yes", COMPOSE_PROJECT_NAME="somebody-else")
    assert out.returncode == 0, out.stdout + out.stderr
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert "volume ls -q --filter label=com.docker.compose.project=ostraka-app | project=ostraka-app" in calls
    assert _down(calls) == ["compose down -v --rmi local --remove-orphans | project=ostraka-app"]
    assert not home.exists()


@needs_pwsh
def test_ps1_the_project_comes_from_env_first(tmp_path):
    _tree(tmp_path / "Ostraka", env="COMPOSE_PROJECT_NAME=ostraka\r\n")
    assert _ps1(tmp_path, "Ostraka", "-Data", "-Yes").returncode == 0
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert _down(calls) == ["compose down -v --rmi local --remove-orphans | project=ostraka"]


@needs_pwsh
def test_ps1_a_folder_without_the_marker_is_refused(tmp_path):
    home = _tree(tmp_path / "profile", marker=False)
    (home / "notes.txt").write_text("mine\n")
    out = _ps1(tmp_path, "profile", "-Data", "-Yes")
    assert out.returncode == 1
    assert ".ostraka-version" in out.stderr
    assert (home / "notes.txt").exists()
    log = tmp_path / "docker.log"
    assert not log.exists() or not _down(log.read_text().splitlines())


@needs_pwsh
def test_ps1_a_checkout_keeps_its_folder(tmp_path):
    home = _tree(tmp_path / "ostraka", marker=False, git=True)
    out = _ps1(tmp_path, "ostraka", "-Data", "-Yes")
    assert out.returncode == 0, out.stdout + out.stderr
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert _down(calls) == ["compose down -v --rmi local --remove-orphans | project=ostraka"]
    assert home.exists()
