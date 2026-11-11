"""Hashing and encryption: passwords (Argon2id), tokens (SHA-256), TOTP secrets (Fernet)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ostraka.config import get_settings

_hasher = PasswordHasher()
# Verified against when an email isn't known, so a miss takes as long as a wrong password
_DUMMY_HASH = _hasher.hash("ostraka-timing-equaliser")

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_problem(password: str, email: str = "", name: str = "") -> str | None:
    """Why a password isn't acceptable, or None. Length matters most; there are no composition rules."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    lowered = password.lower()
    for part in (email.split("@")[0], email, name):
        if part and len(part) >= 4 and part.lower() in lowered:
            return "Don't include your name or email address."
    if len(set(password)) < 5:
        return "Use a less repetitive password."
    return None


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Tokens are long and random, so a fast hash is enough; a database leak can't replay them."""
    return hashlib.sha256(token.encode()).hexdigest()


_BACKUP_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1IL")


def new_backup_code() -> str:
    raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def normalise_backup_code(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{raw[:5]}-{raw[5:]}"


@lru_cache
def _fernet() -> Fernet:
    # The label predates the rename and stays: changing it would make every stored TOTP secret unreadable
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ciphr totp secrets").derive(
        get_settings().secret_key.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
