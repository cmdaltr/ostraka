"""Shared fixtures for the release and install script tests.

Run with ``backend/.venv/bin/pytest tests/install``. The scripts run for real; only the commands
that would change this machine (apt-get, dnf, sudo, hdiutil, gh, docker) are replaced by fakes on
PATH that record how they were called.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "desktop"


def sha256(path: Path) -> str:
    """The hex SHA-256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_exe(path: Path, body: str) -> Path:
    """Write an executable shell script."""
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def docker_ok() -> bool:
    """Whether a Docker daemon answers, for the container tests."""
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


class FakeMachine:
    """A PATH of fake commands and an /etc/os-release, so install.sh believes it's another machine."""

    def __init__(self, base: Path) -> None:
        self.bin = base / "bin"
        self.bin.mkdir()
        self.log = base / "calls.log"
        self.log.touch()
        self.home = base / "home"
        self.home.mkdir()
        self.os_release = base / "os-release"
        self.set_os("Linux", "x86_64", 'ID=debian\nVERSION_ID="12"\n')
        # Anything that would install or need root only records its arguments
        for cmd in ("apt-get", "hdiutil", "ditto", "defaults", "open"):
            write_exe(self.bin / cmd, f'echo "{cmd} $*" >> "{self.log}"\n')
        # `sudo -v` (install.sh asks for the password up front) only records; anything else runs
        write_exe(
            self.bin / "sudo",
            f'echo "sudo $*" >> "{self.log}"\n[ "$1" = -v ] && exit 0\nexec "$@"\n',
        )

    def set_os(self, system: str, machine: str, os_release: str = "") -> None:
        """Pretend to be ``uname -s`` / ``uname -m`` with the given os-release contents."""
        write_exe(
            self.bin / "uname",
            f'case "$1" in -s) echo {system} ;; -m) echo {machine} ;; *) echo {system} ;; esac\n',
        )
        self.os_release.write_text(os_release)

    def calls(self) -> list[str]:
        """Every faked command run so far."""
        return self.log.read_text().splitlines()

    def env(self, **extra: str) -> dict[str, str]:
        """The environment for a script run on this machine."""
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "OSTRAKA_OS_RELEASE": str(self.os_release),
        }
        env.update(extra)
        return env


@pytest.fixture
def machine(tmp_path: Path) -> FakeMachine:
    """A fake Debian x86_64 machine; tests change its OS as they need."""
    (tmp_path / "machine").mkdir()
    return FakeMachine(tmp_path / "machine")


# The files of a source tree install.sh's tarball route uses: the stack's compose file, the version
# and the Linux launcher with what its menu entry needs
TREE_FILES = [
    "docker-compose.yml", "backend/ostraka/__init__.py", "desktop/linux/install.sh",
    "desktop/linux/desktop-entry.sh", "desktop/linux/ostraka-app.sh", "desktop/linux/ostraka.desktop.in",
    "desktop/icons/ostraka.png", "desktop/uninstall.sh",
]


def make_source_tarball(path: Path, version: str = "0.1.0", top: bool = True) -> Path:
    """A small source tarball shaped like desktop/source-tarball.sh's, under ostraka-<version>/ when ``top``."""
    prefix = f"ostraka-{version}/" if top else ""
    with tarfile.open(path, "w:gz") as t:
        for rel in TREE_FILES:
            src = ROOT / rel
            if rel == "backend/ostraka/__init__.py":
                data = f'"""ostraka."""\n\n__version__ = "{version}"\n'.encode()
                info = tarfile.TarInfo(prefix + rel)
                info.size = len(data)
                info.mode = 0o644
                t.addfile(info, io.BytesIO(data))
            elif src.exists():
                t.add(src, prefix + rel)
    return path


def release_names(version: str = "0.1.0") -> list[str]:
    """Every file a release has besides SHA256SUMS, named as scripts/release.sh names them."""
    return [
        f"Ostraka-{version}-macos-universal.dmg",
        f"Ostraka-{version}-windows-universal.msi",
        f"ostraka_{version}-1_all.deb",
        f"ostraka-{version}-source.tar.gz",
    ]


def make_release(folder: Path, version: str = "0.1.0", corrupt: str | None = None) -> Path:
    """A release folder with every package and SHA256SUMS; ``corrupt`` names one whose sum is wrong."""
    folder.mkdir(parents=True, exist_ok=True)
    names = release_names(version)
    lines = []
    for name in names:
        if name.endswith("-source.tar.gz"):
            make_source_tarball(folder / name, version)
        else:
            (folder / name).write_bytes(f"package {name}\n".encode())
        digest = sha256(folder / name)
        if name == corrupt:
            digest = "0" * 64
        lines.append(f"{digest}  {name}\n")
    (folder / "SHA256SUMS").write_text("".join(lines))
    return folder


def make_api(base: Path, release: Path, tag: str = "v0.1.0") -> str:
    """A file:// tree shaped like the GitHub API for one release, as both latest and its tag."""
    api = base / "api"
    rel = api / "repos" / "cmdaltr" / "ostraka" / "releases"
    (rel / "tags").mkdir(parents=True)
    assets = [
        {"name": f.name, "url": f.as_uri()} for f in sorted(release.iterdir()) if f.is_file()
    ]
    body = json.dumps({"tag_name": tag, "assets": assets})
    (rel / "latest").write_text(body)
    (rel / "tags" / tag).write_text(body)
    return api.as_uri()


def run(args: list[str], env: dict[str, str], cwd: Path | None = None, stdin: str | None = None):
    """Run a script and capture its output (never on a terminal, so no colour and no prompts)."""
    return subprocess.run(
        args, env=env, cwd=cwd, capture_output=True, text=True, input=stdin or "", timeout=120
    )
