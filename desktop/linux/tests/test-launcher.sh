#!/usr/bin/env bash
# The launcher's logic that needs no desktop: addresses, the server check, which setting wins,
# unpacking, updates, the first admin and the hand-off page. curl, zenity, chromium and the tree's
# ostraka are stubs on PATH. Run on Linux (or in a
# container): bash desktop/linux/tests/test-launcher.sh
set -uo pipefail

HERE="$(cd -P "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export HOME="$WORK/home" XDG_CONFIG_HOME="$WORK/home/.config" XDG_STATE_HOME="$WORK/home/.state"
mkdir -p "$HOME" "$WORK/bin"
unset OSTRAKA_SERVER OSTRAKA_HOME

# curl stub: answers /api/meta with $FAKE_BODY and exits $FAKE_STATUS; its arguments go to
# $CURL_ARGS, one a line
export CURL_ARGS="$WORK/curl-args"
cat > "$WORK/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$CURL_ARGS"
[ "${FAKE_STATUS:-0}" -eq 0 ] || { echo "curl: (${FAKE_STATUS}) fake failure" >&2; exit "$FAKE_STATUS"; }
printf '%s' "${FAKE_BODY:-}"
STUB
chmod +x "$WORK/bin/curl"
export PATH="$WORK/bin:$PATH"

# shellcheck disable=SC1091 # sourced for its functions; main() only runs when executed
source "$HERE/../ostraka-app.sh"
CLIENT_JSON="$WORK/client.json"
mkdir -p "$CONFIG" "$STATE"

failures=0
check() {
    # check <description> <expected> <actual>
    if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected '$2', got '$3'"; failures=$((failures + 1)); fi
}

# Addresses
check "bare host gets https" "https://ostraka.example.org" "$(normalise_server ostraka.example.org)"
check "port and path" "https://ir.example.org:8443" "$(normalise_server ' https://ir.example.org:8443/incidents/OST-1 ')"
check "scheme case" "https://ir.example.org" "$(normalise_server HTTPS://ir.example.org/)"
check "http refused when typed" "" "$(normalise_server http://ir.example.org)"
check "http for this computer" "http://127.0.0.1:24747" "$(normalise_server http://127.0.0.1:24747)"
check "http when IT or the override sets it" "http://lab.internal" "$(normalise_server http://lab.internal yes)"
check "not an address" "" "$(normalise_server 'not a host')"
check "other schemes" "" "$(normalise_server ftp://ir.example.org)"
check "empty" "" "$(normalise_server '')"
check "no injection in the host" "" "$(normalise_server 'https://ir.example.org;rm -rf ~')"

# The server check
check "Ostraka answers" 0 \
    "$(FAKE_BODY='{"version":"0.1.0","name":"Ostraka","tagline":"x"}' check_server https://a >/dev/null; echo $?)"
check "spaced JSON" 0 "$(FAKE_BODY='{"name": "Ostraka"}' check_server https://a >/dev/null; echo $?)"
check "something else answers" 1 "$(FAKE_BODY='{"name":"Rowanholt"}' check_server https://a >/dev/null; echo $?)"
check "an HTML page answers" 1 "$(FAKE_BODY='<html>Ostraka</html>' check_server https://a >/dev/null; echo $?)"
why="$(FAKE_STATUS=60 check_server https://a)"
check "untrusted certificate says so" yes "$([[ "$why" == *"certificate this computer trusts"* ]] && echo yes)"
why="$(FAKE_STATUS=6 check_server https://a)"
check "unknown host says so" yes "$([[ "$why" == *"check the address"* ]] && echo yes)"
# -q only works as curl's first argument; it keeps a ~/.curlrc (insecure, say) out of the check
printf 'insecure\n' > "$HOME/.curlrc"
FAKE_BODY='{"name":"Ostraka"}' check_server https://a >/dev/null
check "server check ignores ~/.curlrc" -q "$(head -n1 "$CURL_ARGS")"
check "server check never -k" "" "$(grep -xE -- '-k|--insecure' "$CURL_ARGS")"
FAKE_BODY='{"status":"ok"}' healthy http://127.0.0.1:24747 >/dev/null
check "health check ignores ~/.curlrc" -q "$(head -n1 "$CURL_ARGS")"
rm -f "$HOME/.curlrc"

# Which setting wins: IT, then OSTRAKA_SERVER, then the saved one
check "nothing set is first run" none "$(effective_server)"
echo https://saved.example.org > "$CONFIG/server"
check "saved" "saved https://saved.example.org" "$(effective_server)"
check "override beats saved" "env https://env.example.org" "$(OSTRAKA_SERVER=https://env.example.org effective_server)"
check "override can be local" "env local" "$(OSTRAKA_SERVER=local effective_server)"
printf '{\n  "server_url": "https://it.example.org"\n}\n' > "$CLIENT_JSON"
check "IT beats both" "it https://it.example.org" "$(OSTRAKA_SERVER=https://env.example.org effective_server)"
check "IT locks" 0 "$(is_locked; echo $?)"
check "current server" "https://it.example.org" "$(current_server)"
printf '{"other": 1, "server_url":"https://it2.example.org"}' > "$CLIENT_JSON"
check "client.json on one line" "it https://it2.example.org" "$(effective_server)"
printf '{"server_url": ""}' > "$CLIENT_JSON"
check "empty server_url is no lock" 1 "$(is_locked; echo $?)"
rm -f "$CLIENT_JSON"
echo local > "$CONFIG/server"
check "saved local" "saved local" "$(effective_server)"
check "no current server in local mode" "" "$(current_server)"

# Versions for the in-place update
check "newer" 0 "$(version_newer 0.2.0 0.1.0; echo $?)"
check "same" 1 "$(version_newer 0.1.0 0.1.0; echo $?)"
check "older" 1 "$(version_newer 0.1.0 0.10.0; echo $?)"

# Unpacking, with and without git archive's --prefix
mkdir -p "$WORK/src"
printf 'services: {}\n' > "$WORK/src/docker-compose.yml"
printf '#!/bin/sh\n' > "$WORK/src/ostraka"; chmod +x "$WORK/src/ostraka"
echo 0.3.0 > "$WORK/VERSION"
HERE_SAVED="$HERE"; HERE="$WORK"
BUNDLED="$WORK/flat.tar.gz"; tar -czf "$BUNDLED" -C "$WORK/src" .
unpack_to "$WORK/t1"
check "flat tarball unpacks" yes "$(is_tree "$WORK/t1" && echo yes)"
check "version marker" 0.3.0 "$(cat "$WORK/t1/.ostraka-version")"
BUNDLED="$WORK/prefixed.tar.gz"; tar -czf "$BUNDLED" -C "$WORK" src
unpack_to "$WORK/t2"
check "prefixed tarball unpacks" yes "$(is_tree "$WORK/t2" && echo yes)"
check "no staging folder left" "" "$(find "$WORK/t2" -maxdepth 1 -name '.ostraka-unpack.*')"

# An update mirrors the new tree: what the new version doesn't carry goes, .env stays, and
# nothing outside the copy is touched
mkdir -p "$WORK/t1/backend/migrations/versions" "$WORK/outside"
echo stale > "$WORK/t1/backend/migrations/versions/0099_removed.py"
echo 'OSTRAKA_SECRET_KEY=keep' > "$WORK/t1/.env"
echo precious > "$WORK/outside/file"
ln -s "$WORK/outside" "$WORK/t1/link-out"
mkdir -p "$WORK/t1/.ostraka-unpack.old"   # left by an update cut short
echo 0.4.0 > "$WORK/VERSION"
BUNDLED="$WORK/flat.tar.gz"
unpack_to "$WORK/t1"
check "update removes a file the new tree dropped" no \
    "$([ -e "$WORK/t1/backend/migrations/versions/0099_removed.py" ] && echo yes || echo no)"
check "update keeps .env" "OSTRAKA_SECRET_KEY=keep" "$(cat "$WORK/t1/.env")"
check "update writes the new version" 0.4.0 "$(cat "$WORK/t1/.ostraka-version")"
check "update leaves a working tree" yes "$(is_tree "$WORK/t1" && echo yes)"
check "a symlink's target survives" precious "$(cat "$WORK/outside/file" 2>/dev/null)"
check "old staging folders go" "" "$(find "$WORK/t1" -maxdepth 1 -name '.ostraka-unpack.*')"
# A bad tarball leaves the old files as they were
echo junk > "$WORK/t1/marker-file"
printf 'not a tarball' > "$WORK/bad.tar.gz"; BUNDLED="$WORK/bad.tar.gz"
unpack_to "$WORK/t1" 2>/dev/null
check "a bad tarball fails" 1 "$(unpack_to "$WORK/t1" >/dev/null 2>&1; echo $?)"
check "a bad tarball clears nothing" junk "$(cat "$WORK/t1/marker-file" 2>/dev/null)"
BUNDLED="$WORK/flat.tar.gz"
# Clearing needs the version marker, refuses a checkout, and stays inside the folder
mkdir -p "$WORK/plain"; echo mine > "$WORK/plain/notes"
check "no marker, nothing cleared" 1 "$(clear_tree "$WORK/plain"; echo $?)"
check "no marker, file kept" mine "$(cat "$WORK/plain/notes")"
mkdir -p "$WORK/checkout/.git"; echo x > "$WORK/checkout/.ostraka-version"; echo mine > "$WORK/checkout/notes"
check "a checkout isn't cleared" 1 "$(clear_tree "$WORK/checkout"; echo $?)"
check "a checkout's file kept" mine "$(cat "$WORK/checkout/notes")"
check "the empty path isn't cleared" 1 "$(clear_tree ""; echo $?)"
# A copy whose update was cut short (marker and .env, no tree) is unpacked again, not refused
mkdir -p "$WORK/broken"; echo 0.3.0 > "$WORK/broken/.ostraka-version"; echo 'A=1' > "$WORK/broken/.env"
check "a cut-short copy is repaired" "$WORK/broken" "$(HOME_TREE="$WORK/broken" local_tree 2>/dev/null)"
check "repair keeps .env" "A=1" "$(cat "$WORK/broken/.env")"
HERE="$HERE_SAVED"

# has_admin from `ostraka status --json`
mkdir -p "$WORK/st"; printf 'services: {}\n' > "$WORK/st/docker-compose.yml"
for answer in true false missing; do
    if [ "$answer" = missing ]; then body='{"running": true, "docker": "ok"}'
    else body="{\"running\": true, \"has_admin\": $answer, \"docker\": \"ok\"}"; fi
    printf '#!/bin/sh\necho %s\n' "'$body'" > "$WORK/st/ostraka"; chmod +x "$WORK/st/ostraka"
    expected="$answer"; [ "$answer" = missing ] && expected=""
    check "has_admin $answer" "$expected" "$(has_admin "$WORK/st")"
done

# The first admin: only 0 (invited) and 5 (there's an admin already) settle it
cat > "$WORK/st/ostraka" <<'STUB'
#!/bin/sh
case "$1" in
    status) echo "${FAKE_STATUS_JSON:-}" ;;
    first-admin) [ "${FAKE_FA:-0}" -eq 0 ] && echo "http://127.0.0.1:24747/invite/tok3n_ABC-123"
                 echo "something went wrong" >&2; exit "${FAKE_FA:-0}" ;;
esac
STUB
chmod +x "$WORK/st/ostraka"
mkdir -p "$WORK/zbin"
cat > "$WORK/zbin/zenity" <<'STUB'
#!/bin/sh
case " $* " in *" --entry "*) echo "${FAKE_EMAIL:-admin@example.org}" ;; esac
exit 0
STUB
chmod +x "$WORK/zbin/zenity"
PATH_SAVED="$PATH"; PATH="$WORK/zbin:$PATH"; hash -r
nostatus='{"running": true, "docker": "ok"}'
for case in "0 false link removed" "5 false none removed" "1 false none kept" "3 null none kept" \
    "5 null none removed" "1 null none kept"; do
    read -r code known want pending <<< "$case"
    touch "$ADMIN_PENDING"
    if [ "$known" = null ]; then json="$nostatus"; else json="{\"running\": true, \"has_admin\": $known}"; fi
    got="$(FAKE_FA="$code" FAKE_STATUS_JSON="$json" first_admin "$WORK/st")"
    if [ -n "$got" ]; then got="link"; else got="none"; fi
    check "first-admin exit $code (has_admin $known) opens" "$want" "$got"
    check "first-admin exit $code (has_admin $known) marker" "$pending" "$([ -f "$ADMIN_PENDING" ] && echo kept || echo removed)"
done
rm -f "$ADMIN_PENDING"
check "has_admin null and no marker: not asked" 1 \
    "$(FAKE_STATUS_JSON="$nostatus" first_admin "$WORK/st" >/dev/null; echo $?)"
check "has_admin true clears the marker" removed \
    "$(touch "$ADMIN_PENDING"; FAKE_STATUS_JSON='{"has_admin": true}' first_admin "$WORK/st" >/dev/null
       [ -f "$ADMIN_PENDING" ] && echo kept || echo removed)"
PATH="$PATH_SAVED"; hash -r

# The invitation link reaches the browser through a hand-off page, never its command line
export BROWSER_ARGS="$WORK/browser-args"
cat > "$WORK/bin/chromium" <<'STUB'
#!/bin/sh
printf '%s\n' "$@" > "$BROWSER_ARGS"
STUB
chmod +x "$WORK/bin/chromium"; hash -r
link="http://127.0.0.1:24747/invite/tok3n_ABC-123"
# shellcheck disable=SC2034 # read by handoff_page, sourced above
HANDOFF_TTL=2
( open_window "$link" handoff )
app_arg="$(grep -- '^--app=' "$BROWSER_ARGS")"
page="${app_arg#--app=file://}"
check "the token isn't on the browser's command line" "" "$(grep -F tok3n "$BROWSER_ARGS")"
check "the window opens a local page" yes "$([[ "$app_arg" == --app=file://* ]] && echo yes)"
check "the page sends it on to the link" yes "$(grep -qF "location.replace(\"$link\")" "$page" && echo yes)"
check "the page is the owner's alone" 600 "$(stat -c %a "$page")"
check "its folder is the owner's alone" 700 "$(stat -c %a "${page%/*}")"
check "the page lives beside the browser profile" "${PROFILE%/*}" "$(dirname "${page%/*}")"
sleep 3
check "the page is deleted afterwards" gone "$([ -e "${page%/*}" ] && echo there || echo gone)"
( open_window "$LOCAL_URL_DEFAULT" )
check "an ordinary URL opens directly" "--app=$LOCAL_URL_DEFAULT" "$(grep -- '^--app=' "$BROWSER_ARGS")"
check "a link that could break out of the page is refused" 1 \
    "$(handoff_page 'http://127.0.0.1/invite/a"><script>' "$WORK/hp" >/dev/null; echo $?)"
check "nothing written for it" "" "$(ls -A "$WORK/hp" 2>/dev/null)"
rm -f "$WORK/bin/chromium"; hash -r

# start --progress lines as zenity input
check "progress feed" "$(printf '10\n# Building images\n99\n# Waiting\n100')" \
    "$(printf 'progress 10 Building images\nsome build output\nprogress 100 Waiting\n' | progress_feed)"

echo
if [ "$failures" -ne 0 ]; then echo "$failures failed."; exit 1; fi
echo "All passed."
