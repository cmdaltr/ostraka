"""Regenerate `attack.json`, the compact ATT&CK lookup the reports use, from an enterprise-attack STIX bundle.

    python -m ostraka.reports.make_attack PATH/enterprise-attack.json

The bundle is 50 MB; Ostraka needs each technique's name and tactics, the kill-chain order, and which
techniques each group is known to use (the attack flow's expectations, `ostraka/flow.py`), so this keeps
those and nothing else. Revoked and deprecated objects are left out. A group's techniques are the ones it
uses itself, through its software, and through the campaigns attributed to it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ostraka.log import configure, get_logger

log = get_logger(__name__)

OUT = Path(__file__).with_name("attack.json")
ATTRIBUTION = (
    "© 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of "
    "The MITRE Corporation. MITRE ATT&CK® is a registered trademark of The MITRE Corporation. "
    "Terms of use: https://attack.mitre.org/resources/legal-and-branding/terms-of-use/"
)


def _external_id(obj: dict) -> str | None:
    for ref in obj.get("external_references") or []:
        if ref.get("source_name") == "mitre-attack" and ref.get("external_id"):
            return ref["external_id"]
    return None


def _groups(live: list[dict], techniques: dict[str, dict]) -> dict[str, dict]:
    """Each group's name and techniques: used directly, through software it uses, and through campaigns
    attributed to it (the three routes MITRESaw follows)."""
    ids = {o["id"]: _external_id(o) for o in live}
    kinds = {o["id"]: o.get("type") for o in live}
    uses: dict[str, set[str]] = {}
    attributed: dict[str, set[str]] = {}  # group -> campaigns
    for r in live:
        if r.get("type") != "relationship":
            continue
        src, dst, kind = r.get("source_ref"), r.get("target_ref"), r.get("relationship_type")
        if kind == "uses":
            uses.setdefault(src, set()).add(dst)
        elif kind == "attributed-to" and kinds.get(src) == "campaign" and kinds.get(dst) == "intrusion-set":
            attributed.setdefault(dst, set()).add(src)

    def direct(ref: str) -> set[str]:
        return {
            ids[t]
            for t in uses.get(ref, set())
            if kinds.get(t) == "attack-pattern" and ids.get(t) in techniques
        }

    out: dict[str, dict] = {}
    for o in live:
        if o.get("type") != "intrusion-set" or not (gid := _external_id(o)):
            continue
        found = direct(o["id"])
        for ref in uses.get(o["id"], set()):
            if kinds.get(ref) in ("malware", "tool"):
                found |= direct(ref)
        for campaign in attributed.get(o["id"], set()):
            found |= direct(campaign)
            for ref in uses.get(campaign, set()):
                if kinds.get(ref) in ("malware", "tool"):
                    found |= direct(ref)
        if found:
            out[gid] = {"name": o["name"], "techniques": sorted(found)}
    return dict(sorted(out.items()))


def build(bundle: dict) -> dict:
    """The lookup from a parsed STIX bundle: techniques by ID, `_tactics` in kill-chain order, the version."""
    objects = bundle.get("objects") or []
    live = [o for o in objects if not o.get("revoked") and not o.get("x_mitre_deprecated")]
    by_ref = {o["id"]: o for o in live if o.get("type") == "x-mitre-tactic"}
    matrix = next((o for o in live if o.get("type") == "x-mitre-matrix"), None)
    collection = next((o for o in objects if o.get("type") == "x-mitre-collection"), None)

    refs = matrix.get("tactic_refs", []) if matrix else list(by_ref)
    tactics = [
        {"shortname": by_ref[r]["x_mitre_shortname"], "name": by_ref[r]["name"]} for r in refs if r in by_ref
    ]

    techniques: dict[str, dict] = {}
    for o in live:
        if o.get("type") != "attack-pattern":
            continue
        tid = _external_id(o)
        if not tid:
            continue
        phases = [
            p["phase_name"]
            for p in o.get("kill_chain_phases") or []
            if p.get("kill_chain_name") == "mitre-attack"
        ]
        techniques[tid] = {"name": o["name"], "tactics": phases}

    groups = _groups(live, techniques)
    return {
        "_version": collection.get("x_mitre_version") if collection else None,
        "_modified": collection.get("modified") if collection else None,
        "_source": "MITRE ATT&CK Enterprise (enterprise-attack STIX 2.1 bundle)",
        "_terms": ATTRIBUTION,
        "_tactics": tactics,
        "_groups": groups,
        **dict(sorted(techniques.items())),
    }


def main(argv: list[str] | None = None) -> None:
    """Read the bundle at PATH and write `attack.json` beside this module."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="enterprise-attack.json")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    configure("INFO")
    data = build(json.loads(args.bundle.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    count = sum(1 for k in data if not k.startswith("_"))
    log.info(
        f"Wrote {count} techniques, {len(data['_tactics'])} tactics, ATT&CK v{data['_version']} to {args.out}"
    )


if __name__ == "__main__":
    main()
