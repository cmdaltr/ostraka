"""deploy/linux/install.sh's arguments, tree placement and .env, without Docker: the script is sourced and its
functions called one at a time. A real run (Docker, the build, the first admin) is done separately in a
docker:dind container; see the phase 3b notes.

Run with backend/.venv/bin/pytest deploy/tests.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "linux" / "install.sh"
REPO = Path(__file__).resolve().parents[2]


def bash(snippet: str, cwd: Path) -> subprocess.CompletedProcess:
    """Source the script (its main doesn't run when sourced) and run a snippet with its functions."""
    return subprocess.run(  # noqa: S603 (bash from PATH, as a user runs it)
        ["bash", "-c", f'set -euo pipefail; source "{SCRIPT}"; {snippet}'],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "LC_ALL": "C"},
    )


def run_script(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 (bash from PATH, as a user runs it)
        ["bash", str(SCRIPT), *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def fake_tree(root: Path) -> Path:
    tree = root / "src"
    for d in ("backend/ostraka", "frontend", "deploy"):
        (tree / d).mkdir(parents=True)
    (tree / "docker-compose.yml").write_text("services: {}\n")
    (tree / "backend/ostraka/__init__.py").write_text('__version__ = "9.8.7"\n')
    shutil.copy(REPO / ".env.example", tree / ".env.example")
    (tree / ".env").write_text("POSTGRES_PASSWORD=from-the-source-tree\n")  # must never travel
    (tree / "frontend/node_modules").mkdir()
    (tree / "frontend/node_modules/junk.js").write_text("x")
    return tree


def test_help(tmp_path: Path) -> None:
    proc = run_script("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert "Usage: deploy/linux/install.sh" in proc.stdout
    for option in ("--url", "--admin", "--dir", "--source", "--caddy", "--no-start"):
        assert option in proc.stdout


@pytest.mark.parametrize(
    ("args", "url"),
    [
        ("ostraka.example.org", "https://ostraka.example.org"),
        ("--url https://ir.example.org/", "https://ir.example.org"),
        ("--url=http://127.0.0.1:24747", "http://127.0.0.1:24747"),
    ],
)
def test_the_address(tmp_path: Path, args: str, url: str) -> None:
    proc = bash(f'parse_args {args}; printf "%s" "$URL"', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == url


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ("--bogus", "Unknown option '--bogus'"),
        ("a.example b.example", "One address only"),
        ("--url https://x.example/app", "drop the path"),
        ("--admin not-an-email", "doesn't look like an email address"),
        ("--caddy=wildcard", "--caddy is acme or internal"),
    ],
)
def test_bad_arguments(tmp_path: Path, args: str, message: str) -> None:
    proc = run_script(*args.split(), cwd=tmp_path)
    assert proc.returncode == 2
    assert message in proc.stderr


def test_caddy_mode_is_optional(tmp_path: Path) -> None:
    alone = bash('parse_args --caddy ostraka.example.org; printf "%s %s" "$CADDY" "$URL"', tmp_path)
    assert alone.stdout == "acme https://ostraka.example.org"
    internal = bash('parse_args --caddy internal --dir rel; printf "%s %s" "$CADDY" "$DIR"', tmp_path)
    assert internal.stdout.startswith("internal /") and internal.stdout.endswith("/rel")


def test_a_tree_is_copied_without_its_env_or_node_modules(tmp_path: Path) -> None:
    tree = fake_tree(tmp_path)
    dest = tmp_path / "opt" / "ostraka"
    proc = bash(f'SOURCE="{tree}"; DIR="{dest}"; place_tree', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (dest / "docker-compose.yml").exists()
    assert (dest / ".env.example").exists()
    assert not (dest / ".env").exists()
    assert not (dest / "frontend/node_modules").exists()
    assert (dest / ".ostraka-version").read_text() == "9.8.7\n"


def test_a_git_checkout_is_left_alone(tmp_path: Path) -> None:
    tree = fake_tree(tmp_path)
    dest = tmp_path / "checkout"
    shutil.copytree(tree, dest)
    (dest / ".git").mkdir()
    (tree / "docker-compose.yml").write_text("services: {changed: {}}\n")
    proc = bash(f'SOURCE="{tree}"; DIR="{dest}"; place_tree', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "leaving its files alone" in proc.stdout
    assert (dest / "docker-compose.yml").read_text() == "services: {}\n"


def test_a_tarball_with_a_top_folder(tmp_path: Path) -> None:
    tree = fake_tree(tmp_path)
    top = tmp_path / "ostraka-9.8.7"
    tree.rename(top)
    tarball = tmp_path / "ostraka-9.8.7.tar.gz"
    subprocess.run(["tar", "-czf", str(tarball), "-C", str(tmp_path), top.name], check=True)  # noqa: S603, S607
    dest = tmp_path / "unpacked"
    proc = bash(f'SOURCE="{tarball}"; DIR="{dest}"; place_tree', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (dest / "docker-compose.yml").exists()


def env_of(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_env_written_with_random_secrets_and_the_address(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    shutil.copy(REPO / ".env.example", dest / ".env.example")
    proc = bash(f'DIR="{dest}"; URL=https://ostraka.example.org; write_env', tmp_path)
    assert proc.returncode == 0, proc.stderr
    env_file = dest / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    env = env_of(env_file)
    assert len(env["POSTGRES_PASSWORD"]) == 48
    assert len(env["OSTRAKA_SECRET_KEY"]) == 64
    assert env["OSTRAKA_PUBLIC_URL"] == "https://ostraka.example.org"
    assert "OSTRAKA_SMTP_HOST" in env  # the rest of .env.example is kept
    assert "COMPOSE_FILE" not in env
    assert env["POSTGRES_PASSWORD"] not in proc.stdout + proc.stderr
    assert env["OSTRAKA_SECRET_KEY"] not in proc.stdout + proc.stderr


def test_running_again_keeps_the_secrets(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    shutil.copy(REPO / ".env.example", dest / ".env.example")
    bash(f'DIR="{dest}"; URL=https://one.example.org; write_env', tmp_path)
    first = env_of(dest / ".env")
    (dest / ".env").write_text((dest / ".env").read_text() + "OSTRAKA_SMTP_HOST=mail.example.org\n")
    proc = bash(f'DIR="{dest}"; URL=https://two.example.org; write_env', tmp_path)
    assert proc.returncode == 0, proc.stderr
    second = env_of(dest / ".env")
    assert second["POSTGRES_PASSWORD"] == first["POSTGRES_PASSWORD"]
    assert second["OSTRAKA_SECRET_KEY"] == first["OSTRAKA_SECRET_KEY"]
    assert second["OSTRAKA_PUBLIC_URL"] == "https://two.example.org"
    assert second["OSTRAKA_SMTP_HOST"] == "mail.example.org"
    assert (dest / ".env").read_text().count("OSTRAKA_PUBLIC_URL=") == 1


def test_no_address_means_links_to_this_computer(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    shutil.copy(REPO / ".env.example", dest / ".env.example")
    proc = bash(f'DIR="{dest}"; write_env', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert env_of(dest / ".env")["OSTRAKA_PUBLIC_URL"] == "http://127.0.0.1:24747"


def test_caddy_on_and_off(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    shutil.copy(REPO / ".env.example", dest / ".env.example")
    on = bash(f'DIR="{dest}"; URL=https://ostraka.example.org; CADDY=internal; write_env', tmp_path)
    assert on.returncode == 0, on.stderr
    env = env_of(dest / ".env")
    assert env["COMPOSE_FILE"] == "docker-compose.yml:deploy/docker-compose.caddy.yml"
    assert env["OSTRAKA_CADDY_CERTS"] == "--internal-certs"
    off = bash(f'DIR="{dest}"; CADDY=off; write_env', tmp_path)
    assert off.returncode == 0, off.stderr
    env = env_of(dest / ".env")
    assert "COMPOSE_FILE" not in env and "OSTRAKA_CADDY_CERTS" not in env


@pytest.mark.parametrize("url", ["http://ostraka.example.org", "https://ostraka.example.org:8443"])
def test_caddy_needs_https_on_443(tmp_path: Path, url: str) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    proc = bash(f'DIR="{dest}"; URL={url}; CADDY=acme; write_env', tmp_path)
    assert proc.returncode == 2
    assert "--caddy" in proc.stderr


def test_the_caddy_compose_file_is_where_env_says() -> None:
    compose = REPO / "deploy" / "docker-compose.caddy.yml"
    text = compose.read_text()
    assert "--to web:8080" in text and "${OSTRAKA_PUBLIC_URL" in text


def test_a_release_sized_tarball_survives_pipefail(tmp_path: Path) -> None:
    # `tar -t | head -1` under pipefail dies with SIGPIPE (141) once the listing outgrows the pipe
    # buffer, as a real release's does. Long names keep the entry count, and the time, down.
    tree = fake_tree(tmp_path)
    tarball = tmp_path / "ostraka-9.8.7.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(tree, arcname="ostraka-9.8.7")
        for i in range(700):
            member = tarfile.TarInfo(f"ostraka-9.8.7/frontend/src/{'nested-folder/' * 12}file-{i:04d}.tsx")
            tar.addfile(member, io.BytesIO(b""))
    listing = subprocess.run(["tar", "-tzf", str(tarball)], capture_output=True, check=True)  # noqa: S603, S607
    assert len(listing.stdout) > 128 * 1024  # twice a Linux pipe buffer
    # GNU tar dies of the SIGPIPE; macOS's bsdtar shrugs it off, so a shim writes the listing as GNU tar
    # does, through a process that the signal kills.
    real_tar = shutil.which("tar")
    bin_dir = fake_bin(
        tmp_path,
        "tar",
        f'case "$1" in -t*) out=$(mktemp); "{real_tar}" "$@" >"$out" || exit\n'
        'cat "$out"; rc=$?; rm -f "$out"; exit $rc ;; esac\n'
        f'exec "{real_tar}" "$@"',
    )
    old_way = bash(f'PATH="{bin_dir}:$PATH"; first=$(tar -tzf "{tarball}" | head -1)', tmp_path)
    assert old_way.returncode == 141  # the shim reproduces what Debian 12 did
    dest = tmp_path / "unpacked"
    proc = bash(f'PATH="{bin_dir}:$PATH"; SOURCE="{tarball}"; DIR="{dest}"; place_tree', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (dest / "docker-compose.yml").exists()
    assert (dest / "frontend/src" / ("nested-folder/" * 12) / "file-0699.tsx").exists()


def test_a_directory_with_other_files_is_refused(tmp_path: Path) -> None:
    tree = fake_tree(tmp_path)
    dest = tmp_path / "home-of-something-else"
    dest.mkdir()
    (dest / "notes.txt").write_text("mine\n")
    proc = bash(f'SOURCE="{tree}"; DIR="{dest}"; place_tree', tmp_path)
    assert proc.returncode == 1
    assert "aren't an Ostraka tree" in proc.stderr
    assert sorted(p.name for p in dest.iterdir()) == ["notes.txt"]


def test_an_empty_directory_or_an_earlier_tree_is_used(tmp_path: Path) -> None:
    tree = fake_tree(tmp_path)
    dest = tmp_path / "empty"
    dest.mkdir()
    first = bash(f'SOURCE="{tree}"; DIR="{dest}"; place_tree', tmp_path)
    assert first.returncode == 0, first.stderr
    (tree / "docker-compose.yml").write_text("services: {newer: {}}\n")
    again = bash(f'SOURCE="{tree}"; DIR="{dest}"; place_tree', tmp_path)
    assert again.returncode == 0, again.stderr
    assert (dest / "docker-compose.yml").read_text() == "services: {newer: {}}\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_someone_elses_directory_isnt_chowned(tmp_path: Path) -> None:
    calls = tmp_path / "sudo.log"
    sudo = tmp_path / "fake-sudo"
    sudo.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\nexec "$@"\n')
    sudo.chmod(0o755)
    dest = tmp_path / "locked"
    dest.mkdir()
    dest.chmod(0o555)
    try:
        proc = bash(f'SUDO="{sudo}"; DIR="{dest}"; make_dir', tmp_path)
    finally:
        dest.chmod(0o755)
    assert proc.returncode == 1
    assert "isn't writable" in proc.stderr
    assert not calls.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_only_a_directory_made_here_is_chowned(tmp_path: Path) -> None:
    calls = tmp_path / "sudo.log"
    sudo = tmp_path / "fake-sudo"
    # Stands in for sudo: records the call and makes the directory as this user would, then chown is a no-op.
    sudo.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n'
        f'[ "$1" = chown ] && exit 0\nchmod 755 "{tmp_path}/parent"\nexec "$@"\n'
    )
    sudo.chmod(0o755)
    parent = tmp_path / "parent"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        proc = bash(f'SUDO="{sudo}"; DIR="{parent}/ostraka"; make_dir', tmp_path)
    finally:
        parent.chmod(0o755)
    assert proc.returncode == 0, proc.stderr
    lines = calls.read_text().splitlines()
    assert lines[0] == f"mkdir -p {parent}/ostraka"
    assert lines[1].startswith("chown ") and lines[1].endswith(f" {parent}/ostraka")


def test_the_compose_project_is_written_and_kept(tmp_path: Path) -> None:
    dest = tmp_path / "srv-ir"
    dest.mkdir()
    shutil.copy(REPO / ".env.example", dest / ".env.example")
    proc = bash(f'DIR="{dest}"; URL=https://ostraka.example.org; write_env; printf "%s" "$PROJECT"', tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert env_of(dest / ".env")["COMPOSE_PROJECT_NAME"] == "ostraka"
    assert proc.stdout.endswith("ostraka")
    # An admin's own choice is left alone: the name is what the data volumes are called after.
    (dest / ".env").write_text(
        (dest / ".env").read_text().replace("COMPOSE_PROJECT_NAME=ostraka", "COMPOSE_PROJECT_NAME=ir")
    )
    again = bash(f'DIR="{dest}"; write_env; printf "%s" "$PROJECT"', tmp_path)
    assert again.returncode == 0, again.stderr
    assert env_of(dest / ".env")["COMPOSE_PROJECT_NAME"] == "ir"
    assert again.stdout.endswith("OSTRAKA_PUBLIC_URL=https://ostraka.example.org\nir")


def fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text(f"#!/bin/sh\n{body}\n")
    tool.chmod(0o755)
    return bin_dir


def test_setups_own_compose_calls_use_the_project(tmp_path: Path) -> None:
    log = tmp_path / "docker.log"
    bin_dir = fake_bin(
        tmp_path, "docker", f'printf "%s|%s|%s\\n" "$COMPOSE_PROJECT_NAME" "$PWD" "$*" >> "{log}"'
    )
    dest = tmp_path / "d"
    dest.mkdir()
    proc = bash(
        f'PATH="{bin_dir}:$PATH"; export COMPOSE_PROJECT_NAME=ostraka-app; DIR="{dest}"; compose ps', tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    assert log.read_text() == f"ostraka|{dest}|compose ps\n"


def test_a_firewall_that_refuses_is_a_warning(tmp_path: Path) -> None:
    bin_dir = fake_bin(tmp_path, "firewall-cmd", 'case "$*" in --state) exit 0 ;; *) exit 1 ;; esac')
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / ".env").write_text("COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.caddy.yml\n")
    proc = bash(
        f'PATH="{bin_dir}:$PATH"; DIR="{dest}"; ENV_FILE="{dest}/.env"; open_firewall; echo carried-on',
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Couldn't open 80 and 443 in firewalld" in proc.stderr
    assert proc.stdout.strip().endswith("carried-on")
