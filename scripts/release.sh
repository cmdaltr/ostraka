#!/usr/bin/env bash
# Build every Ostraka package this machine can build into dist/<version>/, with SHA256SUMS, and with
# --upload put them on a draft GitHub release. This is the local build: a pushed v* tag builds the same
# files on GitHub (.github/workflows/desktop-release.yml), installs and uninstalls each on its own
# runner and attaches them to a draft release.
#
#   scripts/release.sh --version 0.1.0            build and checksum only
#   scripts/release.sh --version 0.1.0 --upload   then gh release create v0.1.0 --draft with the files
#
# One package per platform, for every architecture, named as Rowanholt's are:
# Ostraka-<v>-macos-universal.dmg, Ostraka-<v>-windows-universal.msi, ostraka_<v>-1_all.deb. A version
# is n.n.n, or n.n.n-pre for a pre-release (1.0.0-rc1, as Shikari's and Rowanholt's tags), and every
# file carries it as written. Inside, the .deb says 1.0.0~rc1-1 so it sorts before 1.0.0-1, and the
# .msi says 1.0.0 (Windows Installer takes numbers only); a '~' stays out of the filenames because
# GitHub renames it in an asset.
#
# Each packaging script is called as `<script> --version X --source TARBALL OUTDIR` (the tarball is
# desktop/source-tarball.sh's, made once here) and must leave its package in OUTDIR under the name
# desktop/README.md gives it. The .deb script needs dpkg-deb: where this machine hasn't got it (a Mac,
# or a Linux without dpkg) it runs in a Debian container instead. The .msi script runs wixl in Docker
# itself. So one Mac with Docker builds all three; a script that isn't written yet is skipped with a
# note. The release also carries the source tree, for servers and for install.sh on a Linux without apt.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

version=
upload=
while [ $# -gt 0 ]; do
    case "$1" in
        --version) version="${2:?--version needs a version, e.g. 0.1.0}"; shift 2 ;;
        --upload) upload=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "release.sh: unknown option $1" >&2; exit 1 ;;
    esac
done

if [ -t 1 ]; then c=$'\033[36m'; g=$'\033[32m'; y=$'\033[33m'; r=$'\033[31m'; o=$'\033[0m'
else c='' g='' y='' r='' o=''; fi
step() { printf '\n%s== %s%s\n' "$c" "$*" "$o"; }
ok() { printf '   %sOK%s    %s\n' "$g" "$o" "$*"; }
skip() { printf '   %sSKIP%s  %s\n' "$y" "$o" "$*"; }
die() { printf '\n%srelease.sh: %s%s\n' "$r" "$*" "$o" >&2; exit 1; }

[ -n "$version" ] || die "--version is required (the one in backend/ostraka/__init__.py)"
version="${version#v}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] \
    || die "--version $version isn't n.n.n or n.n.n-pre (a pre-release is 1.0.0-rc1, with a hyphen)"
code_version=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' backend/ostraka/__init__.py)
[ "$version" = "$code_version" ] \
    || die "--version $version doesn't match backend/ostraka/__init__.py ($code_version): bump it there and commit first"

# A package is git archive HEAD, so an uncommitted change would be in the release's name but not in it.
# Untracked files can't reach a package that way, so they don't count.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no >&2
    die "the working tree has uncommitted changes: commit or stash them first"
fi
commit=$(git rev-parse HEAD)

out="$ROOT/dist/$version"
# A fresh directory, so SHA256SUMS never lists a package from an earlier attempt
rm -rf "$out"
mkdir -p "$out"
started=$(date +%s)
printf '\n  Ostraka %s release from %s\n' "$version" "${commit:0:12}"

step "Source tree"
source="ostraka-$version-source.tar.gz"
desktop/source-tarball.sh "$out/$source"
ok "$source"

# Runs a Linux packaging script in a throwaway container with the repository mounted, for a machine
# without the tool. Root in the container, so the files it leaves are handed back to this user.
in_container() {
    local image="$1" prepare="$2"; shift 2
    docker run --rm -v "$ROOT:/src" -w /src "$image" bash -euo pipefail -c "
        $prepare
        $(printf '%q ' "$@")
        chown -R $(id -u):$(id -g) dist"
}

# script | the package it makes | what builds it
packages=(
    "desktop/macos/package.sh|Ostraka-$version-macos-universal.dmg|macos"
    "desktop/windows/package-msi.sh|Ostraka-$version-windows-universal.msi|docker"
    "desktop/linux/package-deb.sh|ostraka_${version}-1_all.deb|dpkg-deb"
)
docker_up=
docker info >/dev/null 2>&1 && docker_up=1
built=()
skipped=()
for entry in "${packages[@]}"; do
    IFS='|' read -r script name needs <<< "$entry"
    step "$name"
    if [ ! -f "$script" ]; then
        skip "$script isn't written yet"
        skipped+=("$name")
        continue
    fi
    run=(bash "$script" --version "$version" --source "dist/$version/$source" "dist/$version")
    case "$needs" in
        macos)
            if [ "$(uname -s)" != Darwin ]; then
                skip "needs macOS (swiftc, hdiutil): build it on a Mac and add it to the release"
                skipped+=("$name"); continue
            fi ;;
        docker)
            if [ -z "$docker_up" ]; then
                skip "needs Docker running (wixl runs in a container)"
                skipped+=("$name"); continue
            fi ;;
        dpkg-deb)
            if ! command -v "$needs" >/dev/null 2>&1; then
                if [ -z "$docker_up" ]; then
                    skip "needs $needs, or Docker running to use it in a container"
                    skipped+=("$name"); continue
                fi
                run=(in_container debian:12 : "${run[@]}")
            fi ;;
    esac
    t=$(date +%s)
    "${run[@]}" || die "$script failed"
    [ -f "$out/$name" ] || die "$script finished but left no $name in dist/$version/"
    ok "$name ($(du -h "$out/$name" | cut -f1 | tr -d ' '), $(( $(date +%s) - t ))s)"
    built+=("$name")
done

step "SHA256SUMS"
# Only the files this run made: anything else a packaging script left behind stays out of the release
files=("$source" ${built[@]+"${built[@]}"})
if command -v sha256sum >/dev/null 2>&1; then sum=(sha256sum); else sum=(shasum -a 256); fi
(cd "$out" && "${sum[@]}" "${files[@]}") > "$out/SHA256SUMS"
ok "${#files[@]} file(s)"

took=$(( $(date +%s) - started ))
printf '\n  %sBuilt in %dm %ds: dist/%s/%s\n' "$g" $((took / 60)) $((took % 60)) "$version" "$o"
for f in "${files[@]}" SHA256SUMS; do printf '    %s\n' "$f"; done
[ ${#skipped[@]} -eq 0 ] || printf '  %sNot built here: %s%s\n' "$y" "${skipped[*]}" "$o"

[ -n "$upload" ] || exit 0

step "Draft release v$version"
command -v gh >/dev/null 2>&1 || die "--upload needs the gh CLI"
# The tag is created on GitHub at this commit, so the commit has to be there already
git fetch -q origin 2>/dev/null || true
[ -n "$(git branch -r --contains "$commit" 2>/dev/null)" ] || die "${commit:0:12} isn't pushed: push it, then run with --upload again"
gh release view "v$version" >/dev/null 2>&1 && die "v$version already exists on GitHub: delete the draft or bump the version"
assets=()
for f in "${files[@]}" SHA256SUMS; do assets+=("$out/$f"); done
gh release create "v$version" --draft --target "$commit" --title "Ostraka $version" \
    --notes "Install with desktop/install.sh (macOS, Linux) or desktop/install.ps1 (Windows); each checks its download against SHA256SUMS." \
    "${assets[@]}"
ok "Draft v$version: check it on GitHub, then publish it"
[ ${#skipped[@]} -eq 0 ] || printf '  %sStill to add from another machine: %s (gh release upload v%s FILE, then rebuild SHA256SUMS)%s\n' \
    "$y" "${skipped[*]}" "$version" "$o"
