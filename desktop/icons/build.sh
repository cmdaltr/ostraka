#!/usr/bin/env bash
# Regenerate the app icons from the clay mark (docs/brand/ostrakon-clay.png):
#   Ostraka.icns   the macOS app
#   ostraka.png    512 px, for the Linux menu entry and anything else that wants a PNG
#   ostraka.ico    16 to 256 px, for the Windows shortcuts and the MSI
# macOS only (swift, sips, iconutil). The outputs are committed so the packages need none of it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

swift "$HERE/make-icon.swift" tile "$ROOT/docs/brand/ostrakon-clay.png" "$WORK/icon-1024.png"

ICONSET="$WORK/Ostraka.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$WORK/icon-1024.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$WORK/icon-1024.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$HERE/Ostraka.icns"

sips -z 512 512 "$WORK/icon-1024.png" --out "$HERE/ostraka.png" >/dev/null

ICO_PNGS=()
for size in 16 24 32 48 64 128 256; do
    sips -z "$size" "$size" "$WORK/icon-1024.png" --out "$WORK/ico-$size.png" >/dev/null
    ICO_PNGS+=("$WORK/ico-$size.png")
done
swift "$HERE/make-icon.swift" ico "$HERE/ostraka.ico" "${ICO_PNGS[@]}"

# OneDrive and sips leave extended attributes that codesign later refuses to sign over
xattr -c "$HERE/Ostraka.icns" "$HERE/ostraka.png" "$HERE/ostraka.ico" 2>/dev/null || true
echo "Icons written to $HERE"
