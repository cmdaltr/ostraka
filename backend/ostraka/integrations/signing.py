"""Receiving the suite's signed webhooks: pure functions, standard library only, with no Ostraka
imports, so this file can move into a package shared by Ostraka, Shikari and Rowanholt.

Every product sends the same shape (docs/LICENSING_MODEL.md, Webhooks): a POST of
`{"event", "sent_at", "data"}` with `X-<Product>-Event` and, when the webhook has a secret,
`X-<Product>-Signature: sha256=<hex HMAC-SHA256 of the raw body>`. Nothing else is signed: there's no
timestamp or delivery id in the signature, so a receiver must make acting on a delivery harmless to
repeat.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any


def sign(secret: str, body: bytes) -> str:
    """The signature header's value for `body`."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    """Whether `header` is `body` signed with `secret`. An empty secret or header never is."""
    return bool(secret and header) and hmac.compare_digest(sign(secret, body), header.strip())


def headers_for(product: str) -> tuple[str, str]:
    """The event and signature header names a product sends: ("X-Shikari-Event", "X-Shikari-Signature")."""
    name = product.strip().capitalize()
    return f"X-{name}-Event", f"X-{name}-Signature"


class NotADelivery(ValueError):
    """A body that isn't `{"event", "sent_at", "data"}`."""


@dataclass(frozen=True)
class Delivery:
    event: str
    sent_at: Any
    data: dict[str, Any] = field(default_factory=dict)


def parse(body: bytes) -> Delivery:
    """The delivery in a webhook body. Raises NotADelivery."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise NotADelivery("Not JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
        raise NotADelivery("No event.")
    data = payload.get("data")
    return Delivery(payload["event"], payload.get("sent_at"), data if isinstance(data, dict) else {})
