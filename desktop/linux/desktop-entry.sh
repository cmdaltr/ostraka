#!/usr/bin/env bash
# Print ostraka.desktop for a launcher: desktop-entry.sh <ostraka-app path or command>
# Exec arguments are quoted per the Desktop Entry spec, so a checkout path with spaces works.
set -euo pipefail

HERE="$(cd -P "$(dirname "$0")" && pwd)"
# shellcheck disable=SC2016 # the sed expressions are literal on purpose
quoted="\"$(printf '%s' "$1" | sed 's/\\/\\\\\\\\/g; s/"/\\\\"/g; s/`/\\\\`/g; s/\$/\\\\$/g')\""
# \, & and | are special in the replacement and delimiter of the sed below
quoted="$(printf '%s' "$quoted" | sed 's/[\\&|]/\\&/g')"
sed "s|@EXEC@|$quoted|g" "$HERE/ostraka.desktop.in"
