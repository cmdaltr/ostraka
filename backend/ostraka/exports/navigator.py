"""An incident's techniques as an ATT&CK Navigator layer.

Built to Rowanholt's layer (rowanholt post/mitre/layers.py `build_layer`, and the confirmed-findings
layer in web/backend/case_routes.py `navigator_layer`), so a layer from Ostraka opens beside one from
Rowanholt and reads the same: layer format 4.5 for Navigator 5.3.2, the ATT&CK major version of the
bundled data, Enterprise with every platform it has (cloud included, or cloud-only techniques would
be filtered out of sight), colour and comment per technique with no score, IDs shown, and the
confirmed colour Rowanholt uses. Shikari's assessment layer hard-codes ATT&CK 17, so it isn't
followed. Technique IDs the bundled ATT&CK release doesn't have are left out, as in Rowanholt.

By default the layer is the confirmed findings' techniques only, as Rowanholt's is: a layer is often
shared as "what happened", and a lead on it would read as established. With `leads=True`, techniques
named only by leads are added in a separate colour, labelled in the legend and in each comment, so
the matrix can show what's still being looked at without passing it off as confirmed.

Each technique's comment names the findings behind it ("FND-12 Payroll export staged (confirmed)")
and its links open each finding in Ostraka.
"""

from __future__ import annotations

from typing import Any

from ostraka.exports.record import CONFIRMED, IntelRecord
from ostraka.observables.detect import ObservableType
from ostraka.reports import attack

NAVIGATOR_VERSION = "5.3.2"  # Rowanholt's (its docker-compose.yml's navigator service)
LAYER_VERSION = "4.5"
DOMAIN = "enterprise-attack"
CONFIRMED_COLOUR = "#ed5565"  # Rowanholt's confirmed-finding colour
LEAD_COLOUR = "#ffce54"
COMMENT_LIMIT = 1000  # as Rowanholt cuts them
# Every platform Enterprise's techniques name (Rowanholt's attack_lookup.platforms("enterprise-attack")
# over ATT&CK v20); the bundled attack.json doesn't carry platforms
PLATFORMS = [
    "Containers",
    "ESXi",
    "IaaS",
    "Identity Provider",
    "Linux",
    "Network Devices",
    "Office Suite",
    "PRE",
    "SaaS",
    "Windows",
    "macOS",
]


def attack_major() -> str:
    """The bundled release's major version ("20"), the one Navigator layers name."""
    return (attack.VERSION or "").split(".")[0]


def layer(record: IntelRecord, *, leads: bool = False) -> dict[str, Any]:
    """The layer as a dict (`json.dumps` it to send)."""
    named = record.techniques()
    for o in record.observables:
        # A technique recorded as an observable has no finding behind it, so it's a lead at most
        tid = o.value.strip().upper()
        if o.kind is ObservableType.MITRE_TECHNIQUE and tid not in named:
            named[tid] = []

    techniques: dict[str, tuple[str, str, list[dict[str, str]]]] = {}
    for tid, findings in named.items():
        if not attack.technique(tid)[0]:
            continue
        confirmed = record.technique_standing(tid) == CONFIRMED
        if not confirmed and not leads:
            continue
        shown = findings if leads else [f for f in findings if f.standing == CONFIRMED]
        comment = (
            "; ".join(f"{f.ref} {f.title} ({f.standing})" for f in shown)
            or "Recorded as an observable (lead)"
        )
        links = [{"label": f.ref, "url": f.url} for f in shown if f.url]
        techniques[tid] = (CONFIRMED_COLOUR if confirmed else LEAD_COLOUR, comment[:COMMENT_LIMIT], links)

    parents = {tid.split(".")[0] for tid in techniques if "." in tid}
    legend = [{"label": "Confirmed finding", "color": CONFIRMED_COLOUR}]
    if leads:
        legend.append({"label": "Lead, not confirmed", "color": LEAD_COLOUR})
    return {
        "name": f"{record.ref} ({'confirmed and leads' if leads else 'confirmed'})",
        "versions": {"attack": attack_major(), "navigator": NAVIGATOR_VERSION, "layer": LAYER_VERSION},
        "domain": DOMAIN,
        "description": (
            f"{record.ref}: {record.title}. "
            + (
                "Techniques behind confirmed findings, and leads in their own colour"
                if leads
                else "Techniques behind the analysts' confirmed findings only"
            )
            + f". TLP:{record.tlp.value.upper()}."
        ),
        "filters": {"platforms": PLATFORMS},
        "sorting": 0,
        "layout": {
            "layout": "side",
            "aggregateFunction": "average",
            "showID": True,
            "showName": True,
            "showAggregateScores": False,
            "countUnscored": False,
        },
        "hideDisabled": False,
        "techniques": [
            {
                "techniqueID": tid,
                "color": colour,
                "comment": comment,
                "enabled": True,
                "metadata": [],
                "links": links,
                "showSubtechniques": tid in parents,
            }
            for tid, (colour, comment, links) in sorted(techniques.items())
        ],
        "gradient": {"colors": ["#ff6666ff", "#ffe766ff", "#8ec843ff"], "minValue": 0, "maxValue": 100},
        "legendItems": legend,
        "metadata": [
            {"name": "TLP", "value": f"TLP:{record.tlp.value.upper()}"},
            {"name": "Incident", "value": record.ref},
            {"name": "Source", "value": record.url},
        ],
        "links": [],
        "showTacticRowBackground": False,
        "tacticRowBackground": "#dddddd",
        "selectTechniquesAcrossTactics": True,
        "selectSubtechniquesWithParent": False,
    }
