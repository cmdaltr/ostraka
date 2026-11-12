"""An incident's techniques as an ATT&CK Navigator layer.

Built by `samarkand.navigator.build_layer`, which is Rowanholt's layer (rowanholt post/mitre/
layers.py), so a layer from Ostraka opens beside one from Rowanholt and reads the same: layer
format 4.5 for Navigator 5.3.2, the ATT&CK major version of the bundled data, Enterprise with every
platform it has (cloud included, or cloud-only techniques would be filtered out of sight), colour
and comment per technique with no score, IDs shown, and the confirmed colour Rowanholt uses.
Technique IDs the bundled ATT&CK release doesn't have are left out, as in Rowanholt.

By default the layer is the confirmed findings' techniques only, as Rowanholt's is: a layer is often
shared as "what happened", and a lead on it would read as established. With `leads=True`, techniques
named only by leads are added in a separate colour, labelled in the legend and in each comment, so
the matrix can show what's still being looked at without passing it off as confirmed.

Each technique's comment names the findings behind it ("FND-12 Payroll export staged (confirmed)")
and its links open each finding in Ostraka.
"""

from __future__ import annotations

from typing import Any

from samarkand import navigator as shared
from samarkand.navigator import (  # noqa: F401  (re-exported for the API and tests)
    COMMENT_LIMIT,
    CONFIRMED_COLOUR,
    LAYER_VERSION,
    LEAD_COLOUR,
    NAVIGATOR_VERSION,
)

from ostraka.exports.record import CONFIRMED, IntelRecord
from ostraka.observables.detect import ObservableType
from ostraka.reports import attack

DOMAIN = shared.ENTERPRISE


def attack_major() -> str:
    """The bundled release's major version ("20"), the one Navigator layers name."""
    return shared.attack_major(attack.VERSION or "")


def layer(record: IntelRecord, *, leads: bool = False) -> dict[str, Any]:
    """The layer as a dict (`json.dumps` it to send)."""
    named = record.techniques()
    for o in record.observables:
        # A technique recorded as an observable has no finding behind it, so it's a lead at most
        tid = o.value.strip().upper()
        if o.kind is ObservableType.MITRE_TECHNIQUE and tid not in named:
            named[tid] = []

    techniques: dict[str, shared.Mark] = {}
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
        techniques[tid] = shared.Mark(CONFIRMED_COLOUR if confirmed else LEAD_COLOUR, comment, links)

    legend = [{"label": "Confirmed finding", "color": CONFIRMED_COLOUR}]
    if leads:
        legend.append({"label": "Lead, not confirmed", "color": LEAD_COLOUR})
    return shared.build_layer(
        f"{record.ref} ({'confirmed and leads' if leads else 'confirmed'})",
        DOMAIN,
        techniques,
        attack_version=attack.VERSION or "",
        # The bundled attack.json carries no platforms, so the layer names every one ATT&CK 20.1's
        # Enterprise techniques do (samarkand's list, the same as Rowanholt reads from its data)
        platforms=list(shared.PLATFORMS[DOMAIN]),
        description=(
            f"{record.ref}: {record.title}. "
            + (
                "Techniques behind confirmed findings, and leads in their own colour"
                if leads
                else "Techniques behind the analysts' confirmed findings only"
            )
            + f". TLP:{record.tlp.value.upper()}."
        ),
        legend=legend,
        show_ids=True,
        metadata=[
            {"name": "TLP", "value": f"TLP:{record.tlp.value.upper()}"},
            {"name": "Incident", "value": record.ref},
            {"name": "Source", "value": record.url},
        ],
        links=[],
    )
