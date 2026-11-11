"""scripts/release.sh and desktop/source-tarball.sh, in a scratch git repository with stub packagers."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from conftest import APP, ROOT, make_release, release_names, run, sha256, write_exe

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.org",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.org",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={**os.environ, **GIT_ENV},
    ).stdout


STUB = """# A stand-in packager with the real ones' interface: [--arch A] --version V --source TARBALL OUTDIR
set -eu
a=none
while [ $# -gt 0 ]; do
    case "$1" in
        --arch) a=$2; shift 2 ;; --version) v=$2; shift 2 ;; --source) s=$2; shift 2 ;; *) o=$1; shift ;;
    esac
done
test -f "$s"
echo "built from $(basename "$s") for $a" > "$o/{name}"
"""
DEB = "ostraka_0.1.0-1_all.deb"


def make_repo(tmp_path: Path, version: str = "0.1.0") -> Path:
    """A committed repository holding the release scripts, the given version and a stub .deb packager."""
    r = tmp_path / "repo"
    (r / "backend/ostraka").mkdir(parents=True)
    (r / "backend/ostraka/__init__.py").write_text(f'"""x."""\n\n__version__ = "{version}"\n')
    (r / "desktop/linux").mkdir(parents=True)
    (r / "scripts").mkdir()
    shutil.copy(APP / "source-tarball.sh", r / "desktop/source-tarball.sh")
    shutil.copy(ROOT / "scripts/release.sh", r / "scripts/release.sh")
    write_exe(r / "desktop/linux/package-deb.sh", STUB.replace("{name}", "ostraka_${v}-1_all.deb"))
    (r / ".gitignore").write_text("dist/\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", ".")
    git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """make_repo at version 0.1.0."""
    return make_repo(tmp_path)


@pytest.fixture
def tools(tmp_path: Path) -> tuple[Path, Path]:
    """A PATH with dpkg-deb (so the .deb builds here), a docker that isn't running, and a recording gh."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "gh.log"
    write_exe(bin_ / "dpkg-deb", "exit 0\n")
    write_exe(bin_ / "docker", "exit 1\n")
    write_exe(
        bin_ / "gh",
        f'echo "gh $*" >> "{log}"\ncase "$2" in view) exit 1 ;; esac\nexit 0\n',
    )
    return bin_, log


def env_with(bin_: Path) -> dict[str, str]:
    return {**os.environ, **GIT_ENV, "PATH": f"{bin_}:{os.environ['PATH']}"}


def release(repo: Path, bin_: Path, *args: str):
    return run(["bash", str(repo / "scripts/release.sh"), *args], env_with(bin_), cwd=repo)


def test_refuses_a_version_that_isnt_the_code_s(repo, tools):
    r = release(repo, tools[0], "--version", "0.2.0")
    assert r.returncode != 0
    assert "doesn't match backend/ostraka/__init__.py (0.1.0)" in r.stderr
    assert not (repo / "dist").exists()


def test_refuses_a_dirty_tree(repo, tools):
    (repo / "backend/ostraka/__init__.py").write_text('__version__ = "0.1.0"\n# edited\n')
    r = release(repo, tools[0], "--version", "0.1.0")
    assert r.returncode != 0
    assert "uncommitted changes" in r.stderr
    assert not (repo / "dist").exists()


def test_builds_what_it_can_and_checksums_it(repo, tools):
    (repo / "notes.txt").write_text("untracked: can't reach a package, so it doesn't block a release")
    r = release(repo, tools[0], "--version", "v0.1.0")
    assert r.returncode == 0, r.stdout + r.stderr
    out = repo / "dist/0.1.0"
    assert "desktop/macos/package.sh isn't written yet" in r.stdout
    assert "desktop/windows/package-msi.sh isn't written yet" in r.stdout
    assert (out / DEB).read_text() == "built from ostraka-0.1.0-source.tar.gz for none\n"
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    assert set(sums) == {"ostraka-0.1.0-source.tar.gz", DEB}
    for name, digest in sums.items():
        assert sha256(out / name) == digest
    assert not tools[1].exists(), "gh is only used with --upload"


def test_a_packager_that_leaves_the_wrong_name_fails(repo, tools):
    write_exe(repo / "desktop/linux/package-deb.sh", 'for a; do o=$a; done; touch "$o/ostraka.deb"\n')
    git(repo, "commit", "-qam", "wrong name")
    r = release(repo, tools[0], "--version", "0.1.0")
    assert r.returncode != 0
    assert "left no ostraka_0.1.0-1_all.deb" in r.stderr
    assert not (repo / "dist/0.1.0/SHA256SUMS").exists()


def test_skips_the_msi_when_docker_isnt_running(repo, tools):
    (repo / "desktop/windows").mkdir()
    write_exe(repo / "desktop/windows/package-msi.sh", "exit 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "msi")
    r = release(repo, tools[0], "--version", "0.1.0")
    assert r.returncode == 0, r.stderr
    assert "needs Docker running" in r.stdout


def test_upload_makes_a_draft_at_the_pushed_commit(repo, tools, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    r = release(repo, tools[0], "--version", "0.1.0", "--upload")
    assert r.returncode != 0
    assert "isn't pushed" in r.stderr
    git(repo, "push", "-q", "origin", "main")
    r = release(repo, tools[0], "--version", "0.1.0", "--upload")
    assert r.returncode == 0, r.stderr
    create = [c for c in tools[1].read_text().splitlines() if "release create" in c][0]
    head = git(repo, "rev-parse", "HEAD").strip()
    assert create.startswith(f"gh release create v0.1.0 --draft --target {head} --title Ostraka 0.1.0")
    for name in ("ostraka-0.1.0-source.tar.gz", DEB, "SHA256SUMS"):
        assert f"dist/0.1.0/{name}" in create


def test_its_output_installs_with_install_sh(repo, tools, machine):
    r = release(repo, tools[0], "--version", "0.1.0")
    assert r.returncode == 0, r.stderr
    i = run([str(APP / "install.sh"), "--from-dir", str(repo / "dist/0.1.0")], machine.env())
    assert i.returncode == 0, i.stderr
    assert any(c.startswith("apt-get install -y ") for c in machine.calls())


def test_every_platform_gets_one_package(repo, tools):
    """Every packager stubbed: each runs once, with no --arch, under the names install.sh reads."""
    (repo / "desktop/macos").mkdir()
    (repo / "desktop/windows").mkdir()
    write_exe(repo / "desktop/windows/package-msi.sh", STUB.replace("{name}", "Ostraka-${v}-windows-universal.msi"))
    write_exe(repo / "desktop/macos/package.sh", STUB.replace("{name}", "Ostraka-${v}-macos-universal.dmg"))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "all packagers")
    # A running docker, so nothing is skipped
    write_exe(tools[0] / "docker", "exit 0\n")
    if platform.system() != "Darwin":
        pytest.skip("release.sh only runs the .dmg packager on macOS")
    r = release(repo, tools[0], "--version", "0.1.0")
    assert r.returncode == 0, r.stdout + r.stderr
    out = repo / "dist/0.1.0"
    sums = {line.split("  ")[1] for line in (out / "SHA256SUMS").read_text().splitlines()}
    assert sums == set(release_names())
    for name in release_names():
        if not name.endswith("source.tar.gz"):
            assert (out / name).read_text().endswith(" for none\n"), name


def test_a_pre_release_keeps_its_hyphen_in_every_name(tmp_path, tools):
    repo = make_repo(tmp_path, "1.0.0-rc1")
    r = release(repo, tools[0], "--version", "v1.0.0-rc1")
    assert r.returncode == 0, r.stdout + r.stderr
    out = repo / "dist/1.0.0-rc1"
    assert {p.name for p in out.iterdir()} == {
        "ostraka-1.0.0-rc1-source.tar.gz", "ostraka_1.0.0-rc1-1_all.deb", "SHA256SUMS",
    }


@pytest.mark.parametrize("version", ["1.0.0rc1", "1.0.0~rc1", "1.0", "1.0.0-"])
def test_refuses_a_version_that_isnt_n_n_n_or_a_hyphenated_pre_release(tmp_path, tools, version):
    repo = make_repo(tmp_path, version)
    r = release(repo, tools[0], "--version", version)
    assert r.returncode != 0
    assert "isn't n.n.n or n.n.n-pre" in r.stderr
    assert not (repo / "dist").exists()


def _names(members: list[str]) -> set[str]:
    return {m.rstrip("/") for m in members}


def test_source_tarball_has_one_top_folder_and_leaves_out_what_an_install_doesnt_use(tmp_path):
    out = tmp_path / "src.tar.gz"
    subprocess.run([str(APP / "source-tarball.sh"), str(out)], check=True)
    with tarfile.open(out) as t:
        names = _names(t.getnames())
    version = (ROOT / "backend/ostraka/__init__.py").read_text().split('__version__ = "')[1].split('"')[0]
    top = f"ostraka-{version}"
    assert {n.split("/")[0] for n in names} == {top}
    for kept in ("docker-compose.yml", "LICENSE", "COMMERCIAL-LICENSE.md", "backend/ostraka/__init__.py",
                 "backend/Dockerfile", "frontend/Dockerfile", "frontend/src"):
        assert f"{top}/{kept}" in names, kept
    for dropped in (".claude", "frontend/e2e", "backend/tests", "scripts/admin", "tests"):
        assert f"{top}/{dropped}" not in names, dropped
        assert not any(n.startswith(f"{top}/{dropped}/") for n in names), dropped


def test_source_zip_holds_the_same_tree(tmp_path):
    tgz, zp = tmp_path / "s.tar.gz", tmp_path / "s.zip"
    subprocess.run([str(APP / "source-tarball.sh"), str(tgz)], check=True)
    subprocess.run([str(APP / "source-tarball.sh"), str(zp)], check=True)
    with tarfile.open(tgz) as t, zipfile.ZipFile(zp) as z:
        assert _names(t.getnames()) == _names(z.namelist())


def test_make_release_fixture_is_consistent(tmp_path):
    """The fake release the install tests use checks out, so their refusals are the script's."""
    rel = make_release(tmp_path / "r")
    for line in (rel / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert sha256(rel / name) == digest
