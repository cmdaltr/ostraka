"""The product's name and wording in one place, so nothing else hard-codes it.

The frontend mirrors these values in `frontend/src/brand.ts`; `/api/meta` serves them for anything
that would rather ask.
"""

from __future__ import annotations

NAME = "Ostraka"
TAGLINE = "Incident management and response"
EXPANSION = "Operational Suite for Teams to Respond, Assess, Keep records & take Action"
INCIDENT_PREFIX = "OST"
HUNT_PREFIX = "HNT"
TOKEN_PREFIX = "ost_"  # noqa: S105 (a prefix that marks Ostraka API tokens, not a secret)
DEFAULT_THEME = "cobalt"
THEMES = ("cobalt", "ostrakon", "ledger")
PHASES = ("detect", "scope", "contain", "eradicate", "recover", "learn")
HUNT_PHASES = ("hypothesis", "collect", "analyse", "conclude")


def as_dict() -> dict[str, object]:
    """Branding as served by `/api/meta`."""
    return {
        "name": NAME,
        "tagline": TAGLINE,
        "expansion": EXPANSION,
        "incident_prefix": INCIDENT_PREFIX,
        "hunt_prefix": HUNT_PREFIX,
        "default_theme": DEFAULT_THEME,
        "themes": list(THEMES),
        "phases": list(PHASES),
        "hunt_phases": list(HUNT_PHASES),
    }
