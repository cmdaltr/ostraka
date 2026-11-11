#!/usr/bin/env bash
# What the Ostraka menu entry runs (desktop/README.md is the contract). It opens Ostraka in its own
# window: a team's server, or the Docker stack on this computer, started first if it isn't up.
#
# From a checkout it runs that checkout. From the .deb (/usr/lib/ostraka, beside
# ostraka-src.tar.gz) it unpacks the bundled source into ~/Ostraka on first local run and offers
# to update it when the package is newer. The server comes from, in order: /etc/ostraka/client.json
# (IT; locked), OSTRAKA_SERVER (a URL, or `local`), ~/.config/ostraka/server (what first run saved).
#   ostraka-app [--setup | --change-server | --stop | --licence | --about]

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
HERE="$(cd -P "$(dirname "$SOURCE")" && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/ostraka"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/ostraka"
PROFILE="${XDG_DATA_HOME:-$HOME/.local/share}/ostraka/browser"
CLIENT_JSON=/etc/ostraka/client.json
LOG="$STATE/app.log"
BUNDLED="$HERE/ostraka-src.tar.gz"
HOME_TREE="${OSTRAKA_HOME:-$HOME/Ostraka}"
# Set when local mode is chosen: the fallback for asking for the first admin when `ostraka status
# --json` doesn't say has_admin
ADMIN_PENDING="$STATE/first-admin-pending"
DOCKER_GUIDE=https://docs.docker.com/engine/install/
# `ostraka first-admin` exits 5 when there's an admin already; anything else non-zero is a failure
EXIT_HAS_ADMIN=5
# Seconds a first-admin hand-off page lives before it's deleted (the browser loads it at once)
HANDOFF_TTL=120
LOCAL_URL_DEFAULT="http://127.0.0.1:${OSTRAKA_PORT:-24747}"

if [ -f "$HERE/ostraka.png" ]; then ICON="$HERE/ostraka.png"; else ICON="$HERE/../icons/ostraka.png"; fi

log() { mkdir -p "$STATE" && echo "[$(date '+%F %T')] $1" >> "$LOG"; }

notify() {
    command -v notify-send &>/dev/null && notify-send -a Ostraka -i "$ICON" Ostraka "$1"
    log "$1"
}

has_zenity() { command -v zenity &>/dev/null; }

say() {
    # say <info|error|warning> <text>
    log "$2"
    if has_zenity; then
        zenity "--$1" --title=Ostraka --width=440 --text="$2" 2>/dev/null
    else
        notify "$2"
        printf 'Ostraka: %b\n' "$2" >&2
    fi
}

app_version() {
    if [ -f "$HERE/VERSION" ]; then cat "$HERE/VERSION"; return; fi
    sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/../../backend/ostraka/__init__.py" 2>/dev/null | head -n1
}

is_tree() { [ -x "$1/ostraka" ] && [ -f "$1/docker-compose.yml" ]; }

# ── Which server ──────────────────────────────────────────────────────────────

# server_url from IT's client.json: a flat object, read without jq or Python, which a minimal
# install may not have
json_server_url() {
    tr -d '\n\r' | sed -n 's/.*"server_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

# "<where> <value>": where is it, env, saved or none. IT comes first because it locks the
# choice; OSTRAKA_SERVER beats the saved one but isn't a lock.
effective_server() {
    local value=""
    [ -r "$CLIENT_JSON" ] && value="$(json_server_url < "$CLIENT_JSON")"
    if [ -n "$value" ]; then echo "it $value"; return; fi
    if [ -n "${OSTRAKA_SERVER:-}" ]; then echo "env $OSTRAKA_SERVER"; return; fi
    value="$(head -n1 "$CONFIG/server" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$value" ]; then echo "saved $value"; return; fi
    echo none
}

is_locked() { [[ "$(effective_server)" == it\ * ]]; }

# normalise_server <text> [any-http]: scheme://host[:port], or fail when it isn't a server address.
# https only, except for this computer; IT and OSTRAKA_SERVER pass any-http, since they may
# knowingly point at a plain-http lab server.
normalise_server() {
    local text="$1" allow_http="${2:-}" scheme rest host name
    # Trim, but keep inner spaces so the host check refuses them
    text="$(printf '%s' "$text" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -n "$text" ] || return 1
    [[ "$text" == *://* ]] || text="https://$text"
    scheme="$(printf '%s' "${text%%://*}" | tr '[:upper:]' '[:lower:]')"
    rest="${text#*://}"
    host="${rest%%[/?#]*}"
    [[ "$host" =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:[0-9]{1,5})?$ ]] \
        || return 1
    name="${host%%:*}"
    case "$scheme" in
        https) ;;
        http) [ -n "$allow_http" ] || [[ "$name" =~ ^(localhost|127\.0\.0\.1)$ ]] || return 1 ;;
        *) return 1 ;;
    esac
    echo "$scheme://$host"
}

# check_server <url>: 0 when it's Ostraka. Otherwise prints why. curl checks the certificate
# against the system's trust store, which is "a certificate the OS trusts"; -q (first, or it's
# ignored) keeps a ~/.curlrc from turning that off.
check_server() {
    local url="$1" body status
    body="$(curl -q -sS --fail --max-time 10 --proto '=https,http' -H 'Accept: application/json' \
        "$url/api/meta" 2>&1)"
    status=$?
    case "$status" in
        0) ;;
        60|35|51|58|59|77|83|90|91)
            echo "$url didn't present a certificate this computer trusts. If it comes from your organisation's own CA, ask IT to install that CA's root certificate."
            return 1 ;;
        6) echo "Couldn't find $url: check the address."; return 1 ;;
        7|28) echo "$url didn't answer. Check the address, and that you're on the right network or VPN."; return 1 ;;
        22) echo "$url answered, but not as Ostraka does (no /api/meta)."; return 1 ;;
        *) echo "Couldn't reach $url: ${body##*curl: }"; return 1 ;;
    esac
    if printf '%s' "$body" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"Ostraka"'; then return 0; fi
    echo "$url answered, but it isn't Ostraka."
    return 1
}

# ── First run ─────────────────────────────────────────────────────────────────

# Ask for a server address until one checks out; saves it. Fails if the person gives up.
ask_server() {
    local typed server why current
    current="$(head -n1 "$CONFIG/server" 2>/dev/null | tr -d '[:space:]')"
    [ "$current" = local ] && current=""
    while true; do
        typed="$(zenity --entry --title="Connect to your team's Ostraka" --width=460 \
            --text="Your team's Ostraka server, for example ostraka.example.org (https):" \
            --entry-text="$current" 2>/dev/null)" || return 1
        current="$typed"
        if ! server="$(normalise_server "$typed")"; then
            say error "'$typed' isn't a server address. Give its name, such as ostraka.example.org: Ostraka connects over https."
            continue
        fi
        if why="$(check_server "$server")"; then
            mkdir -p "$CONFIG"
            printf '%s\n' "$server" > "$CONFIG/server"
            log "Server set to $server"
            return 0
        fi
        say error "$why"
    done
}

# What local mode needs that isn't there: sets PROBLEM (dialog text) and DOCKER_GUIDE, and
# succeeds when there is a problem. Docker itself is never installed from here.
docker_problem() {
    local packages="the docker.io and docker-compose-v2 packages (Ubuntu), or Docker's own docker-ce and docker-compose-plugin"
    local info
    PROBLEM=""
    # Rocky, RHEL and Alma ship Podman, and podman-docker answers to `docker`
    if grep -qiE '^ID(_LIKE)?=.*(rhel|fedora|centos)' /etc/os-release 2>/dev/null; then
        packages="docker-ce and docker-compose-plugin from Docker's own repository (the distribution's Podman isn't supported)"
        DOCKER_GUIDE=https://docs.docker.com/engine/install/rhel/
    fi
    if ! command -v docker &>/dev/null; then
        PROBLEM="Ostraka runs on Docker Engine with its compose plugin, which isn't installed.\n\nInstall $packages, then open Ostraka again."
    elif docker --version 2>/dev/null | grep -qi podman; then
        PROBLEM="Ostraka runs on Docker Engine, but 'docker' here is Podman.\n\nInstall $packages, then open Ostraka again."
    elif ! info="$(docker info 2>&1)"; then
        if printf '%s' "$info" | grep -qi "permission denied"; then
            DOCKER_GUIDE=https://docs.docker.com/engine/install/linux-postinstall/
            # getent sees the group as it is now; id -nG would only show this session's
            if getent group docker 2>/dev/null | cut -d: -f4 | tr ',' '\n' | grep -qx "$USER"; then
                PROBLEM="You're in the docker group, but this session started before you were added.\n\nLog out and back in, then open Ostraka again."
            else
                PROBLEM="Docker is installed, but you're not allowed to use it.\n\nAdd yourself to the docker group, then log out and back in:\n\n    sudo usermod -aG docker $USER"
            fi
        else
            PROBLEM="Docker is installed but isn't running.\n\nStart it, then open Ostraka again:\n\n    sudo systemctl enable --now docker"
        fi
    elif ! docker compose version &>/dev/null; then
        DOCKER_GUIDE=https://docs.docker.com/compose/install/linux/
        PROBLEM="Docker's compose plugin isn't installed (docker-compose-v2 on Ubuntu, docker-compose-plugin from Docker's repository).\n\nInstall it, then open Ostraka again."
    fi
    [ -n "$PROBLEM" ]
}

check_docker() {
    docker_problem || return 0
    log "Docker: $PROBLEM"
    if ! has_zenity; then
        say error "$PROBLEM\n\n$DOCKER_GUIDE"
    elif zenity --question --title=Ostraka --width=460 --ok-label="Open the guide" --cancel-label=Close \
        --text="$PROBLEM" 2>/dev/null; then
        xdg-open "$DOCKER_GUIDE" &>/dev/null &
    fi
    return 1
}

# clear_tree <dir>: empty a copy a package unpacked, keeping .env, the version marker and the
# names given after it, so an update mirrors the new tree (a removed migration left behind would
# give Alembic two heads). Only a folder carrying .ostraka-version and no .git, and only inside it:
# rm -rf on a top-level symlink removes the link, not what it points at.
clear_tree() {
    local dir="$1" keep args=()
    shift
    [ -n "$dir" ] && [ -d "$dir" ] && [ ! -L "$dir" ] && [ -f "$dir/.ostraka-version" ] && [ ! -e "$dir/.git" ] \
        || return 1
    dir="$(cd -P "$dir" && pwd)" || return 1
    case "$dir" in /|"$(cd -P "$HOME" && pwd)") return 1 ;; esac
    for keep in .env .ostraka-version "$@"; do args+=(! -name "$keep"); done
    find "$dir" -mindepth 1 -maxdepth 1 "${args[@]}" -exec rm -rf -- {} +
}

# unpack_to <dir>: the bundled source into dir. git archive with or without --prefix: strip the
# top directory only when there is one. It unpacks beside the old files first, so a bad tarball
# leaves them as they were; then a copy this app unpacked before is cleared and the new files
# moved in, so nothing from the old version stays.
unpack_to() {
    local target="$1" strip=1 staging
    mkdir -p "$target" || return 1
    tar -tzf "$BUNDLED" | grep -qxE '(\./)?docker-compose\.yml' && strip=0
    staging="$(mktemp -d "$target/.ostraka-unpack.XXXXXX")" || return 1
    if ! tar -xzf "$BUNDLED" -C "$staging" --strip-components "$strip" >> "$LOG" 2>&1 \
        || ! is_tree "$staging"; then
        rm -rf -- "$staging"
        return 1
    fi
    if [ -f "$target/.ostraka-version" ]; then
        clear_tree "$target" "${staging##*/}" || { rm -rf -- "$staging"; return 1; }
    fi
    find "$staging" -mindepth 1 -maxdepth 1 -exec mv -f -- {} "$target/" \; >> "$LOG" 2>&1 \
        || { rm -rf -- "$staging"; return 1; }
    rmdir -- "$staging"
    app_version > "$target/.ostraka-version"
}

# Where local mode runs: a checkout runs itself; a package uses OSTRAKA_HOME, unpacked if need be
local_tree() {
    if [ ! -f "$BUNDLED" ] && is_tree "$HERE/../.."; then (cd -P "$HERE/../.." && pwd); return 0; fi
    if is_tree "$HOME_TREE"; then echo "$HOME_TREE"; return 0; fi
    [ -f "$BUNDLED" ] || { say error "Can't find Ostraka's files. Set OSTRAKA_HOME to a copy, or reinstall."; return 1; }
    # A copy this app unpacked that's lost its files (an update cut short) is unpacked again
    if [ -e "$HOME_TREE" ] && [ -n "$(ls -A "$HOME_TREE" 2>/dev/null)" ] \
        && { [ ! -f "$HOME_TREE/.ostraka-version" ] || [ -e "$HOME_TREE/.git" ]; }; then
        say error "$HOME_TREE already exists and isn't Ostraka. Move it aside, or set OSTRAKA_HOME to another folder."
        return 1
    fi
    notify "Putting Ostraka's files in $HOME_TREE…"
    unpack_to "$HOME_TREE" || { say error "Couldn't unpack Ostraka into $HOME_TREE. See $LOG"; return 1; }
    echo "$HOME_TREE"
}

# version_newer A B: A sorts after B
version_newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ]; }

# A tree a package unpacked carries .ostraka-version; a git checkout is never overwritten
maybe_update() {
    local root="$1" have want
    [ -f "$BUNDLED" ] && [ -f "$root/.ostraka-version" ] && [ ! -e "$root/.git" ] || return 0
    have="$(cat "$root/.ostraka-version")"
    want="$(app_version)"
    version_newer "$want" "$have" || return 0
    if ! has_zenity || zenity --question --title=Ostraka --width=460 --ok-label=Update --cancel-label="Not now" \
        --text="Update Ostraka in $root from $have to $want?\n\nYour settings (.env) and incidents are kept. This start rebuilds the images, which takes a few minutes." \
        2>/dev/null; then
        notify "Updating Ostraka's files to $want…"
        unpack_to "$root" || say error "Couldn't update $root. See $LOG"
    fi
}

# The first-run question. Saves `local` or a server; fails if the person closes it.
setup() {
    local choice
    if ! has_zenity; then
        say error "Setting Ostraka up needs zenity (sudo apt install zenity, or sudo dnf install zenity)."
        return 1
    fi
    choice="$(zenity --list --radiolist --title="Set up Ostraka" --width=560 --height=260 \
        --text="How will you use Ostraka?" --column="" --column="" --hide-header \
        TRUE "Connect to your team's Ostraka" \
        FALSE "Run Ostraka on this computer" 2>/dev/null)" || return 1
    if [[ "$choice" == Connect* ]]; then
        ask_server
        return
    fi
    check_docker || return 1
    mkdir -p "$CONFIG" "$STATE"
    echo local > "$CONFIG/server"
    touch "$ADMIN_PENDING"
    log "Set up to run on this computer"
}

# ── Local mode ────────────────────────────────────────────────────────────────

healthy() { curl -q -s --max-time 2 "$1/api/health" 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; }

# `ostraka start --progress` lines (progress <0-100> <message>) as zenity --progress input.
# Everything goes to the log. 100 at the end, so the dialog closes on failure too.
progress_feed() {
    local line pct message
    while IFS= read -r line; do
        printf '%s\n' "$line" >> "$LOG"
        if [[ "$line" =~ ^progress[[:space:]]+([0-9]{1,3})[[:space:]]*(.*)$ ]]; then
            pct="${BASH_REMATCH[1]}"; message="${BASH_REMATCH[2]}"
            [ "$pct" -ge 100 ] && pct=99
            echo "$pct"
            [ -n "$message" ] && echo "# $message"
        fi
    done
    echo 100
}

start_stack() {
    local root="$1" status_file status
    if ! has_zenity; then
        notify "Starting Ostraka…"
        (cd "$root" && ./ostraka start) >> "$LOG" 2>&1
        return
    fi
    status_file="$(mktemp)"
    { (cd "$root" && ./ostraka start --progress) 2>&1; echo $? > "$status_file"; } | progress_feed |
        zenity --progress --title=Ostraka --width=460 --auto-close --no-cancel \
            --text="Starting Ostraka" --percentage=0 2>/dev/null
    status="$(cat "$status_file" 2>/dev/null)"
    rm -f "$status_file"
    return "${status:-1}"
}

local_url() {
    local url
    url="$(cd "$1" && ./ostraka url 2>/dev/null | tail -n1)"
    [[ "$url" == http* ]] && echo "$url" || echo "$LOCAL_URL_DEFAULT"
}

# has_admin from `ostraka status --json`: true, false, or nothing when it doesn't say
has_admin() {
    (cd "$1" && ./ostraka status --json 2>/dev/null) | tr -d '\n' \
        | sed -n 's/.*"has_admin"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p'
}

# A fresh install has no admin: ask for one, and open their invitation instead of the sign-in page
first_admin() {
    local root="$1" email out status link known
    has_zenity || return 1
    known="$(has_admin "$root")"
    case "$known" in
        true) rm -f "$ADMIN_PENDING"; return 1 ;;
        false) ;;
        *) [ -f "$ADMIN_PENDING" ] || return 1 ;;
    esac
    while true; do
        email="$(zenity --entry --title="Ostraka's first admin" --width=460 \
            --text="Ostraka is running. Who's its first admin?\n\nTheir email address (they'll set a password and an authenticator app next):" \
            2>/dev/null)" || return 1
        email="$(printf '%s' "$email" | tr -d '[:space:]')"
        [[ "$email" =~ ^[^@]+@[^@]+\.[^@]+$ ]] && break
        say error "'$email' isn't an email address."
    done
    out="$(cd "$root" && ./ostraka first-admin "$email" 2>>"$LOG")"
    status=$?
    # Only "invited" and "there's an admin already" settle it: any other failure (the database
    # not ready, say) asks again next time
    if [ "$status" -eq "$EXIT_HAS_ADMIN" ]; then
        rm -f "$ADMIN_PENDING"
        say info "Ostraka already has an admin, so it opens at the sign-in page."
        return 1
    fi
    link="$(printf '%s\n' "$out" | grep -E '^https?://' | tail -n1)"
    if [ "$status" -ne 0 ] || [ -z "$link" ]; then
        say error "Couldn't make the first admin's invitation ('ostraka first-admin' exited $status). See $LOG\n\nOstraka asks again next time it opens."
        return 1
    fi
    rm -f "$ADMIN_PENDING"
    log "First admin invited: $email"
    echo "$link"
}

run_local() {
    local root url link
    check_docker || exit 1
    root="$(local_tree)" || exit 1
    maybe_update "$root"
    url="$(local_url "$root")"
    if ! healthy "$url"; then
        if ! start_stack "$root"; then
            say error "Ostraka didn't start. The log is $LOG, and 'ostraka logs' in $root shows the containers'."
            exit 1
        fi
    fi
    # The invitation link carries a token, so it goes through a hand-off page, never argv
    if link="$(first_admin "$root")"; then open_window "$link" handoff; fi
    open_window "$url"
}

# ── The window ────────────────────────────────────────────────────────────────

# handoff_page <url> <parent>: a one-time page in a new folder under parent (700, the page 600)
# that sends the browser on to url; prints its file:// address. A URL with a secret in it (the
# first admin's invitation) would otherwise be on the browser's command line, which any local
# user can read in /proc and then use against 127.0.0.1. The folder is deleted HANDOFF_TTL
# seconds later, and older ones left by a shutdown on the next hand-off.
handoff_page() {
    local url="$1" parent="$2" dir
    # Embedded in HTML and a script: nothing that could close either
    [[ "$url" =~ ^https?://[^[:space:]\"\'\<\>\\\`]+$ ]] || return 1
    mkdir -p "$parent" || return 1
    find "$parent" -mindepth 1 -maxdepth 1 -name 'ostraka-handoff.*' -mmin +5 -exec rm -rf -- {} + 2>/dev/null
    dir="$(mktemp -d "$parent/ostraka-handoff.XXXXXX")" || return 1
    chmod 700 "$dir"
    (
        umask 077
        printf '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<meta http-equiv="refresh" content="0;url=%s"><title>Ostraka</title>
<script>location.replace("%s")</script>\n' "$url" "$url" > "$dir/open.html"
    ) || { rm -rf -- "$dir"; return 1; }
    ( sleep "$HANDOFF_TTL"; rm -rf -- "$dir" ) >/dev/null 2>&1 < /dev/null &
    echo "file://$dir/open.html"
}

# open_window <url> [handoff]: handoff when the URL holds a secret (handoff_page)
open_window() {
    local url="$1" handoff="${2:-}" browser path profile="$PROFILE"
    for browser in chromium chromium-browser google-chrome google-chrome-stable brave-browser microsoft-edge \
        microsoft-edge-stable; do
        path="$(command -v "$browser")" || continue
        # A snap can't read hidden folders in $HOME, so its profile (and a hand-off page) lives in
        # the snap's own
        [[ "$(readlink -f "$path")" == /snap/* ]] && profile="$HOME/snap/${browser%%-*}/common/ostraka"
        mkdir -p "$profile"
        if [ -n "$handoff" ]; then url="$(handoff_page "$1" "${profile%/*}")" || return 1; fi
        # --class with its own profile gives the window WM_CLASS Ostraka, which the menu entry's
        # StartupWMClass matches, so the dock shows Ostraka's icon
        # shellcheck disable=SC2093 # the first browser found replaces this script; the rest are fallbacks
        exec "$browser" --app="$url" --class=Ostraka --user-data-dir="$profile" --no-first-run \
            --no-default-browser-check
    done
    if [ -n "$handoff" ]; then url="$(handoff_page "$1" "$STATE")" || return 1; fi
    exec xdg-open "$url"
}

# The URL the window shows: a checked server, or local mode's (which starts the stack first)
run() {
    local where value server why allow_http=""
    read -r where value <<< "$(effective_server)"
    if [ "$where" = none ]; then
        setup || exit 1
        read -r where value <<< "$(effective_server)"
    fi
    if [ "$value" = local ]; then run_local; return; fi
    [ "$where" = saved ] || allow_http=yes
    if ! server="$(normalise_server "$value" "$allow_http")"; then
        say error "'$value' (from $(describe_source "$where")) isn't a server address Ostraka can use."
        exit 1
    fi
    if ! why="$(check_server "$server")"; then
        if [ "$where" = saved ] && has_zenity && zenity --question --title=Ostraka --width=460 \
            --ok-label="Change server" --cancel-label=Close --text="$why" 2>/dev/null; then
            ask_server && run
            return
        fi
        [ "$where" = saved ] || say error "$why\n\nThe address comes from $(describe_source "$where")."
        exit 1
    fi
    open_window "$server"
}

describe_source() {
    case "$1" in
        it) echo "$CLIENT_JSON, set by IT" ;;
        env) echo "OSTRAKA_SERVER" ;;
        *) echo "$CONFIG/server" ;;
    esac
}

# The server in use, for Licence: nothing in local mode
current_server() {
    local where value
    read -r where value <<< "$(effective_server)"
    [ "$where" != none ] && [ "$value" != local ] && normalise_server "$value" yes
}

# ── Menu entry actions (the menus on other platforms) ─────────────────────────

action_stop() {
    local root
    if current_server >/dev/null; then
        say info "Ostraka runs on $(current_server); its admins start and stop it."
        return 0
    fi
    root="$(local_tree)" || return 1
    notify "Stopping Ostraka…"
    if (cd "$root" && ./ostraka stop) >> "$LOG" 2>&1; then notify "Ostraka is stopped. Your incidents are kept."; \
        else say error "'ostraka stop' failed. See $LOG"; fi
}

action_change_server() {
    if is_locked; then
        say info "Your IT team set Ostraka's server ($(current_server)), so it can't be changed here."
        return 0
    fi
    has_zenity || { say error "Changing the server needs zenity."; return 1; }
    ask_server || return 0
    [ -n "${OSTRAKA_SERVER:-}" ] && say warning "Saved, but OSTRAKA_SERVER is set ($OSTRAKA_SERVER), and it wins while it is."
    run
}

action_licence() {
    local server copyright=/usr/share/doc/ostraka/copyright
    server="$(current_server)" || server="$LOCAL_URL_DEFAULT"
    # The page is public, so it works before sign-in; with the stack stopped, the terms that came
    # with the app stand in
    if curl -q -s -o /dev/null --max-time 5 --fail "$server/api/meta"; then open_window "$server/help/licence"; fi
    [ -f "$copyright" ] || copyright="$HERE/../../LICENSE"
    if has_zenity; then
        zenity --text-info --title="Ostraka: Licence" --width=720 --height=640 --filename="$copyright" 2>/dev/null
    else
        xdg-open "$copyright" &>/dev/null &
    fi
}

action_about() {
    say info "Ostraka $(app_version)\n\nIncident management and response for teams.\n\nLicensed under the Elastic License 2.0; paid editions add Ostraka's commercial terms.\nCopyright © 2026 cmdaltr"
}

main() {
    mkdir -p "$STATE" "$CONFIG"
    case "${1:-}" in
        --setup)
            if is_locked; then say info "Your IT team set Ostraka's server ($(current_server))."; exit 0; fi
            setup || exit 0
            run ;;
        --change-server) action_change_server ;;
        --stop) action_stop ;;
        --licence) action_licence ;;
        --about) action_about ;;
        "") run ;;
        *) echo "usage: ostraka-app [--setup | --change-server | --stop | --licence | --about]" >&2; exit 2 ;;
    esac
}

# Sourced by tests/test-launcher.sh for its functions; run otherwise
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
