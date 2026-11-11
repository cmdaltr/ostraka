"""MITRE ATT&CK technique names and tactics for reports, from the bundled `attack.json`.

`make_attack.py` regenerates the file from a newer enterprise-attack bundle.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_PATH = Path(__file__).with_name("attack.json")


@cache
def _data() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def technique(tid: str) -> tuple[str, list[str]]:
    """A technique's name and tactic shortnames, e.g. ("PowerShell", ["execution"]); ("", []) when unknown."""
    entry = _data().get(tid.strip().upper())
    if not entry or tid.startswith("_"):
        return "", []
    return entry["name"], list(entry["tactics"])


def tactic_name(shortname: str) -> str:
    """A tactic's display name from its shortname ("lateral-movement" -> "Lateral Movement")."""
    for t in _data()["_tactics"]:
        if t["shortname"] == shortname:
            return t["name"]
    return shortname.replace("-", " ").title()


VERSION: str | None = _data().get("_version")
TERMS: str = _data()["_terms"]
TACTICS: list[tuple[str, str]] = [(t["shortname"], t["name"]) for t in _data()["_tactics"]]
"""(shortname, name) in kill-chain order, Reconnaissance first."""
