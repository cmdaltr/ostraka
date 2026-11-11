#!/usr/bin/env bash
# Release package for macOS: Ostraka-<version>-macos-universal.dmg (Rowanholt's naming) holding a
# universal Ostraka.app (arm64 and x86_64) that carries the source tree, LICENSE and
# COMMERCIAL-LICENSE.md. Built on a Mac: scripts/release.sh locally, or the desktop-release workflow
# on a macOS runner. Signs and notarises only when the environment provides the means:
#   MACOS_SIGN_IDENTITY     "Developer ID Application: ..." in the keychain
#   MACOS_NOTARY_PROFILE    a notarytool keychain profile, saved once with
#                             xcrun notarytool store-credentials ostraka-notary \
#                                 --apple-id YOU@example.org --team-id TEAMID
#                           (it asks for an app-specific password and keeps it in the keychain, so
#                           no password is ever on a command line or in the environment)
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
#                           used only when there's no MACOS_NOTARY_PROFILE: the workflow's route
#                           (.github/workflows/desktop-release.yml), where the runner's keychain is
#                           thrown away after one build, so a saved profile has nowhere to live
# Without them the app is ad-hoc signed: it runs, but a downloaded copy has to be allowed once
# in System Settings › Privacy & Security.
#
# Usage: package.sh --version V --source ostraka-src.tar.gz <out-dir>
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
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
    echo "usage: package.sh --version V --source ostraka-src.tar.gz <out-dir>" >&2; exit 1; }
SOURCE="$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE")"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
# mktemp makes it 700, and the image's root takes the folder's mode: anyone else on the Mac couldn't open it
chmod 755 "$STAGE"

"$HERE/build.sh" --release --version "$VERSION" --source "$SOURCE" "$STAGE"
APP="$STAGE/Ostraka.app"

if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
    echo "Signing with $MACOS_SIGN_IDENTITY..."
    codesign --force --options runtime --timestamp --sign "$MACOS_SIGN_IDENTITY" "$APP"
fi
codesign --verify --strict "$APP"

# The usual drag-to-install window: the app beside a link to Applications. One image serves both
# architectures, since only the small launcher is compiled; Docker builds the stack's images for the
# Mac's own CPU on first start.
ln -s /Applications "$STAGE/Applications"
DMG="$OUT/Ostraka-$VERSION-macos-universal.dmg"
rm -f "$DMG"
hdiutil create -volname "Ostraka $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null

if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
    codesign --force --timestamp --sign "$MACOS_SIGN_IDENTITY" "$DMG"
    if [ -n "${MACOS_NOTARY_PROFILE:-}" ]; then
        echo "Notarising with the $MACOS_NOTARY_PROFILE keychain profile (this waits for Apple)..."
        xcrun notarytool submit "$DMG" --keychain-profile "$MACOS_NOTARY_PROFILE" --wait
        xcrun stapler staple "$DMG"
    elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
        echo "Notarising as $APPLE_ID (this waits for Apple)..."
        xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
            --password "$APPLE_APP_PASSWORD" --wait
        xcrun stapler staple "$DMG"
    else
        echo "Signed but not notarised: set MACOS_NOTARY_PROFILE to a notarytool keychain profile to notarise."
    fi
else
    echo "Ad-hoc signed: set MACOS_SIGN_IDENTITY to sign with a Developer ID."
fi
echo "Wrote $DMG"
