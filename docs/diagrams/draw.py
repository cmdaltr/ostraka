"""Draws the diagrams in docs/diagrams as SVG (navy, teal, amber; JetBrains Mono). Run: python3 docs/diagrams/draw.py"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

NAVY, TEAL, AMBER, RED, WHITE, MUTED = "#0D1B2A", "#00D4AA", "#F59E0B", "#EF4444", "#FFFFFF", "#8FA3B8"
FONT = "'JetBrains Mono','Courier New',monospace"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent


class Svg:
    def __init__(self, w, h, title):
        self.w, self.h, self.parts = w, h, []
        self.text(w / 2, 34, title, 18, TEAL, anchor="middle", weight=700)

    def text(self, x, y, s, size=12, fill=WHITE, anchor="start", weight=400):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
                          f'font-weight="{weight}">{escape(s)}</text>')

    def box(self, x, y, w, h, title, lines=(), colour=TEAL, dashed=False, fill="#10243A"):
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" '
                          f'stroke="{colour}" stroke-width="1.5"{dash}/>')
        self.text(x + 12, y + 22, title, 13, colour, weight=700)
        for i, line in enumerate(lines):
            self.text(x + 12, y + 41 + i * 16, line, 11, WHITE)
        return (x, y, w, h)

    def group(self, x, y, w, h, label, lx=None):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" '
                          f'stroke="{TEAL}" stroke-opacity="0.3" stroke-width="1"/>')
        self.text(lx if lx is not None else x + 14, y + 22, label, 12, MUTED, weight=700)

    def arrow(self, pts, label="", colour=TEAL, dashed=False, lx=None, ly=None, both=False):
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        mid = {TEAL: "t", AMBER: "a", RED: "r"}[colour]
        start = f' marker-start="url(#s{mid})"' if both else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2"{dash} '
                          f'marker-end="url(#e{mid})"{start}/>')
        if label:
            (x1, y1), (x2, y2) = pts[len(pts) // 2 - 1], pts[len(pts) // 2]
            x, y = lx if lx is not None else (x1 + x2) / 2, ly if ly is not None else (y1 + y2) / 2 - 7
            self.parts.append(f'<text x="{x}" y="{y}" font-size="10.5" fill="{colour}" text-anchor="middle" '
                              f'paint-order="stroke" stroke="{NAVY}" stroke-width="4">{escape(label)}</text>')

    def legend(self, x, y, items):
        for i, (colour, dashed, label) in enumerate(items):
            dash = ' stroke-dasharray="6 4"' if dashed else ""
            yy = y + i * 18
            self.parts.append(f'<line x1="{x}" y1="{yy}" x2="{x + 28}" y2="{yy}" stroke="{colour}" stroke-width="2"{dash}/>')
            self.text(x + 36, yy + 4, label, 11, MUTED)

    def save(self, name):
        markers = "".join(
            f'<marker id="e{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M0 0 L10 5 L0 10 z" fill="{c}"/></marker>'
            f'<marker id="s{k}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M10 0 L0 5 L10 10 z" fill="{c}"/></marker>'
            for k, c in (("t", TEAL), ("a", AMBER), ("r", RED)))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">'
               f'<defs>{markers}</defs><rect width="{self.w}" height="{self.h}" fill="{NAVY}"/>'
               + "".join(self.parts) + "</svg>\n")
        (OUT / name).write_text(svg)


# ---- 1. Architecture --------------------------------------------------------------------------------
s = Svg(1240, 830, "Ostraka · architecture")
s.group(20, 60, 230, 420, "people and clients")
s.box(35, 95, 200, 92, "Browser", ["responders, team leads,", "observers, admins", "React SPA · TanStack Query"])
s.box(35, 215, 200, 92, "MCP client", ["Claude Code and others", "Bearer ost_… token", "read-only or full"])
s.box(35, 335, 200, 120, "Server CLI", ["docker exec backend", "ostraka bootstrap-admin", "ostraka import --sample", "(in-process API)"])
s.group(268, 60, 460, 260, "server · Docker Compose")
s.box(283, 120, 200, 92, "TLS proxy", ["Caddy, nginx or similar", "https://ostraka.example.org", "→ 127.0.0.1:24747"])
s.box(513, 120, 200, 92, "web · nginx :8080", ["built SPA", "/api /mcp /ws → backend", "security headers, no CORS"])
s.group(748, 60, 472, 500, "backend · FastAPI :6176 (package ostraka)")
cells = [
    ("API routers /api", ["incidents, hunts, findings,", "tasks, playbooks, people"]),
    ("Auth & policy", ["password + TOTP for all", "JWT in memory, refresh cookie"]),
    ("Incidents", ["access rules · IOC masking", "hash-chained activity log"]),
    ("MCP server /mcp", ["tools call /api in-process", "as the token's owner"]),
    ("Live hub /ws", ["says what changed,", "never the content"]),
    ("Work & workflow", ["queues, nudges, decisions,", "hand-offs, phase gates"]),
    ("Notify · outward", ["written in the transaction,", "sent after the commit"]),
    ("Scheduler", ["reminders → team lead →", "incident commander"]),
]
pos = {}
for i, (t, l) in enumerate(cells):
    x, y = 763 + (i % 2) * 225, 95 + (i // 2) * 112
    pos[t] = s.box(x, y, 210, 92, t, l)
s.box(763, 620, 210, 80, "Postgres 16", ["schema by Alembic only", "users, records, activity"])
s.box(988, 620, 210, 80, "Redis 7", ["pub/sub for live updates", "WebSocket tickets"])
s.group(268, 560, 460, 255, "leaves Ostraka · masked by notify.outward")
s.box(283, 595, 200, 76, "Email", ["SMTP or Microsoft Graph"], AMBER)
s.box(513, 595, 200, 76, "Web Push", ["VAPID · encrypted payload"], AMBER)
s.box(283, 690, 200, 76, "Microsoft Teams", ["a team's channel webhook"], AMBER)
s.box(513, 690, 200, 105, "AI provider", ["Ollama (local) or Anthropic", "rephrases finished drafts", "TLP:RED and AMBER+STRICT", "stay on Ollama"], AMBER)
s.box(35, 600, 200, 70, "Rowanholt", ["evidence and analysis"], TEAL, dashed=True)
s.box(35, 690, 200, 70, "Shikari", ["detection and hunting"], TEAL, dashed=True)
s.text(35, 785, "beside ostraka, not inside it", 11, MUTED)
s.arrow([(235, 141), (283, 141)], "HTTPS", ly=133)
s.arrow([(235, 261), (259, 261), (259, 196), (283, 196)], "HTTPS", lx=259, ly=232)
s.arrow([(483, 166), (513, 166)])
s.text(498, 236, "127.0.0.1", 10.5, TEAL, anchor="middle")
s.text(498, 250, ":34747", 10.5, TEAL, anchor="middle")
s.arrow([(713, 166), (748, 166)])
s.text(613, 112, "/api /mcp /ws → backend", 10.5, TEAL, anchor="middle")
s.arrow([(235, 395), (748, 395)], "in-process API (httpx ASGI)", ly=388)
s.arrow([(868, 560), (868, 620)], "SQL · asyncpg", lx=905, ly=595)
s.arrow([(1093, 560), (1093, 620)], "publish / subscribe", lx=1140, ly=595, both=True)
s.arrow([(763, 470), (740, 470), (740, 633), (713, 633)], "masked", AMBER, lx=740, ly=520)
s.legend(990, 735, [(TEAL, False, "inside ostraka"), (AMBER, False, "leaves ostraka, cut down first"),
                    (TEAL, True, "separate apps")])
s.save("architecture.svg")

# ---- 2. Records: incidents, hunts, findings --------------------------------------------------------
s = Svg(1240, 590, "Ostraka · records, phases and findings")
s.group(20, 60, 1200, 130, "hunt  ·  HNT-YYYY-NNN")
hx = [40, 335, 630, 925]
for x, (t, l) in zip(hx, [("Hypothesis", ["what we expect to find,", "and where"]), ("Collect", ["searches run in Shikari,", "evidence in Rowanholt"]),
                          ("Analyse", ["leads, not verdicts"]), ("Conclude", ["found something, or not"])]):
    s.box(x, 95, 265, 80, t, l)
for a, b in zip(hx, hx[1:]):
    s.arrow([(a + 265, 135), (b, 135)])
s.group(20, 230, 1200, 130, "incident  ·  OST-YYYY-NNN", lx=250)
ix = [40, 238, 436, 634, 832, 1030]
for x, (t, l) in zip(ix, [("Detect", ["declared, roles set"]), ("Scope", ["assets, observables"]), ("Contain", ["stop the spread"]),
                          ("Eradicate", ["remove the foothold"]), ("Recover", ["back to normal"]), ("Learn", ["lessons, write-up"])]):
    s.box(x, 265, 175, 80, t, l)
for a, b in zip(ix, ix[1:]):
    s.arrow([(a + 175, 305), (b, 305)])
s.arrow([(1057, 175), (1057, 215), (127, 215), (127, 265)], "promote: found something", AMBER, lx=590, ly=208)
s.group(20, 395, 1200, 175, "findings  ·  FND-N  ·  the reporting standard")
s.box(40, 440, 250, 90, "Lead", ["anyone, an MCP client,", "or the importer", "never confirmed by itself"])
s.box(400, 440, 250, 90, "Verification", ["source reliability and", "information credibility,", "what it was checked against"])
s.box(760, 440, 200, 90, "Confirmed", ["only by an analyst"])
s.box(1000, 440, 200, 90, "Refuted", ["kept, with the reason"], RED)
s.arrow([(290, 485), (400, 485)])
s.arrow([(650, 485), (760, 485)], "analyst")
s.arrow([(560, 440), (560, 425), (1100, 425), (1100, 440)], "not borne out", RED, lx=980, ly=418)
s.text(620, 556, "every update carries an assessment, in PHIA probability terms", 11, MUTED, anchor="middle")
s.save("records.svg")

# ---- 3. Notifications and nudges --------------------------------------------------------------------
s = Svg(1240, 590, "Ostraka · notifications and nudges")
s.box(30, 80, 210, 92, "A change", ["an API request:", "task, finding, comment,", "phase, mention"])
s.box(275, 80, 230, 92, "One transaction", ["activity event (hash chain)", "notification rows", "version bump"])
s.box(540, 80, 200, 92, "Commit", ["activity.finish", "→ notify.dispatch"])
s.box(775, 70, 430, 112, "notify.outward", ["indicators masked in every message", "TLP:RED / AMBER+STRICT: the reference and", "the kind of event only", "a link back into ostraka"], AMBER)
s.arrow([(240, 126), (275, 126)])
s.arrow([(505, 126), (540, 126)])
s.arrow([(740, 126), (775, 126)])
outs = [("In ostraka", ["the bell, live over Redis", "(what changed, not content)"], TEAL),
        ("Browser push", ["Web Push, encrypted", "per subscription"], AMBER),
        ("Email", ["SMTP or Graph", "one sending mailbox"], AMBER),
        ("Teams channel", ["the team's webhook", "an Adaptive Card"], AMBER)]
for i, (t, l, c) in enumerate(outs):
    x = 30 + i * 300
    s.box(x, 250, 270, 80, t, l, c)
    s.arrow([(990, 182), (990, 215), (x + 135, 215), (x + 135, 250)], "", AMBER if c == AMBER else TEAL)
s.group(20, 360, 1200, 145, "the scheduler · one process per tick, by advisory lock")
steps = [("Task due", ["owner: a person, or", "the team's queue"]), ("Reminder", ["to the owner, or the", "team in ostraka and Teams"]),
         ("Escalate", ["to the team lead"]), ("Escalate again", ["to the incident commander"])]
for i, (t, l) in enumerate(steps):
    s.box(40 + i * 295, 400, 240, 85, t, l, RED if i >= 2 else TEAL)
    if i:
        s.arrow([(40 + (i - 1) * 295 + 240, 442), (40 + i * 295, 442)], ["", "due", "overdue", "still"][i], RED if i >= 2 else TEAL)
s.legend(40, 530, [(TEAL, False, "inside ostraka"), (AMBER, False, "leaves ostraka, cut down first"), (RED, False, "overdue")])
s.save("notifications.svg")

# ---- 4. MCP -----------------------------------------------------------------------------------------
s = Svg(1240, 360, "Ostraka · MCP at /mcp")
row = [("MCP client", ["POST /mcp", "Bearer ost_…"]), ("Token check", ["an API token, never", "a browser JWT"]),
       ("Tools offered", ["read-only token:", "read tools only"]), ("Tool call", ["e.g. get_record", "OST-2026-014"]),
       ("ostraka's own API", ["in-process (ASGI)", "as the token's owner"])]
for i, (t, l) in enumerate(row):
    s.box(30 + i * 242, 90, 210, 80, t, l)
    if i:
        s.arrow([(30 + (i - 1) * 242 + 210, 130), (30 + i * 242, 130)])
s.box(272, 230, 210, 80, "401", ["no token, a JWT,", "or an unknown one"], RED)
s.arrow([(377, 170), (377, 230)], "refused", RED, lx=410)
s.box(756, 230, 210, 90, "TLP:RED", ["AMBER+STRICT too:", "reference only;", "open it in ostraka"], RED)
s.box(998, 230, 212, 80, "Result", ["what the owner sees,", "indicators masked as for them"])
s.arrow([(1103, 170), (1103, 230)])
s.arrow([(1030, 170), (1030, 200), (861, 200), (861, 230)], "restricted record", RED, lx=945, ly=194)
s.text(620, 342, "add_comment and add_lead need a full token; a lead is never confirmed", 11, MUTED, anchor="middle")
s.save("mcp.svg")

# ---- 5. Distribution --------------------------------------------------------------------------------
s = Svg(1240, 660, "Ostraka · distribution")
s.group(20, 60, 260, 400, "desktop apps · thin launchers")
s.box(35, 95, 230, 80, "macOS · .dmg", ["Swift WebKit window", "universal: arm64, x86_64"])
s.box(35, 190, 230, 80, "Windows · .msi", ["Edge in app mode,", "from Ostraka.ps1"])
s.box(35, 285, 230, 80, "Linux · .deb or tarball", ["Chromium, Chrome, Brave", "or Edge, in app mode"])
for i, line in enumerate(["no Ostraka logic inside;", "first launch: connect to a", "team, or run it here"]):
    s.text(35, 400 + i * 16, line, 11, MUTED)
s.group(320, 60, 400, 400, "local mode · this computer, needs Docker")
s.box(335, 95, 370, 96, "Host ostraka CLI", ["./ostraka · ostraka-cli.ps1 on Windows", "start, stop, status --json, first-admin",
                                                 "anything else → the backend's own CLI"])
s.box(335, 221, 370, 96, "Docker Compose stack", ["db, redis, backend, web", "compose project ostraka-app",
                                                     "web on 127.0.0.1:24747 only"])
s.box(335, 347, 370, 80, "OSTRAKA_HOME", ["~/Ostraka · %USERPROFILE%\\Ostraka",
                                             "unpacked from the package, .ostraka-version"])
s.group(740, 60, 480, 400, "central mode · a team server")
s.box(755, 95, 205, 96, "deploy/linux/install.sh", ["copies to /opt/ostraka", ".env mode 600, secrets kept",
                                                       "first admin's invitation"])
s.box(1000, 95, 205, 96, "install-service.ps1", ["deploy/windows/", "WSL 2 and Ubuntu, then", "install.sh as root"])
s.box(755, 221, 450, 96, "Compose stack · /opt/ostraka", ["the same tree and stack as local mode",
                                                              "Ostraka on 127.0.0.1:24747 only",
                                                              "ostraka server init runs install.sh"])
s.box(755, 347, 450, 96, "HTTPS in front", ["Caddy in the stack, optional: --caddy acme or internal",
                                               "or the team's own Caddy or nginx",
                                               "/ws upgraded, uploads up to 100 MB"])
s.box(755, 520, 205, 80, "Browser", ["the team, signed in with", "password + TOTP"])
s.box(1000, 520, 205, 96, "ost · responder", ["clients/ost, ost.ps1", "Bearer ost_… API token", "declare, ioc, host, comment"])
s.box(35, 520, 420, 110, "Releases · a v* tag", ["tagged releases build on GitHub Actions",
                                                    ".dmg .msi .deb + source, each with the source tree",
                                                    "installed and removed on its own OS, SHA256SUMS",
                                                    "make release builds the same files locally"])
s.arrow([(280, 143), (335, 143)], "local", ly=136)
s.arrow([(280, 269), (335, 269)], "window", ly=262)
s.arrow([(520, 191), (520, 221)], "docker compose", lx=580, ly=210)
s.arrow([(520, 347), (520, 317)], "source tree", dashed=True, lx=570, ly=336)
s.arrow([(150, 460), (150, 480), (735, 480), (735, 395), (755, 395)], "central: HTTPS · GET /api/meta", ly=473)
s.arrow([(100, 520), (100, 460)], "packages", dashed=True, lx=135, ly=500)
s.arrow([(857, 191), (857, 221)], "sets up", dashed=True, lx=900, ly=210)
s.arrow([(1000, 143), (960, 143)], "WSL 2", ly=136)
s.arrow([(980, 347), (980, 317)], "127.0.0.1:24747", lx=1050, ly=336)
s.arrow([(857, 520), (857, 443)], "HTTPS", lx=890, ly=485)
s.arrow([(1102, 520), (1102, 443)], "HTTPS", lx=1135, ly=485)
s.legend(490, 560, [(TEAL, False, "runs or calls"), (TEAL, True, "sets up or carries")])
s.save("distribution.svg")
