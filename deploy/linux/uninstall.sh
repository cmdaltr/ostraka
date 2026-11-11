#!/usr/bin/env bash
# Stop the Ostraka team server that deploy/linux/install.sh set up. Keeps data by default.
#
# Usage (as root, or a member of the docker group):
#     sudo deploy/linux/uninstall.sh [--dir DIR] [--purge [--yes]]
#
# --purge also removes the stack's volumes (the database, Redis and Caddy's certificates) and .env with
# its secrets, after asking unless --yes is given. The code dir (/opt/ostraka) is always left in place;
# remove it by hand if you want it gone.
set -euo pipefail

DIR=/opt/ostraka
PURGE=""
YES=""

usage() {
    sed -n '2,9s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"
}

die() {
    printf 'uninstall.sh: %s\n' "$1" >&2
    exit "${2:-1}"
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -h | --help) usage; exit 0 ;;
            --dir) DIR=${2:?--dir needs a directory}; shift ;;
            --dir=*) DIR=${1#*=} ;;
            --purge) PURGE=1 ;;
            --yes | -y) YES=1 ;;
            *) die "Unknown option '$1'. See --help." 2 ;;
        esac
        shift
    done
    DIR=${DIR%/}
}

env_get() { [ -f "$DIR/.env" ] && sed -n "s/^$1=//p" "$DIR/.env" | tail -1; }

confirm() {
    [ -z "$YES" ] || return 0
    # /dev/tty is there even without a terminal; opening it is what fails.
    { : </dev/tty; } 2>/dev/null || die "--purge deletes every incident in $DIR's stack. Run it from a terminal to confirm, or add --yes." 2
    local answer=""
    printf 'Delete all of Ostraka'\''s data (the database, Redis, Caddy'\''s certificates) and .env? This cannot be undone. [y/N] ' >/dev/tty
    read -r answer </dev/tty || true
    case "$answer" in y | Y | yes | YES) ;; *) die "Nothing removed." ;; esac
}

main() {
    parse_args "$@"
    [ -f "$DIR/docker-compose.yml" ] || die "$DIR isn't an Ostraka tree (no docker-compose.yml). Give --dir."
    command -v docker >/dev/null 2>&1 || die "Docker isn't installed, so there's no stack to stop."

    # The project install.sh wrote; before it did, compose named the project after the folder.
    local project caddy
    project=$(env_get COMPOSE_PROJECT_NAME || true)
    project=${project:-$(basename "$DIR")}
    caddy=$(env_get COMPOSE_FILE || true)

    [ -z "$PURGE" ] || confirm

    if [ -n "$PURGE" ]; then
        echo "==> stopping Ostraka (project $project) and removing its volumes"
        (cd "$DIR" && COMPOSE_PROJECT_NAME=$project docker compose down --volumes --remove-orphans)
        # Volumes from a compose file no longer in COMPOSE_FILE (Caddy after --no-caddy) aren't seen by down.
        local left
        left=$(docker volume ls -q --filter "label=com.docker.compose.project=$project")
        if [ -n "$left" ]; then
            # shellcheck disable=SC2086 # one volume name per word
            docker volume rm $left >/dev/null
        fi
        rm -f "$DIR/.env"
        echo "    removed the volumes and $DIR/.env"
    else
        echo "==> stopping Ostraka (project $project)"
        (cd "$DIR" && COMPOSE_PROJECT_NAME=$project docker compose down --remove-orphans)
        echo "    kept data (the $project volumes) and $DIR/.env. Re-run with --purge to remove them."
    fi
    echo "    left the code in $DIR; remove it by hand if you want it gone."
    if [ -n "$caddy" ]; then
        echo "    firewall rules opened for Caddy (80 and 443) are left as they are."
    fi
}

main "$@"
