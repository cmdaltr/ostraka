"""Hiding raw indicators from observers who haven't asked to see them.

Observers (leadership) get the briefing view by default. Observable values are withheld, and
indicators written into free text (comments, statements, queries) are replaced with their type, so a
briefing reads "[ip address]" rather than the address itself.
"""

from __future__ import annotations

import re

from ostraka.models import Role, User
from ostraka.observables.detect import ObservableType, detect_many

# Free-text kinds that would mask ordinary words; only these structured kinds are hidden in prose
_MASKED_KINDS = frozenset(
    {
        ObservableType.IP_ADDRESS,
        ObservableType.CIDR,
        ObservableType.DOMAIN,
        ObservableType.URL,
        ObservableType.EMAIL,
        ObservableType.FILE_HASH_MD5,
        ObservableType.FILE_HASH_SHA1,
        ObservableType.FILE_HASH_SHA256,
        ObservableType.FILE_PATH,
        ObservableType.REGISTRY_KEY,
        ObservableType.MUTEX,
        ObservableType.BITCOIN_ADDRESS,
        ObservableType.MAC_ADDRESS,
        ObservableType.PROCESS_NAME,
        ObservableType.FILE_NAME,
    }
)


def hides_iocs(user: User) -> bool:
    return user.role is Role.OBSERVER and not user.show_raw_iocs


def mask_text(text: str) -> str:
    """Replace every confidently detected indicator in `text` with its type in brackets."""
    if not text:
        return text
    masked = text
    for found in detect_many(text):
        if found.kind not in _MASKED_KINDS:
            continue
        label = f"[{found.kind.value.replace('_', ' ')}]"
        # Match the value as written, defanged or not
        masked = re.sub(re.escape(found.value), label, masked, flags=re.IGNORECASE)
    for defanged in re.findall(r"\S*(?:\[\.\]|hxxp|\[at\]|\[@\])\S*", masked, flags=re.IGNORECASE):
        masked = masked.replace(defanged, "[indicator]")
    return masked


def for_viewer(user: User, text: str) -> str:
    return mask_text(text) if hides_iocs(user) else text
