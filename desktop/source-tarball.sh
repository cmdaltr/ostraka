#!/usr/bin/env bash
# Write the source tree a package carries (desktop/README.md) to OUT: a .tar.gz, or a .zip for the
# Windows package. It's `git archive` of a commit (HEAD unless given), so only tracked files ship:
# .env, secrets and local state never can, and nor can anything uncommitted.
#
# Everything is under one top-level folder, ostraka-<version>/ (the version in that commit's
# backend/ostraka/__init__.py), so unpacking never spills into the target directory: unpack it and
# move the folder's contents, or strip one component (tar --strip-components=1).
#
#   desktop/source-tarball.sh dist/ostraka-0.1.0-source.tar.gz
#   desktop/source-tarball.sh dist/ostraka-0.1.0-source.zip [REF]
set -euo pipefail

OUT="${1:?usage: source-tarball.sh OUT.tar.gz|OUT.zip [ref]}"
REF="${2:-HEAD}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

case "$OUT" in
    *.tar.gz|*.tgz) format=tar.gz ;;
    *.zip) format=zip ;;
    *) echo "source-tarball.sh: $OUT should end .tar.gz or .zip" >&2; exit 1 ;;
esac

# What a running install never uses: the agent's notes, browser tests, backend tests (the backend
# image's .dockerignore drops them anyway), the diagram sources and the licence signer, which only
# runs on Ben's machine. Everything `docker compose up --build` and the launchers need stays.
exclude=(
    .claude
    frontend/e2e
    backend/tests
    docs/diagrams
    scripts/admin
    tests
)
pathspec=(.)
for p in "${exclude[@]}"; do pathspec+=(":(exclude)$p"); done

version=$(git -C "$ROOT" show "$REF:backend/ostraka/__init__.py" | sed -n 's/^__version__ = "\(.*\)"$/\1/p')
[ -n "$version" ] || { echo "source-tarball.sh: no __version__ in $REF:backend/ostraka/__init__.py" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
out="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
git -C "$ROOT" archive --format="$format" --prefix="ostraka-$version/" -o "$out" "$REF" -- "${pathspec[@]}"
