# Ostraka roadmap

Agreed with Ben on 10 November 2026. This replaces the original scaffold. The rebuild keeps `ioc_detector.py`, the useful data shapes (finding types, occurrence vs input time, patient zero, system connections). The product was renamed from ciphr to Ostraka on 10 November 2026 (see Name).

## Position in the suite

| Tool | Owns | Ostraka's relationship |
|---|---|---|
| Rowanholt | Evidence and analysis: parsed artefacts, row-cited findings, Aulë | Link an incident to a Rowanholt job; pull confirmed findings with their `job/host/artefact/row_hash` citation |
| Shikari | Detection and hunting | Each confirmed technique without a detection becomes a follow-up task; Shikari's `run.completed` webhook closes it |
| Ostraka | The incident: people, work, decisions, record | Everything below |

Out of scope: SOAR playbook engine, alert ingestion and triage, artefact parsing, chat replacement, on-call paging.

## Decisions

| Area | Decision |
|---|---|
| Name | **Ostraka** (renamed from ciphr, 10 November 2026: CIPHR Ltd holds UK marks for HR software). Ancient Greek *ostraka*, the potsherds used as everyday records in Egypt and as ballots in Athens: the record, the ledger and the collective decision. The letters: Operational Suite for Teams to Respond, Assess, Keep records & take Action. References OST-YYYY-NNN, API tokens `ost_…`, settings `OSTRAKA_*`, the browser header `X-Ostraka`. The brand is in `docs/BRAND.md` |
| Codebase | Rebuild in this repo; no existing data to migrate |
| Backend | FastAPI, Postgres 16, Alembic only (no `create_all`), Redis pub/sub for live updates, a lightweight scheduler for nudges (Celery removed) |
| Frontend | React, Vite, TypeScript SPA |
| Accounts | Real team; invite-only local accounts with TOTP MFA and backup tokens; Tailscale identity only pre-fills the login |
| Roles | Admin, Team lead, Responder, Observer (read-only briefing access) |
| Invitations | Admins invite anyone into any team; team leads invite responders and observers, only into their own team. Links work once and last 7 days. |
| MFA | Mandatory for every account (TOTP and 10 backup codes), set up while accepting the invitation; a password alone never opens a session |
| First admin | No default account: `ostraka bootstrap-admin EMAIL` on the server prints a one-time link |
| Incident visibility | Members of the teams involved, plus people added individually; admins see every incident |
| Declaring incidents | Responders, team leads and admins; whoever declares becomes Incident Commander until they hand over |
| Cross-incident IOC overlap | When the other incident is one you can't see, Ostraka says the observable is seen elsewhere and which team's lead to ask, and nothing more |
| Where Ostraka runs | The web (a central server, as on the homelab) and desktop apps for macOS, Windows and Linux. On first run the app asks: connect to a team's Ostraka (a window onto that server, nothing stored locally), or run Ostraka on this computer (the same stack, bound to 127.0.0.1). No sync between the two. Follows Rowanholt's pattern: thin native launchers around the compose stack, Postgres and Redis in both modes |
| Local-mode sign-in | Same as the server: password and TOTP for everyone. `ostraka start` bootstraps the first admin on a fresh local install |
| Scripts | Bash and PowerShell installers and uninstallers (download a release, check it against SHA256SUMS, install; run as files, never piped into a shell); a responder client (declare, push IOCs or host details, comment, list tasks, using a `ost_` token); central-server setup for Linux and for Windows (WSL 2) |
| Release builds | On GitHub Actions, as Rowanholt's (Ben, 11 November 2026, replacing "not yet"): a `v*` tag builds the `.dmg`, `.msi`, `.deb` and source tarball, installs and uninstalls each package on its own OS, and attaches them with SHA256SUMS to a draft release (`.github/workflows/desktop-release.yml`). It's the only workflow; `make check` still runs locally. `make release` stays as the local build |
| Task escalation | Due soon (1 h before): the assignee. Overdue: the assignee, again every 4 h. 4 h overdue: the team lead as well. 24 h overdue: the incident commander as well. An unclaimed team task reminds the team's queue (its Teams channel, or its members in Ostraka) in place of an assignee |
| Teams channel | A Workflows webhook per team ("Post to a channel when a webhook request is received"); Ostraka posts an Adaptive Card. The retired Office 365 connector webhooks aren't supported |
| Email | SMTP (STARTTLS or TLS) or Microsoft Graph, per install (`OSTRAKA_MAIL_TRANSPORT`). Graph sends as one mailbox, limited to it by Exchange Online RBAC for Applications rather than a tenant-wide Mail.Send grant |
| Playbooks | Each lists trigger tags. A matching tag pre-ticks the playbook in the declare form; it's applied only when confirmed, and others can be applied later from the Tasks tab |
| Hunts | A second kind of record beside incidents (HNT-YYYY-NNN), with its own phases: Hypothesis → Collect → Analyse → Conclude. Same tasks, findings, observables and comments. Shikari runs the queries; Ostraka tracks the work and the evidence. "Promote to incident" declares an incident linked to the hunt |
| Links between records | Parent and child (one parent per record, no cycles; the parent shows its children's status), related, duplicate of (closes the duplicate), promoted to (hunt → incident). A linked record you can't open is counted, not named |
| Subtasks and blocking | One level of subtasks, each assignable, the parent showing progress. A task can be blocked by another, across incidents; when the blocker finishes, the waiting task's owner is told |
| Workflow playbooks | A playbook is a workflow, not a list. A step can wait for earlier steps, open when the record reaches a phase, or be a decision whose outcomes open the next steps (the others are skipped), close the record, move its phase or promote a hunt. Choosing an earlier step runs it again with everything after it. When steps open, each team they land on is told once. Shown as swimlanes, a lane per team, in the editor and on the record. Applying a playbook brings its teams in |
| MCP server | At `/mcp` on Ostraka itself (streamable HTTP, stateless). API tokens only, never a browser session. Tools go through the API as the token's owner. Read-only tokens get read tools; full tokens add comments and leads, never confirmations. TLP:RED and TLP:AMBER+STRICT records are listed by reference only, as in `notify.outward`. Built ahead of phase 5 (Ben, 10 November 2026) |
| Seed playbooks | Incident response (first hour, then one task per later phase), Phishing and BEC, Ransomware, Account compromise, Data exposure, and Threat hunt for hunts. All editable; playbooks say whether they're for incidents or hunts |
| Lost authenticator | An admin issues a reset link; the person chooses a new password and enrols again |
| Observer IOC access | The briefing view by default; raw IOCs behind a per-user toggle that is off by default |
| Source grading | Intelligence items carry an Admiralty grade: source reliability A–F and information credibility 1–6. They also carry a UK policing handling code: P (lawful sharing permitted) or C (conditions apply, with the conditions recorded). TLP decides who an item may go to; the handling code says whether and how it may be lawfully shared. |
| ACH group rating | Chosen per matrix: independent (each analyst rates blind, then reveal, discuss only the cells that disagree, and record a consensus while keeping the disagreements in the audit trail) or open. Ostraka suggests independent when two or more analysts are rating. |
| API tokens | `ost_` prefix, scopes `full` and `mcp`, same pattern as `rvt_` and `shik_` |
| Seed teams | SOC (SOC), Forensics (FOR), Incident Response (IR), Incident Management (IM), Intelligence (CTI; also Int., Intel.), Threat Hunting (CTH), Leadership (SLT, observers). Each has a short code and other names that @mentions match; all renamable. Migration 0006 merged the first set's Identity and Infrastructure into IR, and Legal and Comms into IM |
| Importing | `ostraka import FILE` loads records through Ostraka's own API as the people the file names, replaying each step at its time so the activity chain is written in order. People not already in Ostraka become accounts that can't sign in. `--sample` loads four incidents and three hunts that show the features; `--remove` deletes what a file loaded, by its tag |
| Nudge delivery | In-app notification centre and browser push, email (SMTP or Microsoft Graph), Microsoft Teams webhook per team |
| Assessments, ACH and reports | Built 11 November 2026, ahead of phase 4. Assessments in structured parts with Ostraka's own linter and sentences, superseded rather than edited; ACH with up to eight analysts, weighted-inconsistency scoring, diagnosticity and sensitivity; lessons learned that become tasks. Reports: a full technical report and a shorter briefing, PDF and Word, from Shikari's renderer restyled in the Ostrakon palette for every report. Exports are for people who worked on the incident; an incident marked sensitive exports for its commander only |
| Editions | Decided 11 November 2026. **Free**: 4 people who can sign in (accounts and open invitations; imported placeholders don't count), one place kept for an admin or team lead; reports in Ostrakon. **Pro**: the licence's seats; one report brand for the installation. **Services** (for MSSPs and consultancies, priced above Pro; was MSSP until later the same day): the licence's seats; a library of brands, chosen per incident; and the right to run Ostraka for clients, which ELv2 otherwise forbids. A brand is a primary and an optional secondary colour, from which Ostraka derives a readable palette (WCAG AA for text, 3:1 for chart series), and up to three logos (cover, header, client) as PNG or JPEG, redrawn by Ostraka before they're stored, never SVG. Severity and TLP colours never take a brand's |
| Licensing | The Elastic License 2.0 for the code, with `COMMERCIAL-LICENSE.md` for Pro and Services, the same for Ostraka, Shikari and Rowanholt (`docs/LICENSING_MODEL.md`). A signed licence line: Ed25519 over the product, licensee, edition, seats, features and validity, checked offline against public keys in the code; one key per product, the private half with Ben (`scripts/admin/sign_licence.py` signs). No phoning home, so local and air-gapped installs work. A licence runs to the end of its expiry day, keeps working for 30 days' grace with a warning, then the installation is Free again: nobody is deactivated and brands are kept, but nobody new joins past the limit and reports go back to Ostrakon. Compiling the backend to deter patching is deferred until patching is actually seen |
| Profile | One page with tabs (Profile, Security, API tokens, Notifications, Appearance) replacing Settings, 10 November 2026. Job title, out-of-hours phone (hidden from observers), time zone and availability (Available, Busy, Away), shown in the directory. Security lists signed-in browsers with sign-out, the person's own security log (including an admin's changes to them), changing authenticator (password and current code, then the new one's first code; other browsers are signed out) and new-browser alerts. Notifications adds per-kind muting and quiet hours that a critical incident overrides |
| Phases | Detect → Scope → Contain → Eradicate → Recover → Learn; hunts: Hypothesis → Collect → Analyse → Conclude |
| AI drafting | Ollama or Anthropic, chosen per deployment; template-only fallback when no model is reachable |
| TLP guard | Incidents marked TLP:RED or TLP:AMBER+STRICT only ever use local Ollama; every draft records which provider produced it |
| Theme | Cobalt by default; Ostrakon (limestone, fired clay and iron-gall ink, replacing Cryptex) and Ledger selectable per user in preferences; each has light and dark. Sign-in shows the mended sherd (clay in light mode, limestone in dark) above a split-flap departures board |
| Type | Inter and JetBrains Mono in every theme (only colours change) |
| Severity and TLP colours | Fixed across themes; severity uses Shikari's four-step scale; TLP uses the FIRST TLP 2.0 colours |
| Hosting | Port 24747 on loopback behind a TLS proxy; only the frontend publishes a port; implements `GET /api/busy` |

Colour directions: https://claude.ai/artifact/N5V8BUNbGEBcgWsCjX7Lpk

## Core model

- **Team**: has leads and its own task queue. A user has one home team and may join others as a guest.
- **Incident**: has severity, TLP, current phase, roles (Incident Commander, Comms, Scribe) and a list of involved teams. IDs look like `OST-2026-014`.
- **Observable**: one record per IOC across the whole platform. A **sighting** records the incident, host, user, when it was seen, the source and who added it.
- **Finding**: a claim backed by evidence. It starts as Lead and moves to Confirmed or Refuted, and only an analyst can confirm it. It must cite evidence: Rowanholt rows, observables or attachments.
- **Asset**: a system of interest; one per incident can be patient zero. Each **asset connection** records lateral movement, C2 or exfiltration between two assets.
- **Graph**: links observables, assets, ATT&CK techniques and findings. It can be exported as an ATT&CK Navigator layer.
- **Task**: assigned either to a team (it waits in the team's queue until someone claims it) or to a person. Each has a checklist and a due date. Playbook templates create tasks as their workflow reaches each step; a decision is a task finished by choosing an outcome.
- **Comment**: threaded on any object, with `@person` and `@team` mentions, `#ID` references and resolve.
- **Tags**: a controlled taxonomy plus free tags.
- **Evidence item**: holds hashes and the chain-of-custody record of handler transfers.
- **Activity log**: append-only and hash-chained. It produces the response timeline automatically; the attack timeline stays separate.

## Features that set Ostraka apart

1. **Handover and presence**: a "since you left" view and a generated shift-handover note; live presence on each object; optimistic concurrency (a version check on every write); cross-incident warnings when someone else is working on the same IOC.
2. **Regulator clocks**: count from the time you became aware, each with an owner and nudges.

   | Regime | Deadline |
   |---|---|
   | UK GDPR (ICO) | 72 h |
   | UK NIS | 72 h |
   | EU NIS2 | Early warning 24 h, notification 72 h, final report 1 month |
   | DORA | Initial within 4 h of classification and no later than 24 h of awareness, intermediate 72 h, final 1 month |
   | Custom | User-defined (contractual, insurer, board) |

3. **Stakeholder updates**: drafted on a fixed rhythm set by severity, from what changed since the last update. Comms approves each one before it goes out. Observers get a read-only briefing view.
   Every update is written to the assessment standard below.
4. **Graph and chain of custody**: as described in the core model.
5. **Detection-gap follow-through**: a follow-up for every confirmed technique without a detection. A gap only closes when Shikari has a detection for it.

## Assessment standard for reports and updates

Ben set four principles on 10 November 2026. Sources:
- Pasquale Stirparo, SANS ISC diaries [22460](https://isc.sans.edu/diary/22460) and [22470](https://isc.sans.edu/diary/22470) (May 2017)
- College of Policing, *Intelligence management APP: professional guide to analysis* (October 2025): ACH p. 26–27, BLUF and 4-3-3 p. 34, writing clearly p. 35, PHIA probability yardstick p. 37–38

1. **Verified, scoped confirmation.** A finding can only be Confirmed with a verification attached. A verification is either:
   - a supporting search (platform, the query text, time range, the host or source it covered, the result count, who ran it and when), or
   - a cited Rowanholt row.

   The person who verifies is recorded separately from the person who reported the finding: the evidence must be verified, not just provided. A verification covers the host or source it searched and nothing wider.
2. **Defensive writing.** An assessment is stored as structured parts, not free prose:
   - the basis: cited findings ("based on X and Y");
   - the judgement, with a yardstick term ("we assess Z is highly likely");
   - negative evidence ("there is no evidence of A"). Each piece must name the search that came back empty, so "no evidence" always says where nobody looked as well as where they did.

   A linter flags certainty that isn't backed by evidence, a missing yardstick term, and loaded adjectives. The APP's examples of loaded adjectives are "huge" and "lacklustre".
3. **Every update carries an assessment.** Updates are written bottom line up front (BLUF): the assessment first, then the evidence from strongest to weakest, then "what", "so what" and "what next", with 4-3-3 as guidance.
   - Ostraka's own templates assemble the full draft from the structured parts, with no AI involved.
   - Optionally, AI rephrases that draft. It may only reword what's already there: it can't add a claim, a citation or a probability term. If the result loses or changes any of those, Ostraka rejects it.
   - The TLP guard applies.
   - Comms approves the update before it goes out.

**The standard is enforced without AI** (Ben, 10 November 2026). Each principle above is met by Ostraka's own structure and fixed rules:
- **Verification:** a required field.
- **Wording:** sentences are assembled from the structured parts.
- **Linting:** fixed rules, not a model.
- **ACH:** plain arithmetic.
- **Yardstick:** a closed list of terms.

An update meets the standard with AI switched off, and AI never decides whether it does.
4. **Analysis of Competing Hypotheses (ACH), built in.** Each incident can have an ACH matrix:
   - Hypotheses go across the top.
   - Evidence goes down the side: findings, observables, assumptions, and negative evidence (things that would be expected but are absent).
   - Each cell is rated C, I or N/A, and each piece of evidence gets a credibility and a relevance of H/M/L.
   - **Scoring:** weighted inconsistency, where each I counts −1 × credibility weight × relevance weight (L 0.707, M 1, H 1.414). The algorithm used is stored with every score.
   - Evidence rated the same against every hypothesis is flagged as non-diagnostic.
   - **Sensitivity:** the score is recomputed with each piece of evidence left out, showing whether the leading hypothesis would change.
   - The report covers every hypothesis, including why each rejected one was rejected.
   - Milestones (what would change the assessment) become tasks.
   - Up to eight analysts can rate a matrix together, as the APP recommends.
   - Yardstick terms are the PHIA probability yardstick's: remote chance ≤5%, highly unlikely 10–20%, unlikely 25–35%, realistic possibility 40–<50%, likely or probably 55–75%, highly likely 80–90%, almost certain ≥95%. Reports include the yardstick as an appendix, as the APP specifies.

## Phases

| # | Phase | Delivers |
|---|---|---|
| 0 | Foundations | Security fixes, compose and homelab setup, `/api/busy`, design tokens and themes, branding module, CLAUDE.md, pytest and Playwright harness, `make check` (all checks run locally; only tagged releases build on GitHub, from 11 November 2026) |
| 1 | People | Users, teams, invites, roles, MFA, API tokens, preferences |
| 2 | Incident core | Incidents, roles, observables and sightings, assets, findings, tags, comments and mentions, activity log, live updates |
| 3 | Work | Team and individual tasks, playbooks, nudges, notifications and delivery channels |
| 3a | Hunts and links | Hunt records and promotion, links between records, subtasks, task blocking, seed playbooks, workflow playbooks with decisions and hand-offs |
| 3b | Everywhere | Built 11 November 2026: the host `ostraka` CLI (`start`, `status`, `first-admin`, `server init`, `app install`) and its PowerShell twin, local and central modes, desktop launchers for macOS (Swift, `.dmg`), Windows (Edge app mode, `.msi`) and Linux (browser app mode, `.deb`, or the source tarball where there's no apt), install and uninstall scripts, the `ost` responder client and server setup for Linux and Windows (WSL 2) in Bash and PowerShell, Help → Licence, `make release` with SHA256SUMS. Some of it still needs real hardware (below) |
| 4 | What sets Ostraka apart | In this order: presence and handover, regulator clocks, graph and custody, stakeholder updates |
| 5 | Integrations | Rowanholt link, Shikari follow-ups and webhooks, STIX/MISP export, Navigator layer (the PDF and Word reports were built early, 11 November 2026) |

## Phase 3b on real hardware

The host CLI, the install, release and server-setup scripts and the responder client are tested locally (`make test-launcher`, `make test-scripts`: shellcheck, PowerShell's parser, and real installs and uninstalls of a dummy package in Debian and Ubuntu containers, and of a source tarball in a Rocky one). An ad-hoc signed `.dmg` has been built on the MacBook (`Ostraka-0.1.0-macos-universal.dmg`). The release workflow (`.github/workflows/desktop-release.yml`, not yet run: it runs once it's pushed) installs and uninstalls the real `.msi` on a Windows runner, the `.dmg` on a macOS runner and the `.deb` on Ubuntu, with the install and uninstall scripts. It checks what each package puts down and takes away: the files, the shortcuts or menu entry, the bundle id and universal binary, the Windows Installer registration. It doesn't start the stack or open the app's window. These haven't been run where they're meant to:

| Piece | Still to check |
|---|---|
| Windows `.msi` | A fresh per-user install and uninstall without `SERVERURL` are the workflow's (once it has run on a Windows runner). Still to check: an upgrade over an older version (the fixed UpgradeCode), and an install with `SERVERURL` |
| Windows launcher | `Ostraka.ps1` opening Edge in app mode, local mode with Docker Desktop, the Help menu; `Ostraka.ps1`, `ostraka-cli.ps1` and `install-service.ps1` are only parse-checked |
| `deploy/windows/install-service.ps1` | A run on Windows Server 2022 or 2025 and on Windows 11 under WSL 2, `-InstallDocker`, and reaching the server from another machine |
| macOS `.dmg` | Install and uninstall of the ad-hoc signed image are the workflow's (once it has run on a macOS runner), and it signs and notarises when the secrets are set (`desktop/README.md`). Still to check: a Developer ID signature and notarisation that pass Gatekeeper, then the app opened from a download on a clean Mac, Apple silicon and Intel |
| Linux desktop | The real `.deb`'s install and uninstall on Ubuntu are the workflow's. Still to check: the `.deb` and the real tarball route on Rocky (the container tests use a cut-down tree) on a desktop, opening Chromium, Chrome, Brave or Edge in app mode; `desktop/linux/tests/test-launcher.sh` isn't in `make check` |
| Updating in place | A newer app updating an unpacked `OSTRAKA_HOME` while keeping `.env` and the `ostraka-app` volumes, on each OS |

## Known issues in the scaffold being replaced

- Open self-registration with a self-chosen team (`auth.py:42`).
- Default admin password published in the README and `config.py`; `SECRET_KEY` and the Postgres password have defaults.
- All four ports published to every interface, including Postgres and Redis.
- The WebSocket JWT is passed in the query string; access and refresh tokens are kept in `localStorage`.
- `get_optional_current_user` calls an async function without awaiting it.
- `create_all` runs alongside Alembic.
- Celery, rate limiting and PDF/DOCX reports are claimed in the README but not implemented.
- No tests or CI.
