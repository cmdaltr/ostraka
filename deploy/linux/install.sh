#!/usr/bin/env bash
# Set up Ostraka as a team server on Linux: the source tree in /opt/ostraka (or --dir), .env with
# random secrets and the public address, the stack started, and the first admin's invitation link.
# `ostraka server init` runs this with its own arguments; deploy/README.md has the rest.
#
# Safe to run again: secrets already in .env are kept, so the database and every TOTP secret
# (encrypted with OSTRAKA_SECRET_KEY) still open. Only what you pass is changed.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_SOURCE=$(cd "$SCRIPT_DIR/../.." && pwd)
WEB_PORT=24747 # docker-compose.yml publishes the web container on 127.0.0.1:24747
CADDY_COMPOSE=deploy/docker-compose.caddy.yml
# Written to .env, so the stack is the same compose project whoever runs `docker compose` in the tree:
# this script, an admin, or the host's `ostraka` command (which honours it over its own default).
PROJECT=ostraka

usage() {
    cat <<'EOF'
Set up Ostraka as a team server on Linux.

Usage: deploy/linux/install.sh [HOSTNAME | URL] [options]

  HOSTNAME | URL       the address people open Ostraka at: ostraka.example.org (https:// is assumed)
                       or a full URL. Written to .env as OSTRAKA_PUBLIC_URL; links in invitations and
                       notifications use it. Same as --url
  --url URL            as above
  --admin EMAIL        create the first admin's invitation and print its link
  --dir DIR            where the source tree goes (default /opt/ostraka)
  --source SRC         where it comes from: an Ostraka source tree, a .tar.gz of one, or a git URL
                       (default: the tree this script is in)
  --ref REF            with a git URL: the branch or tag to check out
  --caddy [MODE]       also run Caddy in the stack for HTTPS on 80 and 443. MODE is acme (default:
                       a Let's Encrypt certificate; the name must reach this host on 80 and 443) or
                       internal (Caddy's own CA, for clients you can give its root certificate)
  --no-caddy           stop running Caddy (a reverse proxy of your own takes over)
  --no-start           write the tree and .env, but don't start the stack
  -h, --help           this help

Without --caddy, Ostraka listens on http://127.0.0.1:24747 only, and a reverse proxy you run in
front of it serves HTTPS: deploy/README.md has Caddy and nginx examples.

Needs Docker Engine with the compose plugin (docker compose version 2), run by root or a member of
the docker group; curl or wget is used if there, and git only for a git URL.

Examples:
  sudo ./deploy/linux/install.sh ostraka.example.org --admin you@example.org --caddy
  ./deploy/linux/install.sh --url https://ir.example.org --dir /srv/ostraka --admin you@example.org
EOF
}

say() { printf '%s\n' "$*"; }
warn() { printf 'install.sh: %s\n' "$*" >&2; }
step() { printf '\n==> %s\n' "$*"; }
die() {
    printf 'install.sh: %s\n' "$1" >&2
    exit "${2:-1}"
}

# ---- arguments ------------------------------------------------------------------------------------

URL=""
ADMIN=""
DIR="/opt/ostraka"
SOURCE=""
REF=""
CADDY=""      # "", acme, internal or off
START=1

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -h | --help) usage; exit 0 ;;
            --url) URL=${2:?--url needs an address}; shift ;;
            --url=*) URL=${1#*=} ;;
            --admin) ADMIN=${2:?--admin needs an email address}; shift ;;
            --admin=*) ADMIN=${1#*=} ;;
            --dir) DIR=${2:?--dir needs a directory}; shift ;;
            --dir=*) DIR=${1#*=} ;;
            --source) SOURCE=${2:?--source needs a tree, tarball or git URL}; shift ;;
            --source=*) SOURCE=${1#*=} ;;
            --ref) REF=${2:?--ref needs a branch or tag}; shift ;;
            --ref=*) REF=${1#*=} ;;
            --caddy)
                # The mode is optional, so only take the next word when it is one.
                case "${2:-}" in
                    acme | internal) CADDY=$2; shift ;;
                    *) CADDY=acme ;;
                esac
                ;;
            --caddy=*) CADDY=${1#*=} ;;
            --no-caddy) CADDY=off ;;
            --no-start) START=0 ;;
            -*) die "Unknown option '$1'. See --help." 2 ;;
            *)
                [ -z "$URL" ] || die "One address only (got '$URL' and '$1')." 2
                URL=$1
                ;;
        esac
        shift
    done

    case "$CADDY" in "" | acme | internal | off) ;; *) die "--caddy is acme or internal." 2 ;; esac
    if [ -n "$URL" ]; then
        case "$URL" in
            http://* | https://*) ;;
            */* | *" "*) die "'$URL' isn't a hostname or URL." 2 ;;
            *) URL="https://$URL" ;;
        esac
        URL=${URL%/}
        local rest=${URL#*://}
        case "$rest" in */*) die "The address is a site, not a page: drop the path from '$URL'." 2 ;; esac
    fi
    if [ -n "$ADMIN" ]; then
        case "$ADMIN" in *@*.*) ;; *) die "'$ADMIN' doesn't look like an email address." 2 ;; esac
    fi
    case "$DIR" in /*) ;; *) DIR="$(pwd)/$DIR" ;; esac
    DIR=${DIR%/}
}

# ---- checks ---------------------------------------------------------------------------------------

SUDO=""

check_host() {
    [ "$(uname -s)" = Linux ] || die "This sets up a Linux server. On Windows use deploy/windows/install-service.ps1; on a Mac, the desktop app or ./ostraka start."
    command -v docker >/dev/null 2>&1 || die "Docker isn't installed. Install Docker Engine and its compose plugin from https://docs.docker.com/engine/install/ (on Rocky, RHEL or Alma, Docker's own packages, not Podman), then run this again."
    # Output is captured before grep sees it: `cmd | grep -q` under pipefail fails with SIGPIPE when grep
    # stops reading before cmd has finished writing.
    local out
    out=$(docker --version 2>/dev/null || true)
    if grep -qi podman <<<"$out"; then
        die "'docker' here is Podman. Ostraka needs Docker Engine: https://docs.docker.com/engine/install/"
    fi
    docker compose version >/dev/null 2>&1 || die "The Docker compose plugin is missing ('docker compose'). Install docker-compose-plugin from Docker's repository."
    if ! out=$(docker info 2>&1); then
        if [ "$(id -u)" -ne 0 ] && grep -qi 'permission denied' <<<"$out"; then
            die "You can't use Docker as $(id -un). Run this with sudo, or: sudo usermod -aG docker $(id -un), then sign in again."
        fi
        die "Docker isn't running. Start it (sudo systemctl enable --now docker) and run this again."
    fi
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO=sudo; fi
}

random_hex() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$1"
    else
        od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'
    fi
}

# ---- the source tree ------------------------------------------------------------------------------

is_tree() { [ -f "$1/docker-compose.yml" ] && [ -d "$1/backend" ] && [ -d "$1/frontend" ]; }

is_empty() { [ -z "$(ls -A "$1")" ]; }

# An existing --dir must be empty or already an Ostraka tree: unpacking over anything else would mix
# Ostraka's files into it. Only a directory made here is handed to the running user; someone else's
# directory keeps its owner.
make_dir() {
    if [ -d "$DIR" ]; then
        [ -r "$DIR" ] && [ -w "$DIR" ] && [ -x "$DIR" ] ||
            die "$DIR isn't writable by $(id -un). Run with sudo, or choose --dir."
        is_empty "$DIR" || is_tree "$DIR" ||
            die "$DIR already holds files that aren't an Ostraka tree. Choose a new or empty --dir."
        return 0
    fi
    [ ! -e "$DIR" ] || die "$DIR exists and isn't a directory."
    if ! mkdir -p "$DIR" 2>/dev/null; then
        [ -n "$SUDO" ] || die "Can't create $DIR. Run with sudo, or choose --dir."
        $SUDO mkdir -p "$DIR" || die "Can't create $DIR. Run with sudo, or choose --dir."
        $SUDO chown "$(id -u):$(id -g)" "$DIR"
    fi
}

copy_tree() {
    # .env and the data never travel with a tree: they belong to the installation.
    tar -C "$1" -cf - \
        --exclude=./.git --exclude=./.env --exclude='./.env.*.bak' --exclude=node_modules --exclude=.venv \
        --exclude=__pycache__ --exclude=./frontend/dist --exclude=./.claude --exclude='*.fetch' . |
        tar -C "$DIR" -xf -
}

place_tree() {
    local src=${SOURCE:-$DEFAULT_SOURCE}
    step "Source tree in $DIR"
    if [ -d "$src" ] && [ "$(cd "$src" && pwd -P)" = "$(cd "$DIR" 2>/dev/null && pwd -P || true)" ]; then
        is_tree "$DIR" || die "$DIR isn't an Ostraka source tree."
        say "Using $DIR as it is."
        return
    fi
    if [ -d "$DIR/.git" ]; then
        # A checkout is someone's working copy: never overwrite it, as the desktop apps don't.
        is_tree "$DIR" || die "$DIR is a git checkout but not of Ostraka."
        say "$DIR is a git checkout; leaving its files alone (update it with git pull)."
        return
    fi
    make_dir
    case "$src" in
        http://* | https://* | git@* | ssh://* | *.git)
            command -v git >/dev/null 2>&1 || die "A git URL needs git installed."
            is_empty "$DIR" ||
                die "$DIR isn't empty, so it can't be cloned into. Choose another --dir, or give --source a tree or tarball."
            git clone --depth 1 ${REF:+--branch "$REF"} -- "$src" "$DIR"
            ;;
        *.tar.gz | *.tgz)
            [ -f "$src" ] || die "No such file: $src"
            # A tarball with one top-level folder (ostraka-0.1.0/...) is unpacked without it.
            # The listing is read once and searched as a string: `tar -t | head -1` under pipefail
            # fails with SIGPIPE once the listing outgrows the pipe, as a release's does.
            # The top folder is found by where docker-compose.yml is, not by the first entry, which on
            # a tarball made on a Mac can be an AppleDouble ._ file.
            local listing strip=0
            listing=$(tar -tzf "$src") || die "Can't read $src as a .tar.gz."
            if ! grep -Eqx -- '(\./)?docker-compose\.yml' <<<"$listing" &&
                grep -Eqx -- '[^/]+/docker-compose\.yml' <<<"$listing"; then
                strip=1
            fi
            tar -xzf "$src" -C "$DIR" --strip-components="$strip"
            ;;
        *)
            [ -d "$src" ] || die "No such source: $src"
            is_tree "$src" || die "$src isn't an Ostraka source tree (no docker-compose.yml, backend and frontend)."
            copy_tree "$src"
            ;;
    esac
    is_tree "$DIR" || die "$DIR doesn't hold an Ostraka source tree after unpacking $src."
    version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$DIR/backend/ostraka/__init__.py" 2>/dev/null || true)
    [ -z "$version" ] || printf '%s\n' "$version" >"$DIR/.ostraka-version"
    say "Placed $src in $DIR${version:+ (version $version)}."
}

# ---- .env -----------------------------------------------------------------------------------------

ENV_FILE=""

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }

# Replace KEY's line, or add it. Written through a temporary file in the same directory so a full
# disk can't leave half a .env, and with 600 from the start so the secrets are never readable.
env_set() {
    local key=$1 value=$2 tmp
    tmp=$(umask 077 && mktemp "$ENV_FILE.XXXXXX")
    awk -v k="$key" -v v="$value" '
        BEGIN { done = 0 }
        index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }' "$ENV_FILE" >"$tmp"
    mv "$tmp" "$ENV_FILE"
}

env_unset() {
    local tmp
    tmp=$(umask 077 && mktemp "$ENV_FILE.XXXXXX")
    awk -v k="$1" 'index($0, k "=") != 1' "$ENV_FILE" >"$tmp"
    mv "$tmp" "$ENV_FILE"
}

write_env() {
    ENV_FILE="$DIR/.env"
    step "Settings in $ENV_FILE"
    if [ ! -f "$ENV_FILE" ]; then
        (
            umask 077
            if [ -f "$DIR/.env.example" ]; then cp "$DIR/.env.example" "$ENV_FILE"; else : >"$ENV_FILE"; fi
        )
        say "Wrote a new .env."
    else
        say "Keeping the secrets already in .env."
    fi
    chmod 600 "$ENV_FILE"

    [ -n "$(env_get POSTGRES_PASSWORD)" ] || env_set POSTGRES_PASSWORD "$(random_hex 24)"
    [ -n "$(env_get OSTRAKA_SECRET_KEY)" ] || env_set OSTRAKA_SECRET_KEY "$(random_hex 32)"

    if [ -n "$URL" ]; then
        env_set OSTRAKA_PUBLIC_URL "$URL"
    elif [ -z "$(env_get OSTRAKA_PUBLIC_URL)" ] || [ "$(env_get OSTRAKA_PUBLIC_URL)" = "http://127.0.0.1:$WEB_PORT" ]; then
        # .env.example's address is this computer: say so, since nobody else can open those links
        env_set OSTRAKA_PUBLIC_URL "http://127.0.0.1:$WEB_PORT"
        say "No address given: links will use http://127.0.0.1:$WEB_PORT until you run this again with one."
    fi
    URL=$(env_get OSTRAKA_PUBLIC_URL)
    # One already there is kept: it names the volumes that hold the data.
    if [ -n "$(env_get COMPOSE_PROJECT_NAME)" ]; then
        PROJECT=$(env_get COMPOSE_PROJECT_NAME)
    else
        env_set COMPOSE_PROJECT_NAME "$PROJECT"
    fi

    case "$CADDY" in
        acme | internal)
            case "$URL" in https://*) ;; *) die "--caddy needs an https:// address (got $URL)." 2 ;; esac
            case "${URL#https://}" in *:*) die "--caddy serves 443, so the address can't name a port ($URL)." 2 ;; esac
            # COMPOSE_FILE in .env makes every later `docker compose` (and `ostraka start`) include
            # Caddy without being told.
            env_set COMPOSE_FILE "docker-compose.yml:$CADDY_COMPOSE"
            if [ "$CADDY" = internal ]; then env_set OSTRAKA_CADDY_CERTS "--internal-certs"; else env_set OSTRAKA_CADDY_CERTS ""; fi
            say "Caddy will serve $URL on 80 and 443 ($([ "$CADDY" = internal ] && echo "its own CA" || echo "Let's Encrypt"))."
            ;;
        off)
            env_unset COMPOSE_FILE
            env_unset OSTRAKA_CADDY_CERTS
            say "Caddy is off: put your own reverse proxy in front of 127.0.0.1:$WEB_PORT."
            ;;
    esac
    say "OSTRAKA_PUBLIC_URL=$URL"
}

caddy_on() { [ "$(env_get COMPOSE_FILE)" = "docker-compose.yml:$CADDY_COMPOSE" ]; }

open_firewall() {
    caddy_on || return 0
    # A firewall that won't take the rule is worth a warning, not the end of the setup.
    local ufw_status=""
    if command -v ufw >/dev/null 2>&1; then ufw_status=$($SUDO ufw status 2>/dev/null || true); fi
    if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
        if $SUDO firewall-cmd --quiet --permanent --add-service=http --add-service=https &&
            $SUDO firewall-cmd --quiet --reload; then
            say "Opened 80 and 443 in firewalld's default zone."
        else
            warn "Couldn't open 80 and 443 in firewalld. Open them yourself: firewall-cmd --permanent --add-service=http --add-service=https && firewall-cmd --reload"
        fi
    elif grep -q 'Status: active' <<<"$ufw_status"; then
        if $SUDO ufw allow 80,443/tcp >/dev/null; then
            say "Opened 80 and 443 in ufw."
        else
            warn "Couldn't open 80 and 443 in ufw. Open them yourself: ufw allow 80,443/tcp"
        fi
    fi
}

# ---- start ----------------------------------------------------------------------------------------

compose() { (cd "$DIR" && COMPOSE_PROJECT_NAME=$PROJECT docker compose "$@"); }

healthy() {
    local url="http://127.0.0.1:$WEB_PORT/api/health" answer=""
    if command -v curl >/dev/null 2>&1; then
        answer=$(curl -fsS --max-time 5 "$url" 2>/dev/null || true)
    elif command -v wget >/dev/null 2>&1; then
        answer=$(wget -qO- -T 5 "$url" 2>/dev/null || true)
    else
        answer=$(compose exec -T backend python -c \
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:6176/api/health').read().decode())" \
            2>/dev/null || true)
    fi
    grep -q '"status": *"ok"' <<<"$answer"
}

start_stack() {
    step "Starting Ostraka (the first build takes a few minutes)"
    compose up -d --build --remove-orphans
    local waited=0
    until healthy; do
        [ "$waited" -lt 300 ] || die "Ostraka didn't come up within five minutes. See: cd $DIR && docker compose logs backend web"
        sleep 3
        waited=$((waited + 3))
    done
    say "Up: http://127.0.0.1:$WEB_PORT answers."
}

first_admin() {
    [ -n "$ADMIN" ] || return 0
    step "First admin"
    local out rc=0 link
    out=$(compose exec -T backend ostraka bootstrap-admin "$ADMIN" 2>&1) || rc=$?
    link=$(grep -Eo 'https?://[^[:space:]]+/invite/[^[:space:]]+' <<<"$out" | sed -n 1p || true)
    if [ "$rc" -eq 0 ] && [ -n "$link" ]; then
        INVITE_LINK=$link
        say "Invitation for $ADMIN (admin): open it within the next few days, set a password and scan the QR code."
    elif grep -q 'already has an admin' <<<"$out"; then
        say "Ostraka already has an admin: they invite everyone else from People."
    else
        printf '%s\n' "$out" >&2
        die "Couldn't create the invitation."
    fi
}

INVITE_LINK=""

finish() {
    step "Done"
    if caddy_on; then
        say "Ostraka: $URL (Caddy, on 80 and 443)."
        if [ "$(env_get OSTRAKA_CADDY_CERTS)" = "--internal-certs" ]; then
            say "Clients must trust Caddy's root certificate. Export it with:"
            say "  cd $DIR && docker compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > ostraka-root.crt"
            say "then distribute it (MDM, Group Policy, update-ca-certificates); the ost client takes OSTRAKA_CA_BUNDLE."
        fi
    else
        say "Ostraka listens on http://127.0.0.1:$WEB_PORT only."
        say "Serve $URL through a reverse proxy on this host: Caddy and nginx examples are in"
        say "  $DIR/deploy/README.md"
        say "or run this again with --caddy."
    fi
    if [ -n "$INVITE_LINK" ]; then
        say ""
        say "  $INVITE_LINK"
        say ""
    elif [ -z "$ADMIN" ]; then
        say "Make the first admin with: cd $DIR && docker compose exec backend ostraka bootstrap-admin you@example.org"
    fi
}

main() {
    parse_args "$@"
    check_host
    place_tree
    write_env
    if [ "$START" = 1 ]; then
        open_firewall
        start_stack
        first_admin
        finish
    else
        step "Not started (--no-start)"
        say "Start it with: cd $DIR && docker compose up -d --build"
    fi
}

# Sourced by the tests to reach the functions; run, it sets up.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
