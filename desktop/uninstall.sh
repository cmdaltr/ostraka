#!/bin/sh
# Remove Ostraka from macOS or Linux (desktop/README.md).
#
#   sh uninstall.sh               remove the app; ~/Ostraka and its data stay for a reinstall
#   sh uninstall.sh --data        also stop the local stack, delete its Docker volumes (the database:
#                                 every incident on this computer) and ~/Ostraka, asking first
#   sh uninstall.sh --data --yes  the same without asking
#
# Without a terminal to ask on, --data keeps everything unless --yes says otherwise. Only a folder
# the app unpacked (it has .ostraka-version) is deleted: never a git checkout, never anything else.
# IT's /etc/ostraka/client.json is never touched.
set -eu

data=
yes=
while [ $# -gt 0 ]; do
    case "$1" in
        --data) data=1; shift ;;
        --yes|-y) yes=1; shift ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) printf 'uninstall.sh: unknown option %s\n' "$1" >&2; exit 1 ;;
    esac
done

if [ -t 1 ]; then c=$(printf '\033[36m'); g=$(printf '\033[32m'); d=$(printf '\033[2m'); r=$(printf '\033[31m'); o=$(printf '\033[0m')
else c='' g='' d='' r='' o=''; fi
n=0
steps=2
[ -n "$data" ] && steps=3
started=$(date +%s)
step() { n=$((n + 1)); t0=$(date +%s); printf '\n%s[%d/%d] %s%s\n' "$c" "$n" "$steps" "$*" "$o"; }
ok() { printf '      %sOK  %s%s %s(%ds)%s\n' "$g" "$*" "$o" "$d" $(($(date +%s) - t0)) "$o"; }
note() { printf '      %s%s%s\n' "$d" "$*" "$o"; }
die() { printf '\n%suninstall.sh: %s%s\n' "$r" "$*" "$o" >&2; exit 1; }

as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"
    else
        note "$1 needs administrator rights: sudo may ask for your password"
        sudo "$@"
    fi
}

case "$(uname -s)" in
    Darwin) os=mac ;;
    Linux) os=linux ;;
    *) die "no Ostraka app for $(uname -s): on Windows use uninstall.ps1" ;;
esac
home="${OSTRAKA_HOME:-$HOME/Ostraka}"

printf '\n  Ostraka: uninstaller\n'

step "Removing the app"
if [ $os = mac ]; then
    if pgrep -xq Ostraka; then
        osascript -e 'quit app "Ostraka"' >/dev/null 2>&1 || true
        sleep 2
        pkill -x Ostraka || true
        note "Quit Ostraka"
    fi
    removed=
    for app in /Applications/Ostraka.app "$HOME/Applications/Ostraka.app"; do
        [ -d "$app" ] || continue
        if [ -w "$(dirname "$app")" ]; then rm -rf "$app"; else as_root rm -rf "$app"; fi
        note "Removed $app"
        removed=1
    done
    # What macOS keeps per app outside the bundle, the saved server among it. A checkout's own build
    # (com.cmdaltr.ostraka.local) keeps its own and isn't touched
    for f in "$HOME/Library/Caches/com.cmdaltr.ostraka" "$HOME/Library/WebKit/com.cmdaltr.ostraka" \
             "$HOME/Library/HTTPStorages/com.cmdaltr.ostraka" "$HOME/Library/HTTPStorages/com.cmdaltr.ostraka.binarycookies" \
             "$HOME/Library/Preferences/com.cmdaltr.ostraka.plist" \
             "$HOME/Library/Saved Application State/com.cmdaltr.ostraka.savedState"; do
        [ -e "$f" ] || continue
        rm -rf "$f"
        note "Removed $f"
    done
    if [ -n "$removed" ]; then ok "Ostraka.app removed"; else ok "No Ostraka.app in /Applications or ~/Applications"; fi
else
    if command -v dpkg >/dev/null 2>&1 && dpkg -s ostraka >/dev/null 2>&1; then
        as_root env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq ostraka >/dev/null || die "apt couldn't remove ostraka"
        ok "ostraka package purged"
    elif [ -f "$home/.ostraka-version" ] && [ ! -e "$home/.git" ] && [ -f "$home/desktop/linux/install.sh" ] \
        && [ -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/ostraka.desktop" ]; then
        # install.sh's route where there's no .deb: the tree in ~/Ostraka and a menu entry for this user
        bash "$home/desktop/linux/install.sh" --uninstall >/dev/null || die "couldn't remove Ostraka's menu entry"
        ok "Ostraka's menu entry removed"
    else
        ok "The ostraka package isn't installed"
    fi
    if [ -f "$HOME/.config/ostraka/server" ]; then
        rm -f "$HOME/.config/ostraka/server"
        rmdir "$HOME/.config/ostraka" 2>/dev/null || true
        note "Forgot the saved server"
    fi
fi

if [ -z "$data" ]; then
    step "Ostraka's data"
    if [ -d "$home" ]; then ok "Kept $home and the stack's volumes: uninstall.sh --data deletes them"
    else ok "None at $home"; fi
    took=$(( $(date +%s) - started ))
    printf '\n  %sOstraka is uninstalled (%dm %ds).%s\n' "$g" $((took / 60)) $((took % 60)) "$o"
    exit 0
fi

# Only a tree the app unpacked (it writes .ostraka-version) is ever deleted, so OSTRAKA_HOME pointing
# at a home folder or anything else can't take it with it. A checkout keeps its folder, as below.
checkout=
[ -e "$home/.git" ] && checkout=1
if [ -d "$home" ] && [ -z "$checkout" ] && [ ! -f "$home/.ostraka-version" ]; then
    die "$home has no .ostraka-version, so it isn't a folder the Ostraka app unpacked: nothing in it is deleted. Remove it yourself if it's Ostraka's."
fi

# The project the host ostraka command uses: COMPOSE_PROJECT_NAME in .env, else ostraka-app for an
# unpacked tree, else the folder's name (a checkout). Not the shell's own COMPOSE_PROJECT_NAME: a
# stray one would point down -v at some other stack's volumes.
project=
[ -f "$home/.env" ] && project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$home/.env" | tail -1 | tr -d '\r"'"'"' ')
if [ -z "$project" ]; then
    if [ -f "$home/.ostraka-version" ]; then project=ostraka-app
    else project=$(basename "$home" | tr '[:upper:]' '[:lower:]'); fi
fi
export COMPOSE_PROJECT_NAME="$project"
in_home() { (cd "$home" && "$@"); }

step "Stopping the local stack"
compose=
volumes=
blocked=
if [ -f "$home/docker-compose.yml" ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    compose=1
    # Listed by label rather than guessed from their names
    volumes=$(docker volume ls -q --filter "label=com.docker.compose.project=$project" | tr '\n' ' ')
    in_home docker compose stop >/dev/null 2>&1 || true
    ok "Stopped (project $project)"
elif [ -f "$home/docker-compose.yml" ]; then
    # The folder stays with them: it's what finds the volumes again once Docker is back
    blocked=1
    ok "Docker isn't running, so the stack's volumes can't be removed now"
else
    ok "No stack at $home"
fi

step "Deleting Ostraka's data"
if [ ! -d "$home" ] && [ -z "$volumes" ]; then
    ok "Nothing to delete"
else
    size=$(du -sh "$home" 2>/dev/null | cut -f1 | tr -d ' ' || true)
    [ -n "$volumes" ] && note "Docker volumes: $volumes"
    [ -d "$home" ] && [ -z "$checkout" ] && [ -z "$blocked" ] && note "Folder: $home (${size:-?})"
    [ -n "$checkout" ] && note "$home is a git checkout: it stays, only the stack's volumes go"
    [ -n "$blocked" ] && note "$home stays until Docker is running and this runs again, so its volumes can go with it"
    answer=n
    if [ -n "$yes" ]; then answer=y
    elif [ -t 0 ]; then
        printf '      Delete these: every incident, user and setting this computer holds? It can'\''t be undone. [y/N] '
        read -r answer || answer=n
    else
        note "No terminal to ask on, so nothing is deleted (--yes deletes without asking)"
    fi
    case "$answer" in
        [yY]*)
            if [ -n "$compose" ]; then
                # down -v removes the containers, the network and the named volumes; --rmi local the
                # images this stack built (backend and web), which are hundreds of megabytes
                in_home docker compose down -v --rmi local --remove-orphans >/dev/null 2>&1 \
                    || note "docker compose down didn't finish: docker volume ls shows what's left"
            fi
            # Checked again just before: nothing without the marker is ever removed
            if [ -z "$checkout" ] && [ -z "$blocked" ] && [ -f "$home/.ostraka-version" ]; then
                rm -rf "$home"
            fi
            ok "Deleted"
            ;;
        *) ok "Kept everything" ;;
    esac
fi

took=$(( $(date +%s) - started ))
printf '\n  %sOstraka is uninstalled (%dm %ds).%s\n' "$g" $((took / 60)) $((took % 60)) "$o"
