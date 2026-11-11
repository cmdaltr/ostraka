#!/usr/bin/env bash
# Build Ostraka.app from Ostraka.swift and Local.swift with swiftc (no Xcode project).
#
#   build.sh [--install] [dest-dir]   Checkout build for this Mac's CPU. Its bundle id is
#                                     com.cmdaltr.ostraka.local, so its settings and sign-ins stay
#                                     apart from a release install's, and it runs Ostraka from this
#                                     checkout (the path goes into Info.plist). --install puts it in
#                                     /Applications, or ~/Applications when that isn't writable;
#                                     otherwise it goes in dest-dir, or dist/macos.
#   build.sh --uninstall              Remove an installed checkout build
#   build.sh --release --version V --source ostraka-src.tar.gz <dest-dir>
#                                     Release build (package.sh): universal binary (arm64 and
#                                     x86_64), bundle id com.cmdaltr.ostraka, carrying the source
#                                     tree it unpacks into ~/Ostraka on first local run
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
INSTALL=false
UNINSTALL=false
RELEASE=false
VERSION=""
SOURCE=""
DEST=""
while [ $# -gt 0 ]; do
    case "$1" in
        --install) INSTALL=true ;;
        --uninstall) UNINSTALL=true ;;
        --release) RELEASE=true ;;
        --version) VERSION="$2"; shift ;;
        --source) SOURCE="$2"; shift ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "Error: unknown option: $1" >&2; exit 1 ;;
        *) DEST="$1" ;;
    esac
    shift
done

applications() { if [ -w /Applications ]; then echo /Applications; else echo "$HOME/Applications"; fi; }

if $UNINSTALL; then
    for dir in /Applications "$HOME/Applications"; do
        app="$dir/Ostraka.app"
        # Only a checkout build: a release install is the user's to remove
        if [ -d "$app" ] && [ "$(defaults read "$app/Contents/Info" CFBundleIdentifier 2>/dev/null)" = com.cmdaltr.ostraka.local ]; then
            rm -rf "$app"
            echo "Removed $app"
        fi
    done
    exit 0
fi

if $RELEASE; then
    [ -n "$DEST" ] || { echo "Error: --release needs a destination directory" >&2; exit 1; }
    [ -f "$SOURCE" ] || { echo "Error: --release needs --source <ostraka-src.tar.gz>" >&2; exit 1; }
    [ -n "$VERSION" ] || { echo "Error: --release needs --version" >&2; exit 1; }
elif $INSTALL; then
    DEST="${DEST:-$(applications)}"
else
    DEST="${DEST:-$ROOT/dist/macos}"
fi
command -v swiftc >/dev/null || { echo "Error: swiftc not found; run 'xcode-select --install'." >&2; exit 1; }

if [ -z "$VERSION" ]; then
    VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$ROOT/backend/ostraka/__init__.py" | head -1)"
    VERSION="${VERSION:-0.0.0}"
fi
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
BUNDLE="$BUILD/Ostraka.app"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

compile() {
    swiftc -O -swift-version 5 -parse-as-library -target "$1-apple-macos12.0" \
        -framework AppKit -framework WebKit \
        "$HERE/Ostraka.swift" "$HERE/Local.swift" -o "$2"
}

echo "Compiling Ostraka.app $VERSION..."
if $RELEASE; then
    compile arm64 "$BUILD/Ostraka-arm64"
    compile x86_64 "$BUILD/Ostraka-x86_64"
    lipo -create "$BUILD/Ostraka-arm64" "$BUILD/Ostraka-x86_64" -output "$BUNDLE/Contents/MacOS/Ostraka"
    cp "$SOURCE" "$BUNDLE/Contents/Resources/ostraka-src.tar.gz"
    BUNDLE_ID="com.cmdaltr.ostraka"
    ROOT_ENTRY=""
else
    compile "$(uname -m)" "$BUNDLE/Contents/MacOS/Ostraka"
    BUNDLE_ID="com.cmdaltr.ostraka.local"
    ROOT_XML="$(printf '%s' "$ROOT" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')"
    ROOT_ENTRY="<key>OstrakaRoot</key><string>$ROOT_XML</string>"
fi

cp "$ROOT/desktop/icons/Ostraka.icns" "$BUNDLE/Contents/Resources/Ostraka.icns"
cp "$HERE/start.html" "$BUNDLE/Contents/Resources/"
cp "$ROOT/frontend/public/brand/ostrakon-clay.png" "$ROOT/frontend/public/brand/ostrakon-limestone.png" \
    "$BUNDLE/Contents/Resources/"
# The licence travels inside the app (desktop/README.md): Help › Licence shows these before a server answers
cp "$ROOT/LICENSE" "$ROOT/COMMERCIAL-LICENSE.md" "$BUNDLE/Contents/Resources/"

# NSAllowsArbitraryLoads: http:// is allowed only for this computer, or when IT or
# OSTRAKA_SERVER sets the address, and the app enforces that itself (parseServerAddress); ATS
# would otherwise block an IT-set http server outright. Certificates are still checked
# against the system's trust store either way.
cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Ostraka</string>
    <key>CFBundleDisplayName</key><string>Ostraka</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleExecutable</key><string>Ostraka</string>
    <key>CFBundleIconFile</key><string>Ostraka</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>CFBundleDevelopmentRegion</key><string>en-GB</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.business</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>Copyright © 2026 cmdaltr. Elastic License 2.0.</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key><true/>
        <key>NSAllowsLocalNetworking</key><true/>
    </dict>
    $ROOT_ENTRY
</dict>
</plist>
PLIST
plutil -lint "$BUNDLE/Contents/Info.plist" >/dev/null
# OneDrive gives the repo's files mode 700; an app in /Applications must be readable by every user
chmod -R u+rwX,go+rX,go-w "$BUNDLE"
chmod 644 "$BUNDLE"/Contents/Resources/* "$BUNDLE/Contents/Info.plist"

# Ad-hoc signature (package.sh re-signs a release with a Developer ID when it has one).
# Files copied out of OneDrive carry extended attributes codesign refuses to sign over.
xattr -cr "$BUNDLE"
codesign --force --sign - "$BUNDLE" 2>"$BUILD/codesign.log" || { cat "$BUILD/codesign.log" >&2; exit 1; }

mkdir -p "$DEST"
APP="$DEST/Ostraka.app"
rm -rf "$APP"
cp -R "$BUNDLE" "$APP"
touch "$APP"  # nudges Finder and the Dock to pick up the icon
echo "Built $APP"
if $INSTALL; then
    echo "Open it from Launchpad or Spotlight. It runs Ostraka from $ROOT; rebuild it after moving the checkout."
fi
