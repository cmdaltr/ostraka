#!/usr/bin/env bash
# Tidy what developing and testing Ostraka leaves behind. Without --apply it only lists what it
# would do; with --apply it does it. Files go to the Bin (~/.Trash) where there is one, so a
# mistake can be undone. It never touches .env, the data volumes of a running stack, other
# projects' containers, git history or a worktree that's still in use.
#
#   scripts/housekeeping.sh            list
#   scripts/housekeeping.sh --apply    tidy
#   scripts/housekeeping.sh --apply --images   also remove the test-only Docker images
#
# What it tidies:
#   - Postgres containers from `make test-db` (labelled ostraka.test=1, or an unlabelled
#     postgres:16-alpine whose database is called ostraka) that were left running
#   - OneDrive conflict copies in the repo: *.fetch, *-MacBook.*, "name 2.ext"
#   - test and build output: frontend/test-results, playwright-report, .pytest_cache, .ruff_cache,
#     dist/ (release builds; rebuild with make release)
#   - git worktrees whose folders are gone, and remote branches deleted on GitHub
#   - with --images: the images the script tests build (ostraka-pwsh-test, ostraka-wixl)
set -uo pipefail

ROOT="$(cd -P "$(dirname "$0")/.." && pwd)"
APPLY=0
IMAGES=0
for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=1 ;;
        --images) IMAGES=1 ;;
        -h | --help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "housekeeping.sh: unknown option $arg (try --help)" >&2; exit 2 ;;
    esac
done

if [ "$APPLY" -eq 1 ]; then verb="Removing"; else verb="Would remove"; fi
found=0

say() { printf '%s\n' "$*"; }
item() { found=$((found + 1)); printf '  %s\n' "$*"; }

# To the Bin where there is one, so nothing is lost for good
discard() {
    local path="$1"
    [ "$APPLY" -eq 1 ] || return 0
    if [ -d "$HOME/.Trash" ]; then
        local name
        name="$(basename "$path")"
        [ -e "$HOME/.Trash/$name" ] && name="$name $(date +%H%M%S)-$$"
        mv -- "$path" "$HOME/.Trash/$name"
    else
        rm -rf -- "$path"
    fi
}

docker_ok() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

say "Ostraka housekeeping in $ROOT"
[ "$APPLY" -eq 1 ] || say "(listing only: run with --apply to tidy)"

# ── Test databases left running ───────────────────────────────────────────────
say ""
say "Test databases"
if docker_ok; then
    ids="$(docker ps -q --filter label=ostraka.test=1)"
    # Older runs had no label: an unlabelled postgres:16-alpine is ours only if its database is ostraka
    for id in $(docker ps -q --filter ancestor=postgres:16-alpine); do
        case " $ids " in *" $id "*) continue ;; esac
        if docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$id" | grep -qx 'POSTGRES_DB=ostraka'; then
            ids="$ids $id"
        fi
    done
    for id in $ids; do
        item "$verb container $id ($(docker ps --filter id="$id" --format '{{.Image}}, up {{.RunningFor}}'))"
        [ "$APPLY" -eq 1 ] && docker stop "$id" >/dev/null
    done
    [ -n "${ids// /}" ] || say "  none"
else
    say "  Docker isn't running: skipped"
fi

# ── OneDrive conflict copies ──────────────────────────────────────────────────
say ""
say "OneDrive conflict copies"
before=$found
while IFS= read -r -d '' path; do
    item "$verb ${path#"$ROOT"/}"
    discard "$path"
done < <(find "$ROOT" \( -name .git -o -name node_modules -o -name .venv -o -path "$ROOT/.claude/worktrees" \) -prune -o \
    \( -name '*.fetch' -o -name '*-MacBook.*' -o -name '*-MacBook' -o -name '* [0-9].*' \) -print0 2>/dev/null)
[ "$found" -gt "$before" ] || say "  none"

# ── Test and build output ─────────────────────────────────────────────────────
say ""
say "Test and build output"
before=$found
for rel in frontend/test-results frontend/playwright-report frontend/blob-report backend/.pytest_cache \
           .pytest_cache backend/.ruff_cache .ruff_cache dist; do
    [ -e "$ROOT/$rel" ] || continue
    item "$verb $rel ($(du -sh "$ROOT/$rel" 2>/dev/null | cut -f1))"
    discard "$ROOT/$rel"
done
[ "$found" -gt "$before" ] || say "  none"

# ── git ───────────────────────────────────────────────────────────────────────
say ""
say "git"
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    stale="$(git -C "$ROOT" worktree prune --dry-run -v 2>&1)"
    if [ -n "$stale" ]; then
        item "$verb worktree records whose folders are gone:"
        printf '%s\n' "$stale" | sed 's/^/    /'
        [ "$APPLY" -eq 1 ] && git -C "$ROOT" worktree prune
    fi
    if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
        gone="$(git -C "$ROOT" remote prune origin --dry-run 2>/dev/null | sed -n 's/^ \* \[would prune\] //p')"
        for ref in $gone; do item "$verb remote branch $ref (deleted on GitHub)"; done
        [ "$APPLY" -eq 1 ] && [ -n "$gone" ] && git -C "$ROOT" remote prune origin >/dev/null
    fi
    in_use="$(git -C "$ROOT" worktree list | sed -n '2,$p')"
    [ -z "$in_use" ] || { say "  Worktrees still in use (left alone):"; printf '%s\n' "$in_use" | sed 's/^/    /'; }
else
    say "  not a git checkout: skipped"
fi

# ── Images built only for tests ───────────────────────────────────────────────
if [ "$IMAGES" -eq 1 ] && docker_ok; then
    say ""
    say "Test images"
    before=$found
    for image in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(ostraka-pwsh-test|ostraka-wixl):'); do
        item "$verb image $image"
        [ "$APPLY" -eq 1 ] && docker rmi "$image" >/dev/null
    done
    [ "$found" -gt "$before" ] || say "  none"
fi

# ── Left for you to decide ────────────────────────────────────────────────────
say ""
for bundle in "$HOME"/Backups/ostraka-history-*.bundle; do
    [ -e "$bundle" ] || continue
    say "Kept: $bundle (the pre-publication history; it holds old secrets, so delete it yourself once you no longer need it)"
done

say ""
if [ "$found" -eq 0 ]; then
    say "Nothing to tidy."
elif [ "$APPLY" -eq 1 ]; then
    say "Done: $found item(s). Files are in the Bin."
else
    say "$found item(s). Run again with --apply to tidy them."
fi
