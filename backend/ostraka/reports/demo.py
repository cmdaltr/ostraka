"""A demonstration report that uses every block and chart, for the renderer's test and for review.

    python -m ostraka.reports.demo [OUT_DIR]

writes `renderer-sample.pdf` and `.docx` to OUT_DIR (default: the current directory). The incident is
invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ostraka.log import configure
from ostraka.reports import attack, charts
from ostraka.reports.branded import SEVERITY, STATUS, Cover, write_sample
from ostraka.reports.theme import OSTRAKON

INK, CLAY, BAND = OSTRAKON.ink, OSTRAKON.clay, OSTRAKON.band

TLP = "TLP:AMBER+STRICT"
PHASE_FILLS = {
    "detect": INK,
    "scope": (110, 90, 74),
    "contain": CLAY,
    "eradicate": (201, 138, 32),
    "recover": (168, 155, 142),
    "learn": (221, 211, 195),
}


def cover() -> Cover:
    """The demonstration cover, at TLP:AMBER+STRICT."""
    return Cover(
        kind="Incident Report",
        title="Ransomware staging on the finance file server",
        reference="OST-2026-014",
        date="11 November 2026",
        tlp=TLP,
        prepared_for="Contoso Ltd",
        lines=["Incident lead: B. Smith", "Status: closed"],
    )


def _findings() -> list[tuple[str, str, str, str, str]]:
    techniques = ["T1059.001", "T1003.001", "T1021.002", "T1486", "T1047", "T1053.005", "T1082", "T1070.004"]
    hosts = ["FS01", "WS-114", "WS-207", "DC02"]
    status = ["confirmed", "lead", "confirmed", "refuted"]
    severity = ["critical", "high", "medium", "low"]
    rows = []
    for i in range(36):
        tid = techniques[i % len(techniques)]
        name, _ = attack.technique(tid)
        rows.append(
            (f"F-{i + 1:02d}", f"{tid} {name}", hosts[i % len(hosts)], severity[i % 4], status[(i // 2) % 4])
        )
    return rows


def blocks() -> list[tuple]:
    """Every block type, an AI-marked section, paragraph and cell, a table long enough to span pages,
    and each chart."""
    findings = _findings()
    headers = ["Ref", "Technique", "Host", "Severity", "Status"]
    rows, fills = [], {}
    for i, (ref, tech, host, sev, status) in enumerate(findings):
        rows.append([ref, tech, host, sev.capitalize(), status.capitalize() + ("†" if i == 3 else "")])
        fills[(i, 3)] = SEVERITY[sev]
        fills[(i, 4)] = STATUS[status]

    matrix: dict[str, list[tuple[str, tuple[int, int, int]]]] = {}
    for _, tech, _, _, status in findings[:16]:
        tid = tech.split()[0]
        for tactic in attack.technique(tid)[1][:1]:
            cells = matrix.setdefault(tactic, [])
            if tid not in [c[0] for c in cells]:
                cells.append((tid, STATUS[status]))
    matrix.setdefault("initial-access", []).append(("T1566.001", STATUS["confirmed"]))
    matrix.setdefault("command-and-control", []).append(("T1071.001", STATUS["lead"]))
    matrix.setdefault("exfiltration", []).append(("T1567.002", STATUS["refuted"]))

    return [
        ("section", "Executive Summary"),
        (
            "para",
            "On 3 November 2026 an alert on the finance file server led to the discovery of ransomware "
            "staged but not yet run. The attacker reached the server from a phished workstation, dumped "
            "credentials and moved laterally over SMB. Containment took nine hours from declaration.",
        ),
        (
            "figure",
            charts.headline_cards(
                [
                    ("36", "Findings", INK),
                    ("9.0 h", "To containment", CLAY),
                    ("4", "Hosts affected", SEVERITY["high"]),
                    ("0", "Files encrypted", BAND),
                ]
            ),
            "The incident in four numbers.",
            17.0,
        ),
        ("sub", "What happened"),
        (
            "bullets",
            [
                "A macro-enabled invoice opened on WS-114 started PowerShell (T1059.001).",
                "The attacker read LSASS memory on WS-114 and reused a service account's hash.",
                "The attacker's motive was most likely financial.†",
            ],
        ),
        (
            "card",
            "F-01  T1059.001 PowerShell: Critical",
            SEVERITY["critical"],
            [
                ("Host", "WS-114", None),
                ("Status", "Confirmed", STATUS["confirmed"]),
                ("Verification", "Process tree from the EDR export, row 4,812", None),
                ("Severity", "Critical", SEVERITY["critical"]),
            ],
        ),
        ("section", "Timeline and Phases"),
        ("para", "Time spent in each phase, from declaration to the lessons-learned review."),
        (
            "figure",
            charts.phase_bar(
                [
                    ("detect", 1.5, PHASE_FILLS["detect"]),
                    ("scope", 4.0, PHASE_FILLS["scope"]),
                    ("contain", 3.5, PHASE_FILLS["contain"]),
                    ("eradicate", 12.0, PHASE_FILLS["eradicate"]),
                    ("recover", 30.0, PHASE_FILLS["recover"]),
                    ("learn", 0.75, PHASE_FILLS["learn"]),
                ]
            ),
            "Hours in each incident phase.",
        ),
        ("section", "Findings†"),
        ("sub", "Outcomes"),
        (
            "figure",
            charts.horizontal_bars(
                [
                    ("Confirmed", 18, STATUS["confirmed"]),
                    ("Lead", 9, STATUS["lead"]),
                    ("Refuted", 9, STATUS["refuted"]),
                ],
                total=36,
            ),
            "Findings by status.",
        ),
        ("sub", "Coverage"),
        (
            "figure",
            charts.attack_matrix(
                matrix,
                legend=[
                    ("Confirmed", STATUS["confirmed"]),
                    ("Lead", STATUS["lead"]),
                    ("Refuted", STATUS["refuted"]),
                ],
            ),
            "Techniques seen, by MITRE ATT&CK tactic.",
        ),
        ("sub", "All findings"),
        ("para", "Every finding, most severe first. Confirmed findings carry a verification.†"),
        ("table", (headers, rows, [1.4, 8.0, 2.2, 2.4, 3.0], fills), "Findings register.", True),
        ("section", "Assessment"),
        ("sub", "Competing hypotheses"),
        (
            "figure",
            charts.ach_scores(
                [
                    ("H1: Financially motivated ransomware affiliate", -1.0, True),
                    ("H2: Insider staging data for theft", -4.5, False),
                    ("H3: State actor using ransomware as cover for destruction", -7.0, False),
                    ("H4: Red team exercise not announced to the SOC", 0 - 9.0, False),
                ]
            ),
            "Inconsistency scores; H1 leads.",
        ),
        ("sub", "Hunting query"),
        (
            "code",
            "DeviceProcessEvents\n"
            "| where Timestamp > ago(14d)\n"
            '| where FileName =~ "powershell.exe" and ProcessCommandLine has_any ("-enc", "-EncodedCommand", '
            '"FromBase64String", "IEX", "DownloadString")\n'
            "| summarize count() by DeviceName, AccountName, bin(Timestamp, 1h)",
        ),
        ("sub", "Indicators"),
        (
            "code",
            "185.220.101.47\ninvoice-portal[.]co\nd2c7f8e1a0b94c3e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e",
        ),
        (
            "note",
            [
                "† AI-assisted content. Items marked † were rephrased by an AI model from a finished draft. "
                "The claims, citations and probability terms were checked to be unchanged.",
                "Everything else was written by the responders.",
            ],
        ),
    ]


if __name__ == "__main__":
    configure("INFO")
    write_sample(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd())
