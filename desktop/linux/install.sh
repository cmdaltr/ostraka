#!/usr/bin/env bash
# Install (or --uninstall) Ostraka's menu entry and icon for the current user, so this checkout
# appears in the application menu and can be pinned to the dock. `ostraka app install` runs it;
# desktop/install.sh runs it for a source-tarball install where there's no apt; the .deb is the
# install everywhere else.
set -euo pipefail

HERE="$(cd -P "$(dirname "$0")" && pwd)"
ROOT="$(cd -P "$HERE/../.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP="$DATA/applications/ostraka.desktop"
ICON="$DATA/icons/hicolor/512x512/apps/ostraka.png"
# Without the generated square icon, the logo goes in the icons base directory, which takes any size
FALLBACK_ICON="$DATA/icons/ostraka.png"

refresh() {
    command -v update-desktop-database &>/dev/null && update-desktop-database -q "$DATA/applications" || true
    command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$DESKTOP" "$ICON" "$FALLBACK_ICON"
    refresh
    echo "Removed Ostraka's menu entry."
    exit 0
fi

command -v curl &>/dev/null || echo "Warning: curl isn't installed; the launcher needs it to check servers and the stack." >&2
command -v zenity &>/dev/null || echo "Warning: zenity isn't installed; the launcher needs it for its dialogs." >&2

mkdir -p "$(dirname "$DESKTOP")"
if [ -f "$ROOT/desktop/icons/ostraka.png" ]; then
    mkdir -p "$(dirname "$ICON")"
    cp "$ROOT/desktop/icons/ostraka.png" "$ICON"
else
    mkdir -p "$(dirname "$FALLBACK_ICON")"
    cp "$ROOT/docs/brand/ostrakon-clay.png" "$FALLBACK_ICON"
fi
chmod +x "$HERE/ostraka-app.sh"
"$HERE/desktop-entry.sh" "$HERE/ostraka-app.sh" > "$DESKTOP"
chmod +x "$DESKTOP"
refresh

echo "Installed $DESKTOP"
echo "Find Ostraka in your application menu; right-click its dock icon to pin it."
