"""desktop/install.sh against fake releases, on a fake machine (conftest.FakeMachine)."""

from __future__ import annotations

import io
import os
import shutil
import tarfile
from pathlib import Path

import pytest
from conftest import APP, make_api, make_release, make_source_tarball, release_names, run, sha256, write_exe

INSTALL = str(APP / "install.sh")

DEBIAN = 'ID=debian\nVERSION_ID="12"\n'
UBUNTU = 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n'
ROCKY = 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\n'
ALMA = 'ID="almalinux"\nID_LIKE="rhel centos fedora"\n'
RHEL = 'ID="rhel"\nID_LIKE="fedora"\n'
ARCH = "ID=arch\n"


@pytest.mark.parametrize(
    ("system", "machine_arch", "os_release", "expected"),
    [
        # One package per platform, whatever the processor
        ("Darwin", "arm64", "", "Ostraka-0.1.0-macos-universal.dmg"),
        ("Darwin", "x86_64", "", "Ostraka-0.1.0-macos-universal.dmg"),
        ("Linux", "x86_64", DEBIAN, "ostraka_0.1.0-1_all.deb"),
        ("Linux", "aarch64", DEBIAN, "ostraka_0.1.0-1_all.deb"),
        ("Linux", "aarch64", UBUNTU, "ostraka_0.1.0-1_all.deb"),
        # No .deb there: the source tarball, whatever the processor
        ("Linux", "x86_64", ROCKY, "ostraka-0.1.0-source.tar.gz"),
        ("Linux", "aarch64", ROCKY, "ostraka-0.1.0-source.tar.gz"),
        ("Linux", "aarch64", ALMA, "ostraka-0.1.0-source.tar.gz"),
        ("Linux", "x86_64", RHEL, "ostraka-0.1.0-source.tar.gz"),
        ("Linux", "riscv64", ARCH, "ostraka-0.1.0-source.tar.gz"),
    ],
)
def test_chooses_the_package_for_the_machine(machine, tmp_path, system, machine_arch, os_release, expected):
    machine.set_os(system, machine_arch, os_release)
    release = make_release(tmp_path / "release")
    out = tmp_path / "out"
    r = run([INSTALL, "--from-dir", str(release), "--download-only", str(out)], machine.env())
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted([expected, "SHA256SUMS"])
    assert machine.calls() == []


def test_a_checksum_mismatch_installs_nothing(machine, tmp_path):
    release = make_release(tmp_path / "release", corrupt="ostraka_0.1.0-1_all.deb")
    r = run([INSTALL, "--from-dir", str(release)], machine.env())
    assert r.returncode != 0
    assert "doesn't match SHA256SUMS: not installing it" in r.stderr
    assert machine.calls() == []


def test_a_package_missing_from_sha256sums_installs_nothing(machine, tmp_path):
    release = make_release(tmp_path / "release")
    sums = release / "SHA256SUMS"
    sums.write_text("".join(line for line in sums.read_text().splitlines(True) if ".deb" not in line))
    r = run([INSTALL, "--from-dir", str(release)], machine.env())
    assert r.returncode != 0
    assert "SHA256SUMS doesn't list ostraka_0.1.0-1_all.deb" in r.stderr
    assert machine.calls() == []


def test_a_good_package_goes_to_apt(machine, tmp_path):
    release = make_release(tmp_path / "release")
    r = run([INSTALL, "--from-dir", str(release)], machine.env())
    assert r.returncode == 0, r.stderr
    assert "SHA-256 matches" in r.stdout
    apt = [c for c in machine.calls() if c.startswith("apt-get")]
    assert len(apt) == 1
    assert apt[0].startswith("apt-get install -y /") and apt[0].endswith("/ostraka_0.1.0-1_all.deb")
    assert "[3/3]" in r.stdout


def _tree(machine) -> Path:
    return machine.home / "Ostraka"


def _menu_entry(machine) -> Path:
    return machine.home / ".local/share/applications/ostraka.desktop"


def test_without_apt_the_source_tree_goes_in_home_with_a_menu_entry(machine, tmp_path):
    machine.set_os("Linux", "x86_64", ROCKY)
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "release"))], machine.env())
    assert r.returncode == 0, r.stdout + r.stderr
    tree = _tree(machine)
    assert (tree / ".ostraka-version").read_text() == "0.1.0\n"
    assert (tree / "docker-compose.yml").is_file() and (tree / "desktop/linux/ostraka-app.sh").is_file()
    assert not list(tree.glob(".ostraka-unpack.*"))
    assert f'Exec="{tree}/desktop/linux/ostraka-app.sh"' in _menu_entry(machine).read_text()
    # No root and no package manager on this route
    assert machine.calls() == []
    assert "[3/3]" in r.stdout


def test_the_tree_route_names_what_the_launcher_still_needs(machine, tmp_path):
    machine.set_os("Linux", "x86_64", ROCKY)
    # Only the system's own commands: no curl, zenity or browser on this PATH
    env = machine.env(PATH=f"{machine.bin}:/usr/bin:/bin")
    for b in ("chromium", "google-chrome", "zenity"):
        assert not any((Path(d) / b).exists() for d in ("/usr/bin", "/bin")), "this machine has " + b
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "release"))], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "The launcher also needs:" in r.stdout and "zenity" in r.stdout and "chromium-or-chrome" in r.stdout


def test_a_browser_on_path_isnt_asked_for(machine, tmp_path):
    machine.set_os("Linux", "x86_64", ROCKY)
    for cmd in ("chromium", "zenity", "curl", "xdg-open"):
        write_exe(machine.bin / cmd, "exit 0\n")
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "release"))], machine.env())
    assert r.returncode == 0, r.stderr
    assert "The launcher also needs" not in r.stdout


def test_an_upgrade_mirrors_the_tree_and_keeps_env(machine, tmp_path):
    machine.set_os("Linux", "x86_64", ROCKY)
    assert run([INSTALL, "--from-dir", str(make_release(tmp_path / "a"))], machine.env()).returncode == 0
    tree = _tree(machine)
    (tree / ".env").write_text("OSTRAKA_SECRET_KEY=keep\n")
    (tree / "backend/migrations-from-0.1.0.py").write_text("gone after the upgrade\n")
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "b", version="0.2.0"))], machine.env())
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Replacing 0.1.0" in r.stdout
    assert (tree / ".ostraka-version").read_text() == "0.2.0\n"
    assert (tree / ".env").read_text() == "OSTRAKA_SECRET_KEY=keep\n"
    assert not (tree / "backend/migrations-from-0.1.0.py").exists()
    assert '__version__ = "0.2.0"' in (tree / "backend/ostraka/__init__.py").read_text()


@pytest.mark.parametrize("what", ["checkout", "other"])
def test_the_tree_route_never_touches_a_folder_it_didnt_unpack(machine, tmp_path, what):
    machine.set_os("Linux", "x86_64", ROCKY)
    tree = _tree(machine)
    tree.mkdir()
    (tree / "mine.txt").write_text("keep\n")
    if what == "checkout":
        (tree / ".git").mkdir()
        (tree / ".ostraka-version").write_text("0.0.1\n")
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "r"))], machine.env())
    assert r.returncode != 0
    assert "isn't a copy this installer unpacked" in r.stderr
    assert sorted(p.name for p in tree.iterdir()) == sorted(["mine.txt"] + ([".git", ".ostraka-version"] if what == "checkout" else []))
    assert not _menu_entry(machine).exists()


def test_a_tarball_that_isnt_ostraka_changes_nothing(machine, tmp_path):
    machine.set_os("Linux", "x86_64", ROCKY)
    bad = tmp_path / "ostraka-9.9.9-source.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        info = tarfile.TarInfo("x/README")
        t.addfile(info, io.BytesIO(b""))
    r = run([INSTALL, "--file", str(bad)], machine.env())
    assert r.returncode != 0
    assert "isn't an Ostraka source tree" in r.stderr
    assert not _menu_entry(machine).exists()
    assert not any(p.name.startswith(".ostraka-unpack") for p in _tree(machine).iterdir())


@pytest.mark.parametrize("top", [True, False])
def test_file_takes_the_source_tarball_on_any_linux(machine, tmp_path, top):
    """On Debian too: a tarball given with --file is installed as the tree, not handed to apt."""
    src = make_source_tarball(tmp_path / "ostraka-0.1.0-source.tar.gz", top=top)
    r = run([INSTALL, "--file", str(src), "--server", "https://ir.example.org"], machine.env())
    assert r.returncode == 0, r.stdout + r.stderr
    assert (_tree(machine) / ".ostraka-version").read_text() == "0.1.0\n"
    assert (machine.home / ".config/ostraka/server").read_text() == "https://ir.example.org\n"
    assert not [c for c in machine.calls() if c.startswith("apt-get")]


def test_uninstall_removes_the_tree_route_s_menu_entry_and_keeps_the_tree(machine, tmp_path):
    machine.set_os("Linux", "x86_64", ROCKY)
    assert run([INSTALL, "--from-dir", str(make_release(tmp_path / "r"))], machine.env()).returncode == 0
    u = run(["sh", str(APP / "uninstall.sh")], machine.env())
    assert u.returncode == 0, u.stdout + u.stderr
    assert "menu entry removed" in u.stdout
    assert not _menu_entry(machine).exists()
    assert (_tree(machine) / ".ostraka-version").exists()


def test_file_skips_the_download_and_the_checksum(machine, tmp_path):
    pkg = tmp_path / "ostraka_0.1.0-1_all.deb"
    pkg.write_bytes(b"anything")
    # No token and no API: if it tried to download, it would fail
    env = machine.env(OSTRAKA_GITHUB_API="file:///nonexistent", GH_TOKEN="")
    r = run([INSTALL, "--file", str(pkg)], env)
    assert r.returncode == 0, r.stderr
    assert "SHA256SUMS" not in r.stdout
    assert "[1/1]" in r.stdout
    assert [c for c in machine.calls() if c.startswith("apt-get")] == [f"apt-get install -y {pkg}"]


def test_relative_file_is_made_absolute_for_apt(machine, tmp_path):
    (tmp_path / "ostraka_0.1.0-1_all.deb").write_bytes(b"x")
    r = run([INSTALL, "--file", "ostraka_0.1.0-1_all.deb"], machine.env(), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert f"apt-get install -y {tmp_path}/ostraka_0.1.0-1_all.deb" in machine.calls()


def test_downloads_the_latest_release_from_the_api(machine, tmp_path):
    api = make_api(tmp_path, make_release(tmp_path / "release"))
    out = tmp_path / "out"
    r = run([INSTALL, "--download-only", str(out)], machine.env(OSTRAKA_GITHUB_API=api, GH_TOKEN="t"))
    assert r.returncode == 0, r.stderr
    assert "Finding the latest release" in r.stdout
    assert (out / "ostraka_0.1.0-1_all.deb").read_bytes() == b"package ostraka_0.1.0-1_all.deb\n"


def test_version_without_v_finds_the_tag(machine, tmp_path):
    api = make_api(tmp_path, make_release(tmp_path / "release"))
    (Path(api.removeprefix("file://")) / "repos/cmdaltr/ostraka/releases/latest").unlink()
    r = run(
        [INSTALL, "--version", "0.1.0", "--download-only", str(tmp_path / "out")],
        machine.env(OSTRAKA_GITHUB_API=api, GH_TOKEN="t"),
    )
    assert r.returncode == 0, r.stderr
    assert "Finding the v0.1.0 release" in r.stdout


def test_a_mismatched_download_installs_nothing(machine, tmp_path):
    api = make_api(tmp_path, make_release(tmp_path / "release", corrupt="ostraka_0.1.0-1_all.deb"))
    r = run([INSTALL], machine.env(OSTRAKA_GITHUB_API=api, GH_TOKEN="t"))
    assert r.returncode != 0
    assert "doesn't match SHA256SUMS" in r.stderr
    assert machine.calls() == []


def test_without_a_token_it_says_how_to_get_one(machine, tmp_path):
    # A gh that isn't logged in
    write_exe(machine.bin / "gh", "exit 1\n")
    r = run([INSTALL], machine.env(GH_TOKEN="", GITHUB_TOKEN=""))
    assert r.returncode != 0
    assert "set GH_TOKEN" in r.stderr


def _installer_with_fake_uninstaller(tmp_path: Path, log: Path) -> str:
    folder = tmp_path / "app"
    folder.mkdir()
    shutil.copy(APP / "install.sh", folder / "install.sh")
    write_exe(folder / "uninstall.sh", f'echo "uninstall.sh $*" >> "{log}"\n')
    return str(folder / "install.sh")


def test_clean_uninstalls_after_the_checksum_and_before_installing(machine, tmp_path):
    installer = _installer_with_fake_uninstaller(tmp_path, machine.log)
    r = run([installer, "--clean", "--yes", "--from-dir", str(make_release(tmp_path / "release"))], machine.env())
    assert r.returncode == 0, r.stderr
    calls = machine.calls()
    assert calls[0] == "uninstall.sh --data --yes"
    assert calls[-1].startswith("apt-get install")


def test_clean_with_a_bad_download_leaves_the_current_install(machine, tmp_path):
    installer = _installer_with_fake_uninstaller(tmp_path, machine.log)
    release = make_release(tmp_path / "release", corrupt="ostraka_0.1.0-1_all.deb")
    r = run([installer, "--clean", "--from-dir", str(release)], machine.env())
    assert r.returncode != 0
    assert machine.calls() == []


def test_server_is_saved_for_the_linux_app(machine, tmp_path):
    r = run(
        [INSTALL, "--from-dir", str(make_release(tmp_path / "r")), "--server", "https://ir.example.org"],
        machine.env(),
    )
    assert r.returncode == 0, r.stderr
    assert (machine.home / ".config/ostraka/server").read_text() == "https://ir.example.org\n"


@pytest.mark.parametrize(
    "server",
    [
        "http://ir.example.org",
        "http://localhost.example.org",
        "http://127.0.0.1.example.org",
        "http://localhost:24747@example.org",
        "http://localhost:/",
        "http://localhostx",
        "ftp://localhost",
    ],
)
def test_server_must_be_https(machine, server):
    r = run([INSTALL, "--server", server], machine.env())
    assert r.returncode != 0
    assert "https://" in r.stderr


@pytest.mark.parametrize(
    "server", ["http://localhost", "http://127.0.0.1", "http://localhost:24747", "http://127.0.0.1:24747/"]
)
def test_plain_http_is_allowed_for_this_computer(machine, tmp_path, server):
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "r")), "--server", server], machine.env())
    assert r.returncode == 0, r.stderr
    assert (machine.home / ".config/ostraka/server").read_text() == f"{server}\n"


def test_sudo_asks_for_the_password_before_the_install_starts(machine, tmp_path):
    """The progress clock would overwrite sudo's prompt, so `sudo -v` runs first, in the open."""
    if os.getuid() == 0:
        pytest.skip("as root, install.sh doesn't use sudo")
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "r"))], machine.env())
    assert r.returncode == 0, r.stderr
    calls = machine.calls()
    assert calls[0] == "sudo -v"
    assert calls[1].startswith("sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y ")


def test_when_sudo_refuses_nothing_is_installed(machine, tmp_path):
    if os.getuid() == 0:
        pytest.skip("as root, install.sh doesn't use sudo")
    write_exe(machine.bin / "sudo", f'echo "sudo $*" >> "{machine.log}"\nexit 1\n')
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "r"))], machine.env())
    assert r.returncode != 0
    assert "sudo didn't give administrator rights" in r.stderr
    assert machine.calls() == ["sudo -v"]


@pytest.mark.parametrize(
    ("system", "machine_arch", "os_release", "expected"),
    [
        ("Darwin", "arm64", "", "Ostraka-1.0.0-rc1-macos-universal.dmg"),
        ("Linux", "x86_64", DEBIAN, "ostraka_1.0.0-rc1-1_all.deb"),
        ("Linux", "aarch64", ROCKY, "ostraka-1.0.0-rc1-source.tar.gz"),
    ],
)
def test_a_pre_release_is_found_by_its_names(machine, tmp_path, system, machine_arch, os_release, expected):
    machine.set_os(system, machine_arch, os_release)
    release = make_release(tmp_path / "r", version="1.0.0-rc1")
    out = tmp_path / "out"
    r = run([INSTALL, "--from-dir", str(release), "--download-only", str(out)], machine.env())
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted([expected, "SHA256SUMS"])


def test_the_source_tarball_is_never_taken_for_a_package(machine, tmp_path):
    """Only the package names match: where there's a package, the tarball is left alone."""
    names = release_names()
    for system, arch, rel in [("Darwin", "arm64", ""), ("Linux", "x86_64", DEBIAN), ("Linux", "aarch64", UBUNTU)]:
        machine.set_os(system, arch, rel)
        out = tmp_path / f"out-{system}-{arch}-{len(rel)}"
        r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "r")), "--download-only", str(out)],
                machine.env())
        assert r.returncode == 0, r.stderr
        got = [p.name for p in out.iterdir() if p.name != "SHA256SUMS"]
        assert len(got) == 1 and got[0] in names and not got[0].endswith("source.tar.gz")


def test_sha256sums_from_release_sh_is_accepted(machine, tmp_path):
    """The binary-mode form (`<hash> *<name>`) some tools write is read too."""
    release = make_release(tmp_path / "release")
    deb = release / "ostraka_0.1.0-1_all.deb"
    (release / "SHA256SUMS").write_text(f"{sha256(deb)} *{deb.name}\n")
    r = run([INSTALL, "--from-dir", str(release)], machine.env())
    assert r.returncode == 0, r.stderr


def test_a_release_without_this_platform_s_package_installs_nothing(machine, tmp_path):
    release = make_release(tmp_path / "r")
    (release / "ostraka_0.1.0-1_all.deb").unlink()
    r = run([INSTALL, "--from-dir", str(release)], machine.env())
    assert r.returncode != 0
    assert "has no ostraka_*-1_all.deb" in r.stderr
    assert machine.calls() == []


def test_refuses_an_os_it_has_no_package_for(machine, tmp_path):
    machine.set_os("FreeBSD", "amd64")
    r = run([INSTALL, "--from-dir", str(make_release(tmp_path / "r"))], machine.env())
    assert r.returncode != 0
    assert "no Ostraka package for FreeBSD" in r.stderr
