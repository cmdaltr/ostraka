"""The anatomy of an attack in plain words: how they get in, what they run, what they do and where it goes.

The words and technique lists are Shikari's (`shikari/visualization/anatomy.py`), so the suite describes an
attack one way; change them there and here together. Shikari reads them from ATT&CK's procedures for a
group; Ostraka reads them from an incident's findings (`ostraka/flow.py`) and playbooks hang their checks
on them. No ATT&CK tactic names are shown: the anatomy is the operation, the techniques carry the mapping.
"""

# ruff: noqa: E501  (the tables are Shikari's, laid out as there so the two can be compared line by line)

from __future__ import annotations

import re
from collections.abc import Iterable

# Ways in, side by side: (key, name, the step into it, technique prefixes, what it covers)
WAYS_IN = [
    ("exploit", "Exploit", "Exploits", ("T1190", "T1203", "T1189", "T1195", "T1211"), "Public-facing apps"),
    ("phishing", "Phishing", "Phishes", ("T1566", "T1598", "T1534"), "Lures"),
    ("credentials", "Credentials", "Signs in", ("T1078", "T1133", "T1199", "T1021.007"), "Valid accounts"),
]

# What it runs. Shikari places tools by its software; a record names them in its findings, or shows the
# techniques that run them
TOOLKIT = ("T1059", "T1047", "T1106", "T1129", "T1204", "T1569", "T1105", "T1219", "T1072")

# What they do once in: (key, action, technique prefixes). An intrusion isn't a sequence of stages, so
# these are listed, not ordered
ACTIONS = [
    ("steals_logins", "Steals logins", ("T1003", "T1555", "T1552", "T1110", "T1558", "T1539", "T1056", "T1528",
                                        "T1606", "T1111", "T1621", "T1556", "T1212")),
    ("moves", "Moves across the network", ("T1021", "T1570", "T1550", "T1563", "T1210", "T1080", "T1072")),
    ("admin", "Gains admin rights", ("T1068", "T1548", "T1134", "T1484")),
    ("keeps_access", "Keeps access", ("T1053", "T1547", "T1543", "T1136", "T1098", "T1505", "T1546", "T1574",
                                      "T1037", "T1197", "T1112", "T1137", "T1542")),
    ("hides", "Hides its tracks", ("T1070", "T1562", "T1027", "T1036", "T1218", "T1564", "T1140", "T1055",
                                   "T1620", "T1497", "T1480", "T1014", "T1006", "T1222", "T1553", "T1685")),
    ("looks_around", "Looks around", ("T1082", "T1083", "T1087", "T1018", "T1016", "T1069", "T1057", "T1046",
                                      "T1135", "T1482", "T1012", "T1033", "T1049", "T1007", "T1518", "T1580",
                                      "T1538", "T1526", "T1613", "T1201", "T1124", "T1120", "T1614", "T1615",
                                      "T1652", "T1654", "T1619")),
    ("gathers", "Gathers data", ("T1005", "T1039", "T1213", "T1114", "T1530", "T1560", "T1074", "T1119", "T1115",
                                 "T1113", "T1025", "T1123", "T1125", "T1185", "T1602")),
    ("takes_out", "Takes data out", ("T1567", "T1041", "T1048", "T1030", "T1537", "T1020", "T1029", "T1052")),
    ("destroys", "Encrypts or destroys", ("T1486", "T1490", "T1485", "T1561", "T1489", "T1529", "T1491", "T1565",
                                          "T1499")),
    ("extorts", "Extorts", ("T1657",)),
]  # fmt: skip

# Where it goes, side by side: (key, name, the step into it, service groups in it, technique prefixes).
# Infrastructure, the attacker's own addresses, is Shikari's last step for a CVE
SERVICES = [
    ("cloud", "Cloud", "Uses", ("Cloud tenants", "Hosting", "Tunnels and anonymisers"), ("T1537", "T1578", "T1535")),
    ("messaging", "Messaging", "Beacons to", ("Messaging",), ("T1102",)),
    ("storage", "Code and storage", "Collects from", ("Code and paste sites", "Cloud storage"), ("T1567.001", "T1567.002")),
    ("infrastructure", "Infrastructure", "Connects to", (), ("T1071", "T1090", "T1572", "T1573", "T1568", "T1095",
                                                            "T1104", "T1001", "T1008", "T1041")),
]  # fmt: skip

# Legitimate services named in procedures, by group: (group, [(name, pattern)])
SERVICE_GROUPS = [
    ("Cloud storage", [("Dropbox", r"Dropbox"), ("OneDrive", r"OneDrive"), ("Google Drive", r"Google Drive|Google Docs"),
                       ("MEGA", r"\bMEGA(?:\.nz)?\b|Mega\.nz"), ("Box", r"\bBox\.com"), ("Yandex Disk", r"Yandex")]),
    ("Cloud tenants", [("AWS", r"\bAWS\b|Amazon S3|Amazon Web Services|S3 bucket"), ("Azure", r"Azure|Entra ID"),
                       ("Microsoft 365", r"Microsoft Graph|SharePoint|Microsoft 365|Office 365"), ("Okta", r"Okta"),
                       ("Firebase", r"Firebase")]),
    ("Messaging", [("Telegram", r"Telegram"), ("Discord", r"Discord"), ("Slack", r"Slack"),
                   ("Teams", r"Microsoft Teams|\bTeams\b"), ("Gmail", r"Gmail"), ("Twitter", r"Twitter")]),
    ("Code and paste sites", [("GitHub", r"GitHub"), ("GitLab", r"GitLab"), ("Pastebin", r"Pastebin"),
                              ("Bitbucket", r"Bitbucket"), ("Notion", r"\bNotion\b"), ("Trello", r"Trello")]),
    ("Tunnels and anonymisers", [("ngrok", r"ngrok"), ("Tor", r"\bTor\b"), ("Cloudflare", r"Cloudflare")]),
    ("Remote access", [("AnyDesk", r"AnyDesk"), ("TeamViewer", r"TeamViewer"),
                       ("ScreenConnect", r"ScreenConnect|ConnectWise Control"), ("Atera", r"Atera"),
                       ("Splashtop", r"Splashtop"), ("RustDesk", r"RustDesk"), ("VPN", r"\bVPNs?\b")]),
    ("Hosting", [("DigitalOcean", r"DigitalOcean"), ("Vultr", r"Vultr"), ("Linode", r"Linode")]),
]  # fmt: skip
_SERVICES = [
    (group, name, re.compile(rx, 0 if name in ("Tor", "MEGA") else re.I))
    for group, members in SERVICE_GROUPS
    for name, rx in members
]
# A remote-access product the attacker runs is part of its toolkit, not a service it reaches
TOOLKIT_SERVICES = {"Remote access"}

# Tools by group, as Shikari sorts them; a record's findings name them in prose
FRAMEWORKS = {
    "cobalt strike",
    "empire",
    "powersploit",
    "impacket",
    "metasploit",
    "sliver",
    "brute ratel c4",
    "mythic",
    "havoc",
    "koadic",
    "pupy",
    "poshc2",
    "covenant",
    "nishang",
    "powerview",
    "bloodhound",
    "sharphound",
    "rubeus",
    "responder",
    "adfind",
    "crackmapexec",
    "netexec",
    "merlin",
    "silenttrinity",
}
CREDENTIAL_TOOLS = {
    "mimikatz",
    "lazagne",
    "pwdump",
    "gsecdump",
    "wce",
    "secretsdump",
    "nanodump",
    "dumpert",
    "safetykatz",
    "kekeo",
    "redline stealer",
    "lumma stealer",
    "vidar",
    "agent tesla",
    "formbook",
    "azorult",
    "lokibot",
    "raccoon stealer",
    "stealc",
    "atomic stealer",
    "pony",
}
WEB_SHELLS = {
    "china chopper",
    "aspxspy",
    "godzilla",
    "behinder",
    "antsword",
    "regeorg",
    "neo-regeorg",
    "p.a.s. webshell",
    "sharppanda",
    "tunna",
    "weevely",
    "b374k",
}
EXFIL_TOOLS = {"rclone", "megasync", "restic", "winscp", "filezilla", "exmatter", "stealbit"}
REMOTE_TOOLS = {
    "anydesk",
    "teamviewer",
    "screenconnect",
    "connectwise",
    "atera",
    "splashtop",
    "rustdesk",
    "ngrok",
    "plink",
    "chisel",
    "frp",
    "ligolo",
    "tor",
    "gost",
    "fast reverse proxy",
}
BUILT_IN = {
    "net",
    "certutil",
    "cmd",
    "powershell",
    "psexec",
    "reg",
    "wmic",
    "schtasks",
    "at",
    "ipconfig",
    "systeminfo",
    "tasklist",
    "netstat",
    "bitsadmin",
    "rundll32",
    "regsvr32",
    "mshta",
    "ntdsutil",
    "vssadmin",
    "wevtutil",
    "nltest",
    "dsquery",
    "ping",
    "whoami",
    "arp",
    "route",
    "netsh",
    "sc",
    "taskkill",
    "esentutl",
    "msiexec",
    "cscript",
    "wscript",
    "forfiles",
    "expand",
    "findstr",
    "query",
    "qwinsta",
    "nbtstat",
    "ftp",
    "tar",
    "curl",
    "wget",
    "ssh",
    "procdump",
    "adexplorer",
    "dsget",
    "sdelete",
    "attrib",
    "icacls",
    "xcopy",
    "robocopy",
}
TOOL_GROUPS = [("Frameworks", FRAMEWORKS), ("Stealers", CREDENTIAL_TOOLS), ("Web shells", WEB_SHELLS),
               ("Remote access", REMOTE_TOOLS), ("Exfiltration", EXFIL_TOOLS), ("Built-in", BUILT_IN)]  # fmt: skip
# Names that are ordinary words in an incident's prose ("the first responder", "net") or too short to find
# safely; Shikari reads them from ATT&CK's software list instead, where they're unambiguous
_PROSE_WORDS = {"responder", "havoc", "covenant", "merlin", "mythic", "empire", "pony", "tor", "frp", "gost", "wce",
                "net", "cmd", "reg", "at", "ping", "arp", "route", "sc", "expand", "query", "ftp", "tar", "ssh",
                "attrib", "findstr", "whoami", "curl", "wget"}  # fmt: skip
_TOOLS = [
    (group, name, re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.I))
    for group, names in TOOL_GROUPS
    for name in sorted(names)
    if name not in _PROSE_WORDS
]
# how the services Shikari also lists as tools are written
_SPELLING = {name.lower(): name for _, members in SERVICE_GROUPS for name, _ in members}
_CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)


def has(techniques: Iterable[str], prefixes: Iterable[str]) -> bool:
    """Whether any technique is one of the prefixes or a sub-technique of one."""
    prefixes = tuple(prefixes)
    return any(t == p or t.startswith(p + ".") for t in techniques for p in prefixes)


def services_in(text: str) -> list[tuple[str, str]]:
    """The legitimate services a piece of text names, as (group, name), in the order of SERVICE_GROUPS."""
    return [(group, name) for group, name, rx in _SERVICES if rx.search(text)]


def services_at(address: str) -> list[tuple[str, str]]:
    """The services a domain or URL belongs to. Addresses are lower case, so MEGA, matched by case in
    prose where "mega" is an ordinary word, is matched here in any case."""
    found = services_in(address)
    return found + [s for s in services_in(address.upper()) if s[1] == "MEGA" and s not in found]


def tools_in(text: str) -> list[tuple[str, str]]:
    """The attacker tools a piece of text names, as (group, name as written in the list)."""
    found: list[tuple[str, str]] = []
    for group, name, rx in _TOOLS:
        if rx.search(text) and not any(name == n for _, n in found):
            found.append((group, name))
    return found


def tool_name(group: str, name: str) -> str:
    """A tool's name as people write it: AnyDesk, Mimikatz, Cobalt Strike, and built-in tools as typed."""
    return name if group == "Built-in" else _SPELLING.get(name, name.title())


def cves_in(text: str) -> list[str]:
    """CVE IDs in a piece of text, upper-cased and in order."""
    return list(dict.fromkeys(c.upper() for c in _CVE.findall(text)))
