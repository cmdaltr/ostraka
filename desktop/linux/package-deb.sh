#!/usr/bin/env bash
# Release package for Debian 12+ and Ubuntu 22.04+: ostraka_<version>-1_all.deb (Rowanholt's naming),
# for every architecture: the contents are scripts and the source tree. The launcher and the tree it
# unpacks go in /usr/lib/ostraka, `ostraka-app` in /usr/bin, the menu entry and icon system-wide, and
# the licence in /usr/share/doc/ostraka/copyright. `sudo apt install ./ostraka_*.deb`.
#   package-deb.sh --version V --source ostraka-src.tar.gz <out-dir>
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VERSION=""
SOURCE=""
OUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift ;;
        --source) SOURCE="$2"; shift ;;
        -*) echo "Error: unknown option: $1" >&2; exit 1 ;;
        *) OUT="$1" ;;
    esac
    shift
done
[ -n "$VERSION" ] && [ -f "$SOURCE" ] && [ -n "$OUT" ] || {
    echo "usage: package-deb.sh --version V --source ostraka-src.tar.gz <out-dir>" >&2; exit 1; }
command -v dpkg-deb >/dev/null || { echo "Error: dpkg-deb not found (build on Debian or Ubuntu)." >&2; exit 1; }
[ -f "$ROOT/desktop/icons/ostraka.png" ] || { echo "Error: desktop/icons/ostraka.png is missing (desktop/icons/build.sh)." >&2; exit 1; }
mkdir -p "$OUT"

# The package's own version takes a tilde, which sorts before the release (1.0.0-rc1 → 1.0.0~rc1-1), and
# Debian revision 1. The file keeps the release's spelling, as every other package's does
# (scripts/release.sh): GitHub would rename a '~' in an uploaded asset.
DEB_VERSION="$(printf '%s' "$VERSION" | tr - '~')-1"
DEB_FILE="ostraka_${VERSION}-1_all.deb"
PKG="$(mktemp -d)/ostraka"
trap 'rm -rf "$(dirname "$PKG")"' EXIT
LIB="$PKG/usr/lib/ostraka"
mkdir -p "$PKG/DEBIAN" "$LIB" "$PKG/usr/bin" "$PKG/usr/share/applications" \
    "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/share/doc/ostraka"

install -m 755 "$HERE/ostraka-app.sh" "$LIB/ostraka-app.sh"
install -m 644 "$SOURCE" "$LIB/ostraka-src.tar.gz"
install -m 644 "$ROOT/desktop/icons/ostraka.png" "$LIB/ostraka.png"
install -m 644 "$ROOT/desktop/icons/ostraka.png" "$PKG/usr/share/icons/hicolor/512x512/apps/ostraka.png"
echo "$VERSION" > "$LIB/VERSION"
chmod 644 "$LIB/VERSION"
ln -s ../lib/ostraka/ostraka-app.sh "$PKG/usr/bin/ostraka-app"
"$HERE/copyright.sh" "$ROOT" > "$PKG/usr/share/doc/ostraka/copyright"
chmod 644 "$PKG/usr/share/doc/ostraka/copyright"
# The changelog Debian policy expects beside the copyright file: changelog.Debian.gz, since a version
# with a Debian revision isn't a native package's
printf 'ostraka (%s) unstable; urgency=medium\n\n  * Release %s.\n\n -- cmdaltr <cmdaltr@users.noreply.github.com>  %s\n' \
    "$DEB_VERSION" "$VERSION" "$(LC_ALL=C date -u -R)" | gzip -9n > "$PKG/usr/share/doc/ostraka/changelog.Debian.gz"
chmod 644 "$PKG/usr/share/doc/ostraka/changelog.Debian.gz"
"$HERE/desktop-entry.sh" ostraka-app > "$PKG/usr/share/applications/ostraka.desktop"
chmod 644 "$PKG/usr/share/applications/ostraka.desktop"

# Docker is only recommended: a client of a team's server needs none of it, and apt shows what a
# recommend would pull in before it does. Debian 12's docker-compose is v1, not the plugin, so
# Docker's own packages are the alternative there.
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: ostraka
Version: $DEB_VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: cmdaltr <cmdaltr@users.noreply.github.com>
Homepage: https://github.com/cmdaltr/ostraka
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Depends: curl, zenity, xdg-utils
Recommends: libnotify-bin
Suggests: docker-ce | docker.io, docker-compose-plugin | docker-compose-v2
Description: incident management and response for teams (desktop app)
 Ostraka is the record of an incident: the people, the work, the decisions and
 the assessments. This package adds it to the application menu: on first launch
 it connects to a team's Ostraka server, or runs Ostraka on this computer in
 Docker, and opens it in its own window.
CONTROL

cat > "$PKG/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
SCRIPT
cp "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT/$DEB_FILE"
