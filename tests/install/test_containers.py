"""The scripts on the systems they're for: shellcheck, PowerShell's parser, and real installs and
uninstalls in Debian and Rocky containers with a dummy package built there. Needs Docker; skipped
without it. Slow (a minute or two, mostly apt), so it's its own file."""

from __future__ import annotations

import subprocess

import pytest
from conftest import APP, ROOT, docker_ok, make_source_tarball

pytestmark = pytest.mark.skipif(not docker_ok(), reason="needs a running Docker")

SCRIPTS = [
    "ostraka", "clients/ost", "desktop/install.sh", "desktop/uninstall.sh", "desktop/source-tarball.sh",
    "scripts/release.sh", "desktop/windows/package-msi.sh", "deploy/linux/install.sh",
    *sorted(str(f.relative_to(ROOT)) for f in (ROOT / "desktop/linux").rglob("*.sh")),
]
# Every PowerShell script, so a syntax error anywhere fails make check, not a Windows install
PS1 = sorted(str(f.relative_to(ROOT)) for d in ("desktop", "deploy", "clients", "scripts") for f in (ROOT / d).rglob("*.ps1"))


def docker(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def test_shellcheck():
    r = docker("run", "--rm", "-v", f"{ROOT}:/mnt", "koalaman/shellcheck:stable", *SCRIPTS)
    assert r.returncode == 0, r.stdout + r.stderr


# mcr.microsoft.com/powershell has no arm64 image, and under amd64 emulation on Apple silicon .NET
# crashes in Get-FileHash, so the tests use PowerShell's own release on Debian, native on either
PWSH_IMAGE = "ostraka-pwsh-test:7.4.6"
PWSH_DOCKERFILE = """FROM debian:12
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates libicu72 >/dev/null \\
 && arch=$(dpkg --print-architecture | sed 's/amd64/x64/') \\
 && curl -fsSL -o /tmp/p.tgz https://github.com/PowerShell/PowerShell/releases/download/v7.4.6/powershell-7.4.6-linux-$arch.tar.gz \\
 && mkdir -p /opt/pwsh && tar -xzf /tmp/p.tgz -C /opt/pwsh && chmod +x /opt/pwsh/pwsh \\
 && ln -s /opt/pwsh/pwsh /usr/local/bin/pwsh && rm /tmp/p.tgz
"""
# pwsh on Linux has no PROCESSOR_ARCHITECTURE: OSTRAKA_CPU stands in for it
PWSH = ["run", "--rm", "-v", f"{APP}:/app:ro", "-e", "OSTRAKA_CPU=AMD64", PWSH_IMAGE]


@pytest.fixture(scope="module", autouse=True)
def pwsh_image():
    """The PowerShell image, built once (Docker's cache makes later runs instant)."""
    r = subprocess.run(["docker", "build", "-q", "-t", PWSH_IMAGE, "-"], input=PWSH_DOCKERFILE,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr


def test_powershell_scripts_parse():
    check = (
        f'$bad = 0; foreach ($f in {", ".join(repr(f) for f in PS1)}) {{ $t = $null; $e = $null; '
        '[System.Management.Automation.Language.Parser]::ParseFile("/src/$f", [ref]$t, [ref]$e) | Out-Null; '
        'if ($e) { $bad++; $e | ForEach-Object { "$f $($_.Extent.StartLineNumber): $($_.Message)" } } }; exit $bad'
    )
    r = docker("run", "--rm", "-v", f"{ROOT}:/src:ro", PWSH_IMAGE, "pwsh", "-NoProfile", "-Command", check)
    assert len(PS1) >= 7, PS1
    assert r.returncode == 0, r.stdout + r.stderr


def test_linux_launcher():
    """desktop/linux/tests/test-launcher.sh: the launcher's logic, with curl and zenity stubbed."""
    r = docker("run", "--rm", "-v", f"{ROOT}:/src:ro", "debian:12", "bash", "/src/desktop/linux/tests/test-launcher.sh")
    assert r.returncode == 0, r.stdout + r.stderr


def test_install_ps1_checks_the_msi_before_anything_else():
    """-FromDir with -DownloadOnly runs everything up to msiexec, which pwsh on Linux can't."""
    script = r"""
set -e
mkdir -p /r && cd /r
echo src > ostraka-0.1.0-source.tar.gz
echo msi > Ostraka-0.1.0-windows-universal.msi
good=$(sha256sum Ostraka-0.1.0-windows-universal.msi)
echo "0000000000000000000000000000000000000000000000000000000000000000  Ostraka-0.1.0-windows-universal.msi" > SHA256SUMS
if pwsh -NoProfile -File /app/install.ps1 -FromDir /r -DownloadOnly /out 2> /tmp/err; then exit 10; fi
grep -q "doesn't match SHA256SUMS" /tmp/err || { cat /tmp/err; exit 11; }
[ ! -e /out/Ostraka-0.1.0-windows-universal.msi ] || exit 12
echo "$good" > SHA256SUMS
pwsh -NoProfile -File /app/install.ps1 -FromDir /r -DownloadOnly /out
[ -f /out/Ostraka-0.1.0-windows-universal.msi ] || exit 13
[ "$(ls /out | sort | tr '\n' ' ')" = "Ostraka-0.1.0-windows-universal.msi SHA256SUMS " ] || { ls /out; exit 14; }
# Windows on ARM64 takes the same MSI; 32-bit Windows has no package
OSTRAKA_CPU=ARM64 pwsh -NoProfile -File /app/install.ps1 -FromDir /r -DownloadOnly /arm
[ -f /arm/Ostraka-0.1.0-windows-universal.msi ] || exit 15
if OSTRAKA_CPU=x86 pwsh -NoProfile -File /app/install.ps1 -FromDir /r -DownloadOnly /x86 2> /tmp/err; then exit 16; fi
grep -q "No Ostraka for x86 Windows" /tmp/err || { cat /tmp/err; exit 17; }
for bad in http://ir.example.org http://localhost.example.org http://127.0.0.1.example.org \
           http://localhost:24747@example.org; do
    if pwsh -NoProfile -File /app/install.ps1 -ServerUrl "$bad" -FromDir /r -DownloadOnly /s 2>/dev/null; then
        echo "accepted $bad"; exit 18
    fi
done
pwsh -NoProfile -File /app/install.ps1 -ServerUrl http://localhost:24747 -FromDir /r -DownloadOnly /ok >/dev/null || exit 19
# A gh that isn't signed in (stderr and exit 1) must say how to sign in, not crash on its stderr
mkdir -p /fake && printf '#!/bin/sh\necho "You are not logged into any GitHub hosts." >&2\nexit 1\n' > /fake/gh
chmod +x /fake/gh
if PATH=/fake:$PATH GH_TOKEN= GITHUB_TOKEN= pwsh -NoProfile -File /app/install.ps1 -DownloadOnly /g 2> /tmp/err; then exit 20; fi
grep -q "Set GH_TOKEN" /tmp/err || { cat /tmp/err; exit 21; }
echo ALL-OK
"""
    r = docker(*PWSH, "bash", "-c", script)
    assert r.returncode == 0 and "ALL-OK" in r.stdout, r.stdout + r.stderr


# Builds a dummy ostraka package named as a release would, with the menu entry --launch reads, and
# sets PKG to its name
DUMMY_DEB = r"""
PKG=ostraka_0.1.0-1_all.deb
mkdir -p /pkg/DEBIAN /pkg/usr/share/applications /r
printf 'Package: ostraka\nVersion: 0.1.0-1\nArchitecture: all\nMaintainer: t <t@example.org>\nDescription: dummy\n' > /pkg/DEBIAN/control
printf '[Desktop Entry]\nName=Ostraka\nExec=true %%U\nType=Application\n' > /pkg/usr/share/applications/ostraka.desktop
dpkg-deb --build /pkg "/r/$PKG" >/dev/null
"""


# The same checks on either family: a bad sum installs nothing, a good one installs, --launch works,
# uninstall keeps data without --data, keeps ~/Ostraka while Docker can't remove its volumes, and
# deletes it with --data --yes once there's no stack
ROUND_TRIP = r"""
set -e
cd /r
echo "0000000000000000000000000000000000000000000000000000000000000000  $PKG" > SHA256SUMS
if sh /app/install.sh --from-dir /r > /tmp/out 2>&1; then exit 10; fi
grep -q "doesn't match SHA256SUMS" /tmp/out || { cat /tmp/out; exit 11; }
if $QUERY ostraka >/dev/null 2>&1; then exit 12; fi
sha256sum "$PKG" > SHA256SUMS
sh /app/install.sh --from-dir /r --launch --server https://ir.example.org
$QUERY ostraka >/dev/null || exit 13
[ "$(cat ~/.config/ostraka/server)" = https://ir.example.org ] || exit 14
# A tree the app unpacked (uninstall.sh deletes nothing else)
mkdir -p ~/Ostraka && touch ~/Ostraka/docker-compose.yml ~/Ostraka/.ostraka-version
sh /app/uninstall.sh
if $QUERY ostraka >/dev/null 2>&1; then exit 15; fi
[ -d ~/Ostraka ] || exit 16
[ ! -e ~/.config/ostraka/server ] || exit 17
sh /app/uninstall.sh --data --yes
[ -d ~/Ostraka ] || exit 18
rm ~/Ostraka/docker-compose.yml
sh /app/uninstall.sh --data < /dev/null
[ -d ~/Ostraka ] || exit 19
sh /app/uninstall.sh --data --yes
[ ! -e ~/Ostraka ] || exit 20
echo ALL-OK
"""


@pytest.mark.parametrize(
    ("image", "build", "query"),
    [
        ("debian:12", DUMMY_DEB, "dpkg -s"),
        ("ubuntu:24.04", DUMMY_DEB, "dpkg -s"),
    ],
)
def test_install_and_uninstall_for_real(image, build, query):
    r = docker(
        "run", "--rm", "-v", f"{APP}:/app:ro", "-e", f"QUERY={query}",
        image, "bash", "-c", build + ROUND_TRIP,
    )
    assert r.returncode == 0 and "ALL-OK" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]


# Where there's no apt: the source tarball into ~/Ostraka and a menu entry for the user, with no root,
# no package manager and nothing but bash, tar and coreutils
TREE_ROUND_TRIP = r"""
set -e
cd /r
echo "0000000000000000000000000000000000000000000000000000000000000000  $SRC" > SHA256SUMS
if sh /app/install.sh --from-dir /r > /tmp/out 2>&1; then exit 10; fi
grep -q "doesn't match SHA256SUMS" /tmp/out || { cat /tmp/out; exit 11; }
[ ! -e ~/Ostraka ] || exit 12
sha256sum "$SRC" > SHA256SUMS
sh /app/install.sh --from-dir /r --server https://ir.example.org > /tmp/out 2>&1 || { cat /tmp/out; exit 13; }
cat /tmp/out
[ "$(cat ~/Ostraka/.ostraka-version)" = 0.1.0 ] || exit 14
grep -q "Exec=\"$HOME/Ostraka/desktop/linux/ostraka-app.sh\"" ~/.local/share/applications/ostraka.desktop || exit 15
grep -q "chromium-or-chrome" /tmp/out || exit 16
[ "$(cat ~/.config/ostraka/server)" = https://ir.example.org ] || exit 17
echo "OSTRAKA_SECRET_KEY=kept" > ~/Ostraka/.env
sh /app/install.sh --from-dir /r > /tmp/out 2>&1 || { cat /tmp/out; exit 18; }
grep -q "Replacing 0.1.0" /tmp/out || exit 19
[ "$(cat ~/Ostraka/.env)" = OSTRAKA_SECRET_KEY=kept ] || exit 20
sh /app/uninstall.sh > /tmp/out 2>&1 || { cat /tmp/out; exit 21; }
grep -q "menu entry removed" /tmp/out || { cat /tmp/out; exit 22; }
[ ! -e ~/.local/share/applications/ostraka.desktop ] || exit 23
[ -f ~/Ostraka/.ostraka-version ] || exit 24
# Without Docker the compose file keeps the folder (it finds the volumes again), so take it out first
rm ~/Ostraka/docker-compose.yml
sh /app/uninstall.sh --data --yes
[ ! -e ~/Ostraka ] || exit 25
echo ALL-OK
"""


def test_tree_install_and_uninstall_on_rocky(tmp_path):
    src = make_source_tarball(tmp_path / "ostraka-0.1.0-source.tar.gz")
    r = docker(
        "run", "--rm", "-v", f"{APP}:/app:ro", "-v", f"{tmp_path}:/in:ro", "-e", f"SRC={src.name}",
        "rockylinux:9", "bash", "-c", f"mkdir -p /r && cp /in/{src.name} /r/ && " + TREE_ROUND_TRIP,
    )
    assert r.returncode == 0 and "ALL-OK" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
