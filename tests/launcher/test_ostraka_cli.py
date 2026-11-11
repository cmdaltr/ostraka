"""The host `ostraka` command (./ostraka), driven with fake `docker`, `curl` and `uname`.

PATH holds only a directory of links to the tools the script needs, plus the fakes, so a real
Docker is never reached and "Docker isn't installed" can be tested on a machine that has it. The
script runs from a copy of the tree's files it reads, so the checkout's own .env is never touched.
Bash is /bin/bash (3.2 on macOS) unless LAUNCHER_BASH names another.

    backend/.venv/bin/pytest tests/launcher -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = (
    "sed tail head grep tr od openssl mv rm chmod cat dirname basename readlink sleep env sh mkdir"
).split()

FAKE_DOCKER = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG"
printf '%s %s\n' "${COMPOSE_PROJECT_NAME:-(unset)}" "$*" >> "$FAKE_LOG.project"
case "$1" in
    info)
        # FAKE_INFO_FAILS: fails that many times, then answers (an engine that's being started)
        if [ -n "${FAKE_INFO_FAILS:-}" ]; then
            n=$(cat "$FAKE_LOG.info" 2>/dev/null || echo 0)
            echo $((n + 1)) > "$FAKE_LOG.info"
            [ "$n" -lt "$FAKE_INFO_FAILS" ] && exit 1
            exit 0
        fi
        exit "${FAKE_DOCKER_INFO:-0}" ;;
    compose)
        shift
        case "$1" in
            version) exit "${FAKE_COMPOSE_VERSION:-0}" ;;
            up) echo "compose up output"; exit "${FAKE_UP_EXIT:-0}" ;;
            ps) [ -n "${FAKE_BACKEND_RUNNING:-}" ] && echo 0123abcd; exit 0 ;;
            exec)
                case "$*" in
                    *"cat > "*) cat > "$FAKE_LOG.copied"; exit 0 ;;
                    *" ostraka has-admin") echo "${FAKE_HAS_ADMIN:-false}"; exit 0 ;;
                esac
                [ -n "${FAKE_EXEC_OUT:-}" ] && printf '%s\n' "$FAKE_EXEC_OUT"
                [ -n "${FAKE_EXEC_ERR:-}" ] && printf '%s\n' "$FAKE_EXEC_ERR" >&2
                exit "${FAKE_EXEC_EXIT:-0}" ;;
        esac
        exit 0 ;;
esac
exit 0
"""

# Answers after FAKE_HEALTH_AFTER failed calls (0: at once); FAKE_HEALTH=down never answers
FAKE_CURL = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_LOG.curl"
[ "${FAKE_HEALTH:-up}" = down ] && exit 7
n=$(cat "$FAKE_LOG.count" 2>/dev/null || echo 0)
echo $((n + 1)) > "$FAKE_LOG.count"
[ "$n" -lt "${FAKE_HEALTH_AFTER:-0}" ] && exit 7
printf '{"status": "ok", "version": "%s"}' "${FAKE_RUNNING_VERSION:-0.1.0}"
"""

FAKE_UNAME = '#!/bin/sh\necho "${FAKE_UNAME:-Linux}"\n'


def _script(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Launcher:
    def __init__(self, tmp: Path, docker: bool = True) -> None:
        self.root = tmp / "tree"
        (self.root / "backend" / "ostraka").mkdir(parents=True)
        shutil.copy2(REPO / "ostraka", self.root / "ostraka")
        shutil.copy2(REPO / ".env.example", self.root / ".env.example")
        shutil.copy2(REPO / "backend/ostraka/__init__.py", self.root / "backend/ostraka/__init__.py")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        bash = os.environ.get("LAUNCHER_BASH") or "/bin/bash"
        (self.bin / "bash").symlink_to(bash)
        for tool in TOOLS:
            found = shutil.which(tool)
            assert found, f"{tool} is needed for these tests"
            (self.bin / tool).symlink_to(found)
        if docker:
            _script(self.bin / "docker", FAKE_DOCKER)
        _script(self.bin / "curl", FAKE_CURL)
        _script(self.bin / "uname", FAKE_UNAME)
        self.log = tmp / "docker.log"
        self.cwd = tmp / "elsewhere"  # anywhere but the tree: the script finds its own directory
        self.cwd.mkdir()

    def run(self, *args: str, stdin: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        environ = {"PATH": str(self.bin), "HOME": str(self.cwd), "FAKE_LOG": str(self.log)}
        environ.update(env)
        return subprocess.run(  # noqa: S603 - the script under test, with fakes on PATH
            [str(self.root / "ostraka"), *args],
            cwd=self.cwd,
            env=environ,
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            timeout=60,
        )

    def docker_calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def projects(self, command: str) -> list[str]:
        """COMPOSE_PROJECT_NAME as each `docker compose <command>` saw it in its environment."""
        log = self.log.parent / "docker.log.project"
        lines = log.read_text().splitlines() if log.exists() else []
        return [line.split(" ", 1)[0] for line in lines if line.endswith(f" compose {command}")]

    def unpack(self) -> None:
        """Make this tree look like one the app unpacked."""
        (self.root / ".ostraka-version").write_text("0.1.0\n")


@pytest.fixture
def launcher(tmp_path: Path) -> Launcher:
    return Launcher(tmp_path)


# ---- start --------------------------------------------------------------------------------------


def test_start_writes_env_with_new_secrets_once(launcher):
    first = launcher.run("start")
    assert first.returncode == 0, first.stderr
    env = (launcher.root / ".env").read_text()
    assert re.search(r"^POSTGRES_PASSWORD=[0-9a-f]{48}$", env, re.M)
    assert re.search(r"^OSTRAKA_SECRET_KEY=[0-9a-f]{64}$", env, re.M)
    assert "OSTRAKA_PUBLIC_URL=http://127.0.0.1:24747" in env
    assert stat.S_IMODE((launcher.root / ".env").stat().st_mode) == 0o600
    assert "compose up -d --build" in launcher.docker_calls()
    assert "running at http://127.0.0.1:24747" in first.stdout

    second = launcher.run("start")
    assert second.returncode == 0, second.stderr
    assert (launcher.root / ".env").read_text() == env  # never rewritten
    assert not list(launcher.root.glob(".env.tmp*"))


def test_start_never_overwrites_an_existing_env(launcher):
    (launcher.root / ".env").write_text("POSTGRES_PASSWORD=mine\nOSTRAKA_SECRET_KEY=mine-too\n")
    assert launcher.run("start").returncode == 0
    assert (launcher.root / ".env").read_text() == "POSTGRES_PASSWORD=mine\nOSTRAKA_SECRET_KEY=mine-too\n"


def test_start_progress_prints_only_progress_lines(launcher):
    out = launcher.run("start", "--progress", FAKE_HEALTH_AFTER="4")
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines, "no progress at all"
    numbers = []
    for line in lines:
        match = re.fullmatch(r"progress (\d{1,3}) (\S.*)", line)
        assert match, f"not a progress line: {line!r}"
        numbers.append(int(match.group(1)))
    assert numbers == sorted(numbers) and numbers[0] == 0 and numbers[-1] == 100
    assert all(0 <= n <= 100 for n in numbers)
    # What a person would read goes to stderr, compose's output included
    assert "compose up output" in out.stderr and "Wrote" in out.stderr


def test_start_waits_for_health_then_gives_up(launcher):
    out = launcher.run("start", FAKE_HEALTH="down", OSTRAKA_START_TIMEOUT="2")
    assert out.returncode == 1
    assert "didn't answer" in out.stderr
    assert "/api/health" in (launcher.log.parent / "docker.log.curl").read_text()


def test_start_fails_when_compose_does(launcher):
    out = launcher.run("start", FAKE_UP_EXIT="17")
    assert out.returncode == 1 and "exit 17" in out.stderr


def test_start_uses_ostraka_port(launcher):
    assert launcher.run("start", OSTRAKA_PORT="25000").returncode == 0
    assert "127.0.0.1:25000/api/health" in (launcher.log.parent / "docker.log.curl").read_text()
    assert "OSTRAKA_PUBLIC_URL=http://127.0.0.1:25000" in (launcher.root / ".env").read_text()


def test_start_rejects_unknown_options(launcher):
    assert launcher.run("start", "--nonsense").returncode == 2


# ---- Docker missing or not running --------------------------------------------------------------


def test_docker_missing_exits_4(tmp_path):
    bare = Launcher(tmp_path, docker=False)
    for args in (("start",), ("start", "--progress"), ("status",), ("stop",), ("licence", "show")):
        out = bare.run(*args)
        assert out.returncode == 4, args
        assert "Docker isn't installed" in (out.stderr + out.stdout)
    assert not (bare.root / ".env").exists()  # nothing is prepared without Docker


def test_docker_not_running_exits_4(launcher):
    out = launcher.run("start", FAKE_DOCKER_INFO="1")  # uname says Linux: nothing to wake
    assert out.returncode == 4 and "not running" in out.stderr
    assert launcher.run("status", FAKE_DOCKER_INFO="1").returncode == 4


def test_compose_plugin_missing_exits_4(launcher):
    out = launcher.run("start", FAKE_COMPOSE_VERSION="1")
    assert out.returncode == 4 and "compose plugin" in out.stderr


def test_compose_plugin_is_checked_after_waking_docker(launcher):
    # On a Mac, start wakes a stopped engine; one that comes up without the plugin is still exit 4
    out = launcher.run("start", FAKE_UNAME="Darwin", FAKE_INFO_FAILS="2", FAKE_COMPOSE_VERSION="1")
    assert out.returncode == 4, out.stderr
    assert "compose plugin" in out.stderr
    assert "compose up -d --build" not in launcher.docker_calls()


def test_start_wakes_docker_on_a_mac(launcher):
    out = launcher.run("start", FAKE_UNAME="Darwin", FAKE_INFO_FAILS="2")
    assert out.returncode == 0, out.stderr
    assert "compose up -d --build" in launcher.docker_calls()


def test_docker_that_does_not_wake_exits_4(launcher):
    out = launcher.run("start", FAKE_UNAME="Darwin", FAKE_DOCKER_INFO="1", OSTRAKA_DOCKER_WAIT="1")
    assert out.returncode == 4 and "not running" in out.stderr


# ---- the compose project name lives in .env -----------------------------------------------------


def test_an_unpacked_trees_new_env_names_its_project(launcher):
    launcher.unpack()
    assert launcher.run("start").returncode == 0
    assert (launcher.root / ".env").read_text().endswith("\nCOMPOSE_PROJECT_NAME=ostraka-app\n")
    # Compose reads it from .env, so plain `docker compose` and the uninstallers agree; nothing
    # is exported over it
    assert launcher.projects("up -d --build") == ["(unset)"]


def test_a_checkouts_env_is_left_alone(launcher):
    assert launcher.run("start").returncode == 0
    assert "COMPOSE_PROJECT_NAME" not in (launcher.root / ".env").read_text()
    assert launcher.projects("up -d --build") == ["(unset)"]
    (launcher.root / ".env").write_text("POSTGRES_PASSWORD=mine\n")
    assert launcher.run("stop").returncode == 0
    assert (launcher.root / ".env").read_text() == "POSTGRES_PASSWORD=mine\n"


def test_an_unpacked_trees_older_env_gets_the_project_name(launcher):
    launcher.unpack()
    (launcher.root / ".env").write_text("POSTGRES_PASSWORD=mine")  # no newline at the end
    assert launcher.run("stop").returncode == 0
    expected = "POSTGRES_PASSWORD=mine\nCOMPOSE_PROJECT_NAME=ostraka-app\n"
    assert (launcher.root / ".env").read_text() == expected
    assert launcher.projects("stop") == ["(unset)"]
    assert launcher.run("stop").returncode == 0
    assert (launcher.root / ".env").read_text().count("COMPOSE_PROJECT_NAME") == 1


def test_a_servers_own_project_name_is_honoured(launcher):
    launcher.unpack()
    (launcher.root / ".env").write_text("COMPOSE_PROJECT_NAME=ostraka\n")
    assert launcher.run("stop").returncode == 0
    assert (launcher.root / ".env").read_text() == "COMPOSE_PROJECT_NAME=ostraka\n"
    assert launcher.projects("stop") == ["(unset)"]  # not overridden from the environment


def test_the_project_name_is_exported_only_when_nothing_sets_it(launcher):
    launcher.unpack()
    assert launcher.run("stop").returncode == 0  # no .env to write it in
    assert not (launcher.root / ".env").exists()
    assert launcher.projects("stop") == ["ostraka-app"]
    assert launcher.run("stop", COMPOSE_PROJECT_NAME="mine").returncode == 0
    assert launcher.projects("stop")[-1] == "mine"


# ---- status -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "code", "running", "docker"),
    [
        ({}, 0, True, "ok"),
        ({"FAKE_HEALTH": "down"}, 3, False, "ok"),
        ({"FAKE_DOCKER_INFO": "1"}, 4, False, "stopped"),
    ],
)
def test_status_exit_codes_and_json(launcher, env, code, running, docker):
    assert launcher.run("status", **env).returncode == code
    out = launcher.run("status", "--json", **env)
    assert out.returncode == code
    report = json.loads(out.stdout)
    url = "http://127.0.0.1:24747"
    has_admin = False if running else None  # only asked of a running backend
    assert report == {
        "running": running,
        "url": url,
        "version": "0.1.0",
        "docker": docker,
        "has_admin": has_admin,
    }


def test_status_json_says_whether_there_is_an_admin(launcher):
    out = launcher.run("status", "--json", FAKE_HAS_ADMIN="true")
    assert json.loads(out.stdout)["has_admin"] is True
    assert "compose exec -T backend ostraka has-admin" in launcher.docker_calls()


def test_status_json_when_docker_is_missing(tmp_path):
    out = Launcher(tmp_path, docker=False).run("status", "--json")
    assert out.returncode == 4
    assert json.loads(out.stdout)["docker"] == "missing"


def test_status_reports_the_running_version(launcher):
    out = launcher.run("status", "--json", FAKE_RUNNING_VERSION="0.2.0")
    assert json.loads(out.stdout)["version"] == "0.2.0"


# ---- the small ones -----------------------------------------------------------------------------


def test_url_and_version(launcher):
    assert launcher.run("url").stdout == "http://127.0.0.1:24747\n"
    assert launcher.run("url", OSTRAKA_PORT="25001").stdout == "http://127.0.0.1:25001\n"
    (launcher.root / ".env").write_text("OSTRAKA_PORT=25002\n")
    assert launcher.run("url").stdout == "http://127.0.0.1:25002\n"
    assert launcher.run("version").stdout == "0.1.0\n"
    assert launcher.docker_calls() == []  # neither needs Docker


def test_stop_and_logs(launcher):
    assert launcher.run("stop").returncode == 0
    assert launcher.run("logs", "backend").returncode == 0
    calls = launcher.docker_calls()
    assert "compose stop" in calls and "compose logs -f backend" in calls


def test_help(launcher):
    out = launcher.run("help")
    assert out.returncode == 0 and "first-admin EMAIL" in out.stdout
    assert launcher.run().stdout == out.stdout


# ---- first-admin --------------------------------------------------------------------------------


def test_first_admin_prints_only_the_local_link(launcher):
    out = launcher.run(
        "first-admin",
        "first@example.org",
        FAKE_BACKEND_RUNNING="1",
        FAKE_EXEC_OUT="https://ostraka.example.org/invite/tok_123",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout == "http://127.0.0.1:24747/invite/tok_123\n"
    asked = "compose exec -T backend ostraka bootstrap-admin --link-only first@example.org"
    assert asked in launcher.docker_calls()


def test_first_admin_refuses_with_5_when_there_is_an_admin(launcher):
    out = launcher.run(
        "first-admin",
        "second@example.org",
        FAKE_BACKEND_RUNNING="1",
        FAKE_EXEC_EXIT="5",
        FAKE_EXEC_ERR="Ostraka already has an admin.",
    )
    assert out.returncode == 5 and out.stdout == ""
    assert "already has an admin" in out.stderr


def test_first_admin_other_failures_stay_1(launcher):
    out = launcher.run(
        "first-admin", "b@example.org", FAKE_BACKEND_RUNNING="1", FAKE_EXEC_EXIT="1", FAKE_EXEC_ERR="no seat"
    )
    assert out.returncode == 1 and out.stdout == ""


def test_first_admin_needs_an_email_and_a_running_stack(launcher):
    assert launcher.run("first-admin").returncode == 2
    assert launcher.run("first-admin", "a@example.org").returncode == 3


# ---- passthrough to the backend's CLI -----------------------------------------------------------


def test_unknown_commands_go_to_the_backend(launcher):
    out = launcher.run("licence", "show", FAKE_BACKEND_RUNNING="1", FAKE_EXEC_OUT="Edition: free")
    assert out.returncode == 0 and out.stdout == "Edition: free\n"
    # stdin isn't a terminal here, so no TTY is asked for
    assert "compose exec -T backend ostraka licence show" in launcher.docker_calls()


def test_passthrough_keeps_the_backends_exit_code(launcher):
    assert launcher.run("import", "--sample", FAKE_BACKEND_RUNNING="1", FAKE_EXEC_EXIT="5").returncode == 5


def test_passthrough_copies_host_files_into_the_container(launcher):
    licence = launcher.cwd / "northwind.licence"
    licence.write_text("signed-licence-text")
    out = launcher.run(
        "licence",
        "install",
        "northwind.licence",
        "--accept-terms",
        "Ben <ben@example.org>",
        FAKE_BACKEND_RUNNING="1",
    )
    assert out.returncode == 0, out.stderr
    calls = launcher.docker_calls()
    run = next(c for c in calls if " ostraka licence install " in c)
    copy = r"/tmp/ostraka-host-\d+-1-northwind\.licence"  # noqa: S108 - a path inside the container
    assert re.search(rf"licence install {copy} --accept-terms Ben <ben@example\.org>$", run)
    assert (launcher.log.parent / "docker.log.copied").read_text() == "signed-licence-text"
    assert any(c.startswith("compose exec -T backend rm -f /tmp/ostraka-host-") for c in calls)


def test_passthrough_says_when_ostraka_is_not_running(launcher):
    out = launcher.run("licence", "show")
    assert out.returncode == 3 and "isn't running" in out.stderr


# ---- server init and app, which other parts provide ---------------------------------------------


def test_server_init_dispatches_to_the_setup_script(launcher):
    missing = launcher.run("server", "init")
    assert missing.returncode == 1 and "deploy/linux/install.sh is missing" in missing.stderr
    setup = launcher.root / "deploy/linux/install.sh"
    setup.parent.mkdir(parents=True)
    setup.write_text('echo "setup $*"\n')
    out = launcher.run("server", "init", "--hostname", "ir.example.org")
    assert out.returncode == 0 and out.stdout == "setup --hostname ir.example.org\n"
    assert launcher.run("server", "init", FAKE_UNAME="Darwin").returncode == 1
    assert launcher.run("server").returncode == 2


@pytest.mark.parametrize(
    ("uname", "script", "install", "uninstall"),
    [
        ("Darwin", "desktop/macos/build.sh", "--install", "--uninstall"),
        ("Linux", "desktop/linux/install.sh", "", "--uninstall"),
    ],
)
def test_app_dispatches_per_platform(launcher, uname, script, install, uninstall):
    missing = launcher.run("app", "install", FAKE_UNAME=uname)
    assert missing.returncode == 1 and f"{script} is missing" in missing.stderr
    path = launcher.root / script
    path.parent.mkdir(parents=True)
    path.write_text('echo "app $*"\n')
    assert launcher.run("app", "install", FAKE_UNAME=uname).stdout.rstrip() == f"app {install}".rstrip()
    assert launcher.run("app", "uninstall", FAKE_UNAME=uname).stdout == f"app {uninstall}\n"
    assert launcher.run("app", FAKE_UNAME=uname).returncode == 2


def test_app_on_windows_points_at_powershell(launcher):
    out = launcher.run("app", "install", FAKE_UNAME="MINGW64_NT-10.0")
    assert out.returncode == 1 and "install.ps1" in out.stderr


def test_runs_through_a_symlink(launcher, tmp_path):
    link = tmp_path / "bin-link"
    link.mkdir()
    (link / "ostraka").symlink_to(launcher.root / "ostraka")
    out = subprocess.run(  # noqa: S603 - the script under test, through a link
        [str(link / "ostraka"), "version"],
        env={"PATH": str(launcher.bin)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.stdout == "0.1.0\n"
