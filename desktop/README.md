# Ostraka everywhere: the CLI, desktop apps and packages

Phase 3b (docs/ROADMAP.md). Ostraka is a Docker Compose stack (db, redis, backend, web) with the web
UI on `http://127.0.0.1:24747`. The desktop apps are thin native launchers around that stack, on
Rowanholt's pattern (`../rowanholt/app/`), for the platforms Shikari covers, and named as Rowanholt's
releases are. A tagged release builds on GitHub Actions, as Rowanholt's does; `make release` builds
the same files locally ([How releases are built](#how-releases-are-built)).

| File | For | Window |
|---|---|---|
| `Ostraka-<version>-macos-universal.dmg` | macOS 12+, Apple silicon and Intel | Native WebKit window (Swift), a universal binary |
| `Ostraka-<version>-windows-universal.msi` | Windows 10 and 11, x64 and ARM64 (WiX source, built with `wixl` from msitools) | Edge in app mode, from a PowerShell launcher |
| `ostraka_<version>-1_all.deb` | Debian 12+, Ubuntu 22.04+, any architecture | Chromium, Chrome, Brave or Edge in app mode |
| `ostraka-<version>-source.tar.gz` | Team servers (`deploy/`), and any other Linux (Rocky, RHEL, Alma, Fedora): `install.sh` unpacks it into `~/Ostraka` and adds the launcher to the user's menu, with no package | The same |
| `SHA256SUMS` | Checksums of the above, which the install scripts check | |

One package per platform runs on every architecture, as Rowanholt's do: nothing is compiled for a CPU
but the Mac app's own universal binary, and Docker builds the stack's images for the machine on first
start. A pre-release keeps its hyphen in every filename (`Ostraka-1.0.0-rc1-macos-universal.dmg`,
`ostraka_1.0.0-rc1-1_all.deb`; GitHub renames a `~` in an asset), while inside the `.deb` says
`1.0.0~rc1-1`, so apt sorts it before `1.0.0-1`, and the `.msi` says `1.0.0`, the numbers Windows Installer
takes, upgrading at the same version so the release replaces its pre-release. A manual run of the
workflow builds `n.n.n-dev.N` (N is the run number): the `.deb` says `n.n.n~dev.N-1` and the `.msi`
`n.n.n.N`, a fourth field Windows Installer shows but doesn't compare.

## How releases are built

`.github/workflows/desktop-release.yml` mirrors Rowanholt's, job for job. Pushing a tag `v<version>`
(the version in `backend/ostraka/__init__.py`, or the run stops) builds every file in the table above,
installs and uninstalls each with `install.sh`, `install.ps1`, `uninstall.sh` and `uninstall.ps1` on a
runner of its own OS, and attaches them with SHA256SUMS to a draft release titled "Ostraka <version>",
to check and publish by hand. A manual run (Actions → Desktop app → Run workflow) builds and tests the
same files as `<version>-dev.<run>`, with no release. Jobs pass files to each other through
`actions/cache` (10 GB per repository), not workflow artifacts, whose 500 MB is shared by every
repository on the account.

| Job | Runner | Does |
|---|---|---|
| `source` | ubuntu-latest | Sets the version, builds `ostraka-<v>-source.tar.gz` with `source-tarball.sh` |
| `macos` | macos-15 | `macos/package.sh`, then installs the `.dmg` with `install.sh --file`: a universal binary, bundle id `com.cmdaltr.ostraka`, the licences inside, `codesign --verify`; then `uninstall.sh` and nothing left behind |
| `linux` | ubuntu-22.04 (the oldest supported) | `linux/package-deb.sh`, then the same with the `.deb`: the files in `/usr/lib/ostraka`, the menu entry (`desktop-file-validate`), the copyright file; then `uninstall.sh`, which purges it |
| `msi` | ubuntu-latest | `windows/package-msi.sh` (wixl in a Debian container needs a Linux runner's Docker) |
| `windows` | windows-latest | Signs the MSI when it can, installs it with `install.ps1 -File` from Windows PowerShell: the files, the Start menu shortcuts, the Windows Installer registration by UpgradeCode; then `uninstall.ps1` and nothing left behind |
| `release` | ubuntu-latest | A tag only: SHA256SUMS, and the draft release |

The install tests check what each package puts down and takes away. They don't start the stack (the
macOS runners have no Docker, and Windows' runs Windows containers only) or open the app's window.

Releases ship unsigned until Ostraka earns enough to pay for certificates (Ben, 11 November 2026): on
first launch macOS asks the user to choose Open Anyway in Privacy & Security, and Windows SmartScreen
asks them to choose More info, then Run anyway. When that changes, signing switches on with repository
secrets, the same names as Rowanholt's:

| Secret | For |
|---|---|
| `MACOS_CERTIFICATE_P12` | A Developer ID Application certificate and key, exported as `.p12` and base64-encoded |
| `MACOS_CERTIFICATE_PASSWORD` | The `.p12`'s password |
| `MACOS_SIGN_IDENTITY` | `Developer ID Application: Name (TEAMID)` |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | Notarisation (an app-specific password). The workflow passes them to `package.sh`, which uses them when there's no `MACOS_NOTARY_PROFILE` |
| `WINDOWS_CERTIFICATE_PFX` | A code-signing certificate as `.pfx`, base64-encoded; `signtool` signs the MSI with it. Certificates issued since June 2023 keep their key on hardware or a cloud HSM and can't be exported as a `.pfx`, so Windows signing will mean replacing this step with Azure Trusted Signing or a CA's cloud signing service |
| `WINDOWS_CERTIFICATE_PASSWORD` | The `.pfx`'s password |

Without them the `.dmg` is ad-hoc signed and the `.msi` unsigned, as a local build is.

`make release VERSION=…` stays as the local build, for a release from one Mac with Docker, or to try the
packagers before tagging (below).

## The contract every piece follows

This section is what the launchers, packages and scripts agree on. Change it here first.

### The host `ostraka` command

`./ostraka` (Bash, repo root) and `desktop/windows/ostraka-cli.ps1` (PowerShell; not `ostraka.ps1`, which is
the same file as the launcher `Ostraka.ps1` on a case-insensitive disk) drive the stack from the host. They
run from an Ostraka source tree: the repo checkout, or the copy a package unpacks into `OSTRAKA_HOME`.

| Command | Does |
|---|---|
| `ostraka start [--progress]` | Writes `.env` with random secrets if there isn't one; `docker compose up -d --build`; waits until `GET /api/health` answers `{"status": "ok"}`. `--progress` prints machine-readable lines, `progress <0-100> <message>`, for the launchers. Exit 0 when up |
| `ostraka stop` | `docker compose stop` (data stays) |
| `ostraka status [--json]` | Exit 0 running, 3 stopped, 4 Docker missing or not running. `--json`: `{"running": bool, "url": str, "version": str, "docker": "ok"|"missing"|"stopped", "has_admin": bool|null}` (`null` while the stack is down) |
| `ostraka url` | Prints the local URL, `http://127.0.0.1:24747` (or `OSTRAKA_PORT`) |
| `ostraka first-admin EMAIL` | On a fresh install with no admin: creates the first admin's invitation and prints only the link (`http://127.0.0.1:24747/invite/<token>`), for the launcher to open. Exit 5 when there's an admin already (the backend's `bootstrap-admin` exit, passed on); 1 for any other failure |
| `ostraka logs [service]` | `docker compose logs -f` |
| `ostraka server init [options]` | Central-server setup (see `deploy/`): `deploy/linux/install.sh` on Linux, `deploy/windows/install-service.ps1` (WSL 2) from `ostraka-cli.ps1`, with the options passed on |
| `ostraka app install` / `app uninstall` | A desktop entry for a checkout (macOS app built with `swiftc`, Linux menu entry, Windows shortcuts) |
| `ostraka version` | The version (`backend/ostraka/__init__.py`) |
| anything else | Passed to the backend's own CLI: `docker compose exec backend ostraka "$@"` (`licence install`, `bootstrap-admin`, `import`) |

`start` writes `OSTRAKA_PUBLIC_URL=http://127.0.0.1:<port>` into a new `.env`, so a local install's links
point at itself. `OSTRAKA_START_TIMEOUT` (180 s) and `OSTRAKA_DOCKER_WAIT` (120 s) bound the waits.

The compose project name lives in `.env`, so the CLIs, plain `docker compose` and the uninstallers all see
the same project. In an unpacked tree (it has `.ostraka-version`) a new `.env` gets
`COMPOSE_PROJECT_NAME=ostraka-app`, and an older one without the line has it added. A name `.env` already
has is kept (a server's setup writes `ostraka`), and a checkout's `.env` is left alone. The CLIs export
`COMPOSE_PROJECT_NAME` only when neither the environment nor `.env` sets it, as the environment beats
`.env` in compose. The uninstallers read the project from `.env`, then `ostraka-app` when there's
`.ostraka-version`, then the folder's name (a checkout), ignoring the shell's own `COMPOSE_PROJECT_NAME`;
`--data` / `-Data` deletes only a folder with `.ostraka-version` and refuses (exit 1) any other folder that
isn't a git checkout.

### Where things live

| | macOS | Linux | Windows |
|---|---|---|---|
| Source tree (`OSTRAKA_HOME`) | `~/Ostraka` | `~/Ostraka` | `%USERPROFILE%\Ostraka` |
| Version marker of an unpacked tree | `OSTRAKA_HOME/.ostraka-version` | same | same |
| Saved server (a URL, or `local`) | defaults `com.cmdaltr.ostraka` `OstrakaServer` | `~/.config/ostraka/server` | `%APPDATA%\Ostraka\server` |
| IT-set server (locks it, skips the first-run screen) | MDM profile, domain `com.cmdaltr.ostraka`, key `ServerURL` | `/etc/ostraka/client.json` `{"server_url": "..."}` | `HKLM` or `HKCU\SOFTWARE\Policies\cmdaltr\Ostraka` `ServerURL`; the per-user MSI's `SERVERURL` writes `HKCU\SOFTWARE\cmdaltr\Ostraka` `ServerURL` (a standard user can't write under `Policies`), also locked |
| Override (not locked) | `OSTRAKA_SERVER` (a URL, or `local`) | same | same |
| Bundle id / app id | `com.cmdaltr.ostraka`; a checkout build `com.cmdaltr.ostraka.local` | `ostraka.desktop` | MSI UpgradeCode fixed per product, `{7A1F0C3E-5B2D-4E8A-9C61-0D3F8E2B4A17}` |

A package carries the source tree for its version (`desktop/source-tarball.sh`, `git archive HEAD`, under one
top folder `ostraka-<version>/`). Every packager takes `--version V --source TARBALL OUTDIR`, and
`scripts/release.sh` (`make release VERSION=…`) runs each once, building the files above into `dist/<version>/` with the source tarball and SHA256SUMS. A version is `n.n.n` or `n.n.n-pre`
(`1.0.0-rc1`, a hyphen, as Shikari's tags); `release.sh` refuses anything else, the `-dev.N` of a manual
workflow run among it. Locally, a signed `.dmg` is notarised through a notarytool keychain profile,
named by `MACOS_NOTARY_PROFILE` and saved once with `xcrun notarytool store-credentials` (no password on a
command line); the workflow notarises with the Apple ID secrets instead. On a Linux without apt,
`install.sh` installs the source tarball the way the launcher's first run unpacks one (below) and runs
that tree's `desktop/linux/install.sh` for the menu entry; `uninstall.sh` removes that entry with
`desktop/linux/install.sh --uninstall`. An unpacked tree runs as compose project `ostraka-app`, so it never shares
volumes with a checkout. On
first local run the launcher unpacks it into `OSTRAKA_HOME` and writes `.ostraka-version`; a newer app
offers to update the files in place (`.env` and the data volumes stay). A git checkout is never
overwritten.

### First launch

The app asks: **Connect to your team's Ostraka** (a server address; nothing stored or run locally) or
**Run Ostraka on this computer** (the stack, on 127.0.0.1 only). A server is checked before it's opened:
`GET /api/meta` must answer with `"name": "Ostraka"`, over HTTPS with a certificate the OS trusts
(plain `http://` only for this computer, or when IT or `OSTRAKA_SERVER` sets it). Local mode needs
Docker: OrbStack or Docker Desktop on macOS, Docker Desktop on Windows, Docker Engine with the compose
plugin on Linux. Docker is never installed silently (the `.deb` only suggests it, so `apt` doesn't
pull it in, and `install.sh` only names what the tarball route still needs); the app says what's missing and links its download page. On a fresh local install the app asks for the first admin's email, runs `ostraka
first-admin EMAIL` and opens the link: password and TOTP, as on any server.

### Menus every window has

- **Help → Licence**: opens `<server>/help/licence` (public: it works before sign-in).
- **Help → About Ostraka**: the version, ELv2, copyright cmdaltr.
- **Change server** (unless IT set it), **Stop Ostraka** (local mode only).

### Branding

The name is written **Ostraka**; `ostraka` in paths, commands and ids. Icons come from
`docs/brand/ostrakon-clay.png` (generated into `desktop/icons/`). Colours and fonts as `docs/BRAND.md`.

### Licence notice in packages

Every package carries `LICENSE` and `COMMERCIAL-LICENSE.md`: the `.deb` as its copyright
file (`/usr/share/doc/ostraka/copyright`), the `.dmg` and `.msi` inside the app, the source tarball at its top. Installers don't ask:
using Ostraka is accepting the licence (README, Licence).
