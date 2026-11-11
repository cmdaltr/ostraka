#!/bin/sh
# Install or upgrade Ostraka on macOS or Linux from a GitHub release (desktop/README.md).
#
#   sh install.sh                     the latest release
#   sh install.sh --version v0.2.0    a given release
#   sh install.sh --file PACKAGE      a package already downloaded (or, on Linux, the source tarball), installed
#                                     as it is (no checksum: you chose it)
#   sh install.sh --from-dir DIR      a release in a folder (dist/<version>/ from scripts/release.sh), checked
#                                     against its SHA256SUMS like a download
#   sh install.sh --server URL        connect the app to your team's Ostraka server instead of asking
#   sh install.sh --download-only DIR download and check the package, save it in DIR, install nothing
#   sh install.sh --launch            open Ostraka when it's installed
#   sh install.sh --clean             once the new package passes its checksum, run uninstall.sh --data first
#                                     (it asks before deleting ~/Ostraka and its data; --yes doesn't ask)
#
# On a Linux without apt (Rocky, RHEL, Alma, Fedora, anything else) there's no package: it installs the
# release's source tarball into ~/Ostraka (OSTRAKA_HOME), as the .deb's launcher would on first run, and
# adds the launcher to this user's application menu with that tree's desktop/linux/install.sh. No root.
#
# The repository may be private: set GH_TOKEN (or GITHUB_TOKEN) to a token that can read it, or be logged
# in to the gh CLI. Run this as a file; don't pipe it into a shell.
set -eu

REPO="${OSTRAKA_REPO:-cmdaltr/ostraka}"
# Only the tests point this elsewhere (a file:// tree shaped like the API)
API="${OSTRAKA_GITHUB_API:-https://api.github.com}"
OS_RELEASE="${OSTRAKA_OS_RELEASE:-/etc/os-release}"
version=latest
file=
from_dir=
server=
download_only=
launch=
clean=
yes=
started=$(date +%s)

if [ -t 1 ]; then c=$(printf '\033[36m'); g=$(printf '\033[32m'); d=$(printf '\033[2m'); r=$(printf '\033[31m'); o=$(printf '\033[0m')
else c='' g='' d='' r='' o=''; fi
n=0
steps=0
step() { n=$((n + 1)); t0=$(date +%s); printf '\n%s[%d/%d] %s%s\n' "$c" "$n" "$steps" "$*" "$o"; }
ok() { printf '      %sOK  %s%s %s(%ds)%s\n' "$g" "$*" "$o" "$d" $(($(date +%s) - t0)) "$o"; }
note() { printf '      %s%s%s\n' "$d" "$*" "$o"; }
die() { printf '\n%sinstall.sh: %s%s\n' "$r" "$*" "$o" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --version) version="${2:?--version needs a tag}"; shift 2 ;;
        --file) file="${2:?--file needs a path}"; shift 2 ;;
        --from-dir) from_dir="${2:?--from-dir needs a folder}"; shift 2 ;;
        --server) server="${2:?--server needs a URL}"; shift 2 ;;
        --download-only) download_only="${2:?--download-only needs a folder}"; shift 2 ;;
        --launch) launch=1; shift ;;
        --clean) clean=1; shift ;;
        --yes|-y) yes=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) die "unknown option $1" ;;
    esac
done
[ -z "$file" ] || [ -z "$from_dir" ] || die "--file and --from-dir are two different sources: give one"
[ -z "$file" ] || [ -z "$download_only" ] || die "--download-only has nothing to download with --file"
case "$version" in latest|v*) ;; *) version="v$version" ;; esac
# Plain http only for this computer: the host is exactly 127.0.0.1 or localhost, then a port, a path
# or nothing (http://localhost.example.org is someone else's)
local_http() {
    rest=${1#http://}
    case "$rest" in
        127.0.0.1|127.0.0.1/*|localhost|localhost/*) return 0 ;;
        127.0.0.1:*|localhost:*) port=${rest#*:}; port=${port%%/*} ;;
        *) return 1 ;;
    esac
    case "$port" in ''|*[!0-9]*) return 1 ;; esac
}
case "$server" in
    ''|https://*) ;;
    http://*) local_http "$server" || die "--server must be an https:// address (plain http only for this computer)" ;;
    *) die "--server must be an https:// address (plain http only for this computer)" ;;
esac

# The OS, and on Linux the package family, decide which file this machine wants (desktop/README.md).
# One package per platform serves every processor, as Rowanholt's do: Ostraka-<v>-macos-universal.dmg,
# ostraka_<v>-1_all.deb, and elsewhere on Linux ostraka-<v>-source.tar.gz (scripts and a compose stack)
os=$(uname -s)
case "$os" in
    Darwin) kind=dmg; pattern='Ostraka-*-macos-universal.dmg' ;;
    Linux)
        [ -r "$OS_RELEASE" ] || die "can't tell which Linux this is: no $OS_RELEASE"
        # shellcheck disable=SC1090  # os-release is shell assignments by definition
        family=$(. "$OS_RELEASE"; printf '%s %s' "${ID:-}" "${ID_LIKE:-}")
        case " $family " in
            *" debian "*|*" ubuntu "*) kind=deb; pattern='ostraka_*-1_all.deb' ;;
            *) kind=tree; pattern='ostraka-*-source.tar.gz' ;;
        esac
        ;;
    *) die "no Ostraka package for $os: on Windows use install.ps1" ;;
esac

# A package's name ends the way this machine's pattern does (the version is the * in it)
# shellcheck disable=SC2254  # the pattern is a glob on purpose
matches() { case "$1" in $pattern) return 0 ;; *) return 1 ;; esac; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# --clean runs the uninstaller beside this script (the repository's desktop/, or desktop/ in an unpacked
# ~/Ostraka). Copied out first: it may delete the tree this script is running from
if [ -n "$clean" ]; then
    here=$(dirname "$0")
    for u in "$here/uninstall.sh" "${OSTRAKA_HOME:-$HOME/Ostraka}/desktop/uninstall.sh"; do
        [ -f "$u" ] && { cp "$u" "$tmp/uninstall.sh"; break; }
    done
    [ -f "$tmp/uninstall.sh" ] || die "--clean needs uninstall.sh beside install.sh"
fi

if [ -n "$file" ]; then steps=0; elif [ -n "$from_dir" ]; then steps=2; else steps=3; fi
[ -n "$download_only" ] || steps=$((steps + 1))
[ -n "$clean" ] && [ -z "$download_only" ] && steps=$((steps + 1))
[ -n "$server" ] && [ -z "$download_only" ] && steps=$((steps + 1))
printf '\n  Ostraka: installer\n'
printf '  %sostraka (\341\275\204\317\203\317\204\317\201\316\261\316\272\316\261): pieces of broken pottery, written on for centuries%s\n' "$d" "$o"

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# The release JSON's assets as "<name>\t<api url>" lines. macOS has no python3 without the developer
# tools (the stub opens an install dialog) but always has JavaScript for Automation; RHEL's dnf is
# Python, so it always has python3; a minimal Debian may have neither python3 nor jq, then it says so.
assets() {
    if [ "$os" = Darwin ]; then
        osascript -l JavaScript -e 'function run(argv) {
            var r = JSON.parse($.NSString.alloc.initWithDataEncoding($.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile, $.NSUTF8StringEncoding).js);
            if (!r.assets) throw new Error(r.message || "no release");
            return r.assets.map(function (a) { return a.name + "\t" + a.url; }).join("\n");
        }'
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import json, sys
r = json.load(sys.stdin)
if "assets" not in r: sys.exit(r.get("message", "no release"))
print("\n".join(a["name"] + "\t" + a["url"] for a in r["assets"]))'
    elif command -v jq >/dev/null 2>&1; then
        jq -er '.assets[] | .name + "\t" + .url'
    else
        die "reading the release needs python3 or jq: install one, or download the package and use --file"
    fi
}

if [ -z "$file" ] && [ -n "$from_dir" ]; then
    step "Finding the package in $from_dir"
    [ -d "$from_dir" ] || die "no such folder: $from_dir"
    name=
    for f in "$from_dir"/*; do
        b=$(basename "$f")
        matches "$b" && { name=$b; break; }
    done
    [ -n "$name" ] || die "$from_dir has no $pattern"
    [ -f "$from_dir/SHA256SUMS" ] || die "$from_dir has no SHA256SUMS"
    cp "$from_dir/$name" "$tmp/$name"
    cp "$from_dir/SHA256SUMS" "$tmp/SHA256SUMS"
    ok "$name"
elif [ -z "$file" ]; then
    token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
    if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
        token=$(gh auth token 2>/dev/null || true)
    fi
    [ -n "$token" ] || die "set GH_TOKEN to a token that can read $REPO, or log in with gh auth login"
    # The token goes in a header file, not on the command line, where ps would show it
    printf 'Authorization: Bearer %s\n' "$token" > "$tmp/auth"
    get() { curl -fsSL -H @"$tmp/auth" "$@"; }

    step "Finding the $version release of $REPO"
    [ "$version" = latest ] && path=releases/latest || path="releases/tags/$version"
    get -H "Accept: application/vnd.github+json" "$API/repos/$REPO/$path" > "$tmp/release.json" \
        || die "couldn't read $version from $REPO"
    assets < "$tmp/release.json" > "$tmp/assets" || die "couldn't read the release's assets"
    name=
    while IFS="$(printf '\t')" read -r a _; do
        matches "$a" && { name=$a; break; }
    done < "$tmp/assets"
    [ -n "$name" ] || die "the $version release has no $pattern"
    url() { awk -F '\t' -v n="$1" '$1 == n { print $2; exit }' "$tmp/assets"; }
    [ -n "$(url SHA256SUMS)" ] || die "the $version release has no SHA256SUMS"
    ok "$name"

    step "Downloading $name"
    # curl's own bar on a terminal (get's -s hides it); quiet in a log
    if [ -t 2 ]; then
        curl -fL -# -H @"$tmp/auth" -H "Accept: application/octet-stream" -o "$tmp/$name" "$(url "$name")"
    else
        get -H "Accept: application/octet-stream" -o "$tmp/$name" "$(url "$name")"
    fi
    get -H "Accept: application/octet-stream" -o "$tmp/SHA256SUMS" "$(url SHA256SUMS)"
    ok "$(du -h "$tmp/$name" | cut -f1 | tr -d ' ') downloaded"
fi

if [ -z "$file" ]; then
    # Before anything else touches it: a package that doesn't match is never opened or installed
    step "Checking it against the release's SHA256SUMS"
    expected=$(awk -v n="$name" '$2 == n || $2 == "*" n { print $1; exit }' "$tmp/SHA256SUMS")
    [ -n "$expected" ] || die "SHA256SUMS doesn't list $name: not installing it"
    [ "$(sha256 "$tmp/$name")" = "$expected" ] || die "$name doesn't match SHA256SUMS: not installing it"
    ok "SHA-256 matches"
    file="$tmp/$name"
fi
[ -f "$file" ] || die "no such file: $file"
case "$file" in /*) ;; *) file="$PWD/$file" ;; esac
# On Linux the source tarball installs as the tree, on a Debian too
if [ "$os" = Linux ]; then
    case "$file" in *.tar.gz|*.tgz) kind=tree; pattern='*.t*gz' ;; esac
fi
matches "$(basename "$file")" || note "$(basename "$file") isn't named like a $kind for this machine; trying it anyway"

if [ -n "$download_only" ]; then
    mkdir -p "$download_only"
    cp "$file" "$download_only/"
    [ -f "$tmp/SHA256SUMS" ] && cp "$tmp/SHA256SUMS" "$download_only/"
    printf '\n  %sSaved %s in %s; nothing installed.%s\n' "$g" "$(basename "$file")" "$download_only" "$o"
    exit 0
fi

if [ -n "$clean" ]; then
    step "Removing the current install and its data (uninstall.sh --data)"
    if [ -n "$yes" ]; then set -- --data --yes; else set -- --data; fi
    sh "$tmp/uninstall.sh" "$@" || die "uninstall.sh --data stopped (above), so nothing new was installed: $(basename "$file") is still to install"
    t0=$(date +%s)
    ok "Uninstalled"
fi

# apt needs root; everything else here runs as the user. sudo asks for the password here, in
# the open, before quietly's clock starts: the clock rewrites its line every second and would hide a prompt
need_root() {
    [ "$(id -u)" -eq 0 ] && return 0
    note "$1 needs administrator rights: sudo may ask for your password"
    sudo -v || die "sudo didn't give administrator rights, so $1 can't install it"
}
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

# Package managers print hundreds of lines on a fresh machine: keep them in a log, show a clock so
# it doesn't look stuck, and print the end of the log if it fails
quietly() {
    "$@" > "$tmp/pkg.log" 2>&1 &
    pid=$!
    s=0
    while kill -0 "$pid" 2>/dev/null; do
        [ -t 1 ] && printf '\r      %s%dm %02ds%s' "$d" $((s / 60)) $((s % 60)) "$o"
        sleep 1
        s=$((s + 1))
    done
    [ -t 1 ] && printf '\r              \r'
    if ! wait "$pid"; then
        tail -n 25 "$tmp/pkg.log" >&2
        return 1
    fi
}

# install_tree TARBALL: what the .deb's launcher does on first run (desktop/linux/ostraka-app.sh,
# unpack_to), without a package: the tree into OSTRAKA_HOME, unpacked beside the old files first so a
# bad tarball leaves them as they were, then a tree it unpacked before is mirrored (everything but .env
# and .ostraka-version replaced, so nothing from the old version stays) and the marker written. A
# folder that isn't one it unpacked, a git checkout above all, is never touched.
install_tree() {
    home="${OSTRAKA_HOME:-$HOME/Ostraka}"
    if [ -e "$home" ] && [ -n "$(ls -A "$home" 2>/dev/null)" ] \
        && { [ ! -f "$home/.ostraka-version" ] || [ -e "$home/.git" ]; }; then
        die "$home already exists and isn't a copy this installer unpacked: move it aside, or set OSTRAKA_HOME to another folder"
    fi
    [ ! -L "$home" ] || die "$home is a symbolic link: set OSTRAKA_HOME to the folder itself"
    note "Unpacking into $home"
    mkdir -p "$home"
    case "$(cd -P "$home" && pwd)" in /|"$(cd -P "$HOME" && pwd)") die "OSTRAKA_HOME can't be / or your home folder itself" ;; esac
    strip=1
    tar -tzf "$1" | grep -qxE '(\./)?docker-compose\.yml' && strip=0
    staging=$(mktemp -d "$home/.ostraka-unpack.XXXXXX")
    if ! tar -xzf "$1" -C "$staging" --strip-components "$strip" 2> "$tmp/tar.log" \
        || [ ! -f "$staging/docker-compose.yml" ] || [ ! -f "$staging/desktop/linux/install.sh" ]; then
        cat "$tmp/tar.log" >&2
        rm -rf -- "$staging"
        die "$(basename "$1") isn't an Ostraka source tree: nothing in $home changed"
    fi
    tree_version=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$staging/backend/ostraka/__init__.py" 2>/dev/null | head -1)
    [ -n "$tree_version" ] || { rm -rf -- "$staging"; die "$(basename "$1") has no version in backend/ostraka/__init__.py"; }
    if [ -f "$home/.ostraka-version" ]; then
        note "Replacing $(cat "$home/.ostraka-version") (.env and the data volumes stay)"
        find "$home" -mindepth 1 -maxdepth 1 ! -name .env ! -name .ostraka-version ! -name "${staging##*/}" \
            -exec rm -rf -- {} +
    fi
    find "$staging" -mindepth 1 -maxdepth 1 -exec mv -f -- {} "$home/" \;
    rmdir -- "$staging"
    printf '%s\n' "$tree_version" > "$home/.ostraka-version"
    ok "Ostraka $tree_version in $home"
    # The tree's own menu entry, for this user; it runs the launcher from the tree
    bash "$home/desktop/linux/install.sh" > "$tmp/menu.log" 2>&1 || { cat "$tmp/menu.log" >&2; die "couldn't add Ostraka to the applications menu"; }
    note "Added to the applications menu"
    # What the .deb would have pulled in: only said, since installing them needs root and the distro's names
    missing=
    for cmd in curl zenity xdg-open; do command -v "$cmd" >/dev/null 2>&1 || missing="$missing $cmd"; done
    browser=
    for b in chromium chromium-browser google-chrome google-chrome-stable brave-browser microsoft-edge microsoft-edge-stable; do
        command -v "$b" >/dev/null 2>&1 && { browser=$b; break; }
    done
    [ -n "$browser" ] || missing="$missing chromium-or-chrome"
    if [ -n "$missing" ]; then
        note "The launcher also needs:$missing (Chromium, Chrome, Brave or Edge for its window; on Rocky, RHEL and"
        note "Alma: sudo dnf install curl zenity xdg-utils, and Chromium from EPEL or Chrome from Google)"
    fi
}

step "Installing $(basename "$file")"
case "$kind" in
    dmg)
        mnt="$tmp/mnt"
        mkdir -p "$mnt"
        note "Opening the disk image"
        hdiutil attach -nobrowse -quiet -readonly -mountpoint "$mnt" "$file" || die "couldn't open $(basename "$file")"
        [ -d "$mnt/Ostraka.app" ] || { hdiutil detach -quiet "$mnt"; die "$(basename "$file") has no Ostraka.app"; }
        # /Applications needs an admin account; anyone else gets their own Applications folder
        dest=/Applications
        [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
        pgrep -xq Ostraka && note "Ostraka is running: quit it to use the new version"
        note "Copying Ostraka.app to $dest"
        rm -rf "$dest/Ostraka.app"
        ditto "$mnt/Ostraka.app" "$dest/Ostraka.app"
        hdiutil detach -quiet "$mnt"
        ok "$dest/Ostraka.app"
        where="Open it from Launchpad or $dest"
        ;;
    deb)
        # apt rather than dpkg, so the package's dependencies come with it
        need_root apt-get
        note "apt is installing it with what it depends on"
        quietly as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$file" || die "apt couldn't install $(basename "$file")"
        ok "Installed, with $(grep -c '^Setting up' "$tmp/pkg.log" || true) package(s)"
        where="Open Ostraka from the applications menu"
        ;;
    tree)
        install_tree "$file"
        where="Open Ostraka from the applications menu"
        ;;
esac

if [ -n "$server" ]; then
    # The saved server (desktop/README.md): the app opens it instead of asking. Not a lock: IT's setting
    # (MDM, /etc/ostraka/client.json) still wins, and Change server still works
    step "Connecting the app to $server"
    if [ "$kind" = dmg ]; then
        defaults write com.cmdaltr.ostraka OstrakaServer -string "$server"
    else
        mkdir -p "$HOME/.config/ostraka"
        printf '%s\n' "$server" > "$HOME/.config/ostraka/server"
    fi
    ok "Saved"
fi

took=$(( $(date +%s) - started ))
printf '\n  %sOstraka is installed (%dm %ds). %s.%s\n' "$g" $((took / 60)) $((took % 60)) "$where" "$o"
[ -n "$server" ] || printf '  %sOn first launch it asks for your team'\''s server, or runs Ostraka on this computer (that needs Docker).%s\n' "$d" "$o"

if [ -n "$launch" ]; then
    if [ "$kind" = dmg ]; then
        open "$dest/Ostraka.app"
    else
        # Whatever the menu entry runs, without its field codes (%U and the like)
        entry=/usr/share/applications/ostraka.desktop
        [ "$kind" = tree ] && entry="${XDG_DATA_HOME:-$HOME/.local/share}/applications/ostraka.desktop"
        cmd=$(sed -n 's/^Exec=//p' "$entry" 2>/dev/null | head -1 | sed 's/ %[a-zA-Z]//g')
        if [ -n "$cmd" ]; then (sh -c "$cmd" >/dev/null 2>&1 &)
        else note "No ostraka.desktop to launch: open Ostraka from the applications menu"; fi
    fi
fi
