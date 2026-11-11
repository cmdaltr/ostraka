"""Browser push: Web Push message encryption (RFC 8291) and VAPID sign-in (RFC 8292).

The payload is encrypted for the browser, so the push service (Google, Mozilla, Apple) carries it
without being able to read it. The VAPID key pair is derived from OSTRAKA_SECRET_KEY, so there's no
extra secret to manage; changing the secret key invalidates existing subscriptions, which browsers
then renew.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ostraka.config import get_settings

RECORD_SIZE = 4096
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


@lru_cache
def vapid_key() -> ec.EllipticCurvePrivateKey:
    # The label predates the rename and stays: a new key would orphan every browser's push subscription
    seed = HKDF(algorithm=hashes.SHA256(), length=48, salt=None, info=b"ciphr vapid").derive(
        get_settings().secret_key.encode()
    )
    scalar = int.from_bytes(seed, "big") % (_P256_ORDER - 1) + 1
    return ec.derive_private_key(scalar, ec.SECP256R1())


def vapid_public_key() -> str:
    """The application server key a browser subscribes with."""
    return b64url(_public_bytes(vapid_key().public_key()))


def encrypt(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    salt: bytes | None = None,
    sender: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """An aes128gcm body for one browser. `salt` and `sender` are fixed only in tests."""
    salt = salt or os.urandom(16)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(sender.public_key())
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = sender.exchange(ec.ECDH(), receiver)

    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    if len(plaintext) + 1 + 16 > RECORD_SIZE:
        raise ValueError("Push payloads must fit one record")
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)  # 0x02: the last record
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def vapid_header(endpoint: str, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    settings = get_settings()
    subject = settings.public_url if settings.public_url.startswith("https://") else "mailto:ostraka@invalid"
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now or time.time()) + 12 * 3600),
        "sub": subject,
    }
    token = jwt.encode(claims, vapid_key(), algorithm="ES256")
    return f"vapid t={token}, k={vapid_public_key()}"


@dataclass(frozen=True)
class PushRequest:
    url: str
    headers: dict[str, str]
    body: bytes


def build(endpoint: str, p256dh: str, auth: str, payload: bytes, *, urgent: bool = False) -> PushRequest:
    return PushRequest(
        url=endpoint,
        headers={
            "Authorization": vapid_header(endpoint),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(24 * 3600),
            "Urgency": "high" if urgent else "normal",
        },
        body=encrypt(payload, unb64url(p256dh), unb64url(auth)),
    )
