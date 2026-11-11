"""deploy/linux/uninstall.sh against a fake docker that records what it was asked to do.

Run with backend/.venv/bin/pytest deploy/tests.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "linux" / "uninstall.sh"


@pytest.fixture
def server(tmp_path: Path) -> Path:
    tree = tmp_path / "opt" / "ostraka"
    (tree / "backend").mkdir(parents=True)
    (tree / "docker-compose.yml").write_text("services: {}\n")
    (tree / ".env").write_text(
        "POSTGRES_PASSWORD=x\nCOMPOSE_PROJECT_NAME=ostraka\n"
        "COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.caddy.yml\n"
    )
    return tree


def run(tmp_path: Path, *args: str, volumes: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the uninstaller with a docker on PATH that logs project|cwd|args and lists `volumes`."""
    log = tmp_path / "docker.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s|%s\\n" "${{COMPOSE_PROJECT_NAME:-}}" "$PWD" "$*" >> "{log}"\n'
        f'case "$1 $2" in "volume ls") printf "%s" "{volumes}" ;; esac\n'
    )
    docker.chmod(0o755)
    proc = subprocess.run(  # noqa: S603 (bash from PATH, as a user runs it)
        ["bash", str(SCRIPT), *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
        # No controlling terminal, so the confirmation can't be asked for.
        start_new_session=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "COMPOSE_PROJECT_NAME": "ostraka-app"},
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def test_stops_the_stack_and_keeps_the_data(tmp_path: Path, server: Path) -> None:
    proc, calls = run(tmp_path, "--dir", str(server))
    assert proc.returncode == 0, proc.stderr
    assert calls == [f"ostraka|{server}|compose down --remove-orphans"]
    assert (server / ".env").exists()
    assert "kept data" in proc.stdout
    assert "left the code" in proc.stdout
    assert (server / "docker-compose.yml").exists()


def test_purge_needs_confirming(tmp_path: Path, server: Path) -> None:
    proc, calls = run(tmp_path, "--dir", str(server), "--purge")
    assert proc.returncode == 2
    assert "--yes" in proc.stderr
    assert calls == []
    assert (server / ".env").exists()


def test_purge_with_yes_removes_volumes_and_env_but_not_the_code(tmp_path: Path, server: Path) -> None:
    proc, calls = run(tmp_path, "--dir", str(server), "--purge", "--yes", volumes="ostraka_caddy_data\n")
    assert proc.returncode == 0, proc.stderr
    assert calls[0] == f"ostraka|{server}|compose down --volumes --remove-orphans"
    assert calls[1].endswith("|volume ls -q --filter label=com.docker.compose.project=ostraka")
    assert calls[2].endswith("|volume rm ostraka_caddy_data")
    assert not (server / ".env").exists()
    assert (server / "docker-compose.yml").exists() and (server / "backend").is_dir()
    assert "80 and 443" in proc.stdout


def test_an_older_install_uses_the_folder_name(tmp_path: Path, server: Path) -> None:
    (server / ".env").write_text("POSTGRES_PASSWORD=x\n")
    proc, calls = run(tmp_path, "--dir", str(server))
    assert proc.returncode == 0, proc.stderr
    assert calls == [f"ostraka|{server}|compose down --remove-orphans"]
    renamed = server.parent / "ir"
    server.rename(renamed)
    (tmp_path / "docker.log").unlink()
    proc, calls = run(tmp_path, "--dir", str(renamed))
    assert calls == [f"ir|{renamed}|compose down --remove-orphans"]


def test_not_a_tree(tmp_path: Path) -> None:
    proc, calls = run(tmp_path, "--dir", str(tmp_path))
    assert proc.returncode == 1
    assert "isn't an Ostraka tree" in proc.stderr
    assert calls == []


def test_unknown_option(tmp_path: Path, server: Path) -> None:
    proc, _ = run(tmp_path, "--dir", str(server), "--everything")
    assert proc.returncode == 2
    assert "Unknown option" in proc.stderr
