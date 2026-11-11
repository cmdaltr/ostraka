#!/usr/bin/env bash
# Build Ostraka-<version>-windows-universal.msi from a source archive (desktop/source-tarball.sh, .tar.gz or .zip)
# with wixl, from msitools, inside a Docker container: nothing is installed on the host, and it builds the
# same on macOS or Linux. One package for x64 and ARM64 Windows (Rowanholt's naming): nothing in it is compiled.
#
#   desktop/windows/package-msi.sh --version 0.1.0 --source dist/ostraka-0.1.0-source.tar.gz dist/
#
# The MSI carries the launcher (desktop/windows/Ostraka.ps1), the source tree as source.zip for the
# launcher to unpack into %USERPROFILE%\Ostraka, LICENSE and COMMERCIAL-LICENSE.md, and the icon.
# Each comes from the tarball, so the package matches its version; the launcher and icon fall back to
# this checkout's when the tarball predates them (with a warning: that's a test build, not a release).
set -euo pipefail

usage() { echo "usage: package-msi.sh --version X.Y.Z --source TARBALL|ZIP OUTDIR" >&2; exit 2; }

version="" source="" outdir=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version) version="${2:-}"; shift 2 ;;
        --source) source="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        -*) echo "package-msi.sh: unknown option $1" >&2; usage ;;
        *) [ -z "$outdir" ] || usage; outdir="$1"; shift ;;
    esac
done
[ -n "$version" ] && [ -n "$source" ] && [ -n "$outdir" ] || usage
version="${version#v}"
# Windows Installer compares the first three fields only, each a number (the third up to 65535), so a
# pre-release (1.0.0-rc1) is 1.0.0 to it; the .wxs lets the release replace it at the same number. A
# workflow's manual build (1.0.0-dev.57) takes its run number as the fourth field, 1.0.0.57, as
# Rowanholt's does: shown in Settings > Apps, ignored by upgrades. The file and version.txt (what the
# launcher compares) keep the full version
if ! [[ "$version" =~ ^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,5})(-[0-9A-Za-z.]+)?$ ]]; then
    echo "package-msi.sh: the version must be n.n.n or n.n.n-pre (1.0.0-rc1), not $version" >&2
    exit 1
fi
msi_version="${BASH_REMATCH[1]}"
if [[ "$version" =~ -dev\.([0-9]{1,5})$ ]] && [ "${BASH_REMATCH[1]}" -le 65535 ]; then
    msi_version="$msi_version.$((10#${BASH_REMATCH[1]}))"
fi
msi_file="Ostraka-$version-windows-universal.msi"
[ -f "$source" ] || { echo "package-msi.sh: no tarball at $source" >&2; exit 1; }
command -v docker >/dev/null || { echo "package-msi.sh: needs Docker" >&2; exit 1; }

here="$(cd "$(dirname "$0")" && pwd)"
checkout="$(cd "$here/../.." && pwd)"
image="ostraka-wixl:debian12"
mkdir -p "$outdir"
outdir="$(cd "$outdir" && pwd)"

# msitools 0.101 in Debian 12; wixl is its own package there. Cached after the first build
docker build -q -t "$image" - >/dev/null <<'DOCKERFILE'
FROM debian:12
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends wixl msitools zip unzip >/dev/null \
 && rm -rf /var/lib/apt/lists/*
DOCKERFILE

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/in/fallback"
case "$source" in
    *.zip) cp "$source" "$work/in/source.zip" ;;
    *) cp "$source" "$work/in/source.tar.gz" ;;
esac
cp "$here/ostraka.wxs" "$work/in/ostraka.wxs"
[ -f "$here/Ostraka.ps1" ] && cp "$here/Ostraka.ps1" "$work/in/fallback/"
[ -f "$checkout/desktop/icons/ostraka.ico" ] && cp "$checkout/desktop/icons/ostraka.ico" "$work/in/fallback/"

# Files go in and out as tar streams rather than bind mounts, so the paths needn't be ones Docker
# Desktop or OrbStack shares. macOS tar would add its extended attributes, which GNU tar warns about
COPYFILE_DISABLE=1 tar --no-xattrs -C "$work/in" -cf - . | docker run --rm -i -e VERSION="$version" -e MSI_VERSION="$msi_version" -e MSI_FILE="$msi_file" "$image" bash -euo pipefail -c '
    mkdir -p /w/in /w/tree /w/stage /w/out
    tar -C /w/in --warning=no-unknown-keyword -xf -
    if [ -f /w/in/source.zip ]; then unzip -q /w/in/source.zip -d /w/tree
    else tar -C /w/tree --warning=no-unknown-keyword -xzf /w/in/source.tar.gz; fi
    # With or without a top-level folder (git archive --prefix=ostraka/): the zip in the MSI holds the
    # tree at its top, so the launcher unpacks it straight into OSTRAKA_HOME
    root=/w/tree
    if [ ! -f "$root/docker-compose.yml" ]; then
        root="$(dirname "$(find /w/tree -maxdepth 2 -name docker-compose.yml | head -1)")"
    fi
    [ -f "$root/docker-compose.yml" ] || { echo "package-msi.sh: no docker-compose.yml in the tarball" >&2; exit 1; }

    pick() {  # pick NAME TREE_PATH: from the tarball, else this checkout (a warning), else fail
        if [ -f "$root/$2" ]; then cp "$root/$2" "/w/stage/$1"
        elif [ -f "/w/in/fallback/$1" ]; then
            echo "package-msi.sh: warning: $2 is not in the tarball; using the checkout'"'"'s" >&2
            cp "/w/in/fallback/$1" "/w/stage/$1"
        else echo "package-msi.sh: $2 is missing" >&2; exit 1; fi
    }
    pick Ostraka.ps1 desktop/windows/Ostraka.ps1
    pick ostraka.ico desktop/icons/ostraka.ico
    for f in LICENSE COMMERCIAL-LICENSE.md; do
        [ -f "$root/$f" ] || { echo "package-msi.sh: $f is missing from the tarball" >&2; exit 1; }
        cp "$root/$f" /w/stage/
    done
    printf "%s\n" "$VERSION" > /w/stage/version.txt
    # -X: no Unix extra fields, which Expand-Archive has no use for
    (cd "$root" && zip -qrX /w/stage/source.zip .)

    # wixl takes File sources relative to the .wxs, so it sits with the payload (and isn'"'"'t installed)
    cp /w/in/ostraka.wxs /w/stage/
    wixl -v -D Version="$MSI_VERSION" -D FullVersion="$VERSION" -D StageDir=. -o "/w/out/$MSI_FILE" /w/stage/ostraka.wxs >&2
    # What WiX'"'"'s AllowSameVersionUpgrades does, which wixl ignores: the row that finds an older Ostraka
    # takes the same version too (msidbUpgradeAttributesVersionMaxInclusive, 512), so 1.0.0 replaces
    # 1.0.0-rc1. Without it the two would both be installed, sharing files
    # (idt files have CRLF line ends)
    mkdir -p /w/idt
    (cd /w/idt && msiinfo export "/w/out/$MSI_FILE" Upgrade > Upgrade.idt)
    awk -F "\t" -v OFS="\t" '"'"'{ sub(/\r$/, "") } NR > 3 && $7 == "WIX_UPGRADE_DETECTED" && int($5 / 512) % 2 == 0 { $5 += 512 } { print $0 "\r" }'"'"' \
        /w/idt/Upgrade.idt > /w/idt/Upgrade.new && mv /w/idt/Upgrade.new /w/idt/Upgrade.idt
    msibuild "/w/out/$MSI_FILE" -i /w/idt/Upgrade.idt
    msiinfo export "/w/out/$MSI_FILE" Upgrade \
        | awk -F "\t" '"'"'{ sub(/\r$/, "") } $7 == "WIX_UPGRADE_DETECTED" && int($5 / 512) % 2 == 1 { f = 1 } END { exit !f }'"'"' \
        || { echo "package-msi.sh: couldn'"'"'t make same-version upgrades possible" >&2; exit 1; }
    tar -C /w/out -cf - .
' | tar -C "$outdir" -xf -

echo "$outdir/$msi_file"
