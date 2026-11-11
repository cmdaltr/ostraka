"""Observable type detection: what kind of IOC a pasted value is.

Ported from the original scaffold (`app/services/ioc_detector.py`) with its misclassifications fixed:
file names no longer read as domains, a catch-all "email subject" pattern no longer swallows every
string, IP addresses are parsed rather than pattern-matched, and refanging ignores case.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ObservableType(StrEnum):
    """Types of observable (IOC) ostraka recognises."""

    IP_ADDRESS = "ip_address"
    CIDR = "cidr"
    DOMAIN = "domain"
    URL = "url"
    EMAIL = "email"
    EMAIL_SUBJECT = "email_subject"
    FILE_HASH_MD5 = "file_hash_md5"
    FILE_HASH_SHA1 = "file_hash_sha1"
    FILE_HASH_SHA256 = "file_hash_sha256"
    FILE_NAME = "file_name"
    FILE_PATH = "file_path"
    PROCESS_NAME = "process_name"
    PROCESS_COMMAND = "process_command"
    REGISTRY_KEY = "registry_key"
    SERVICE_NAME = "service_name"
    USER_ACCOUNT = "user_account"
    PHONE_NUMBER = "phone_number"
    ALIAS = "alias"
    CVE = "cve"
    MUTEX = "mutex"
    CERTIFICATE = "certificate"
    BITCOIN_ADDRESS = "bitcoin_address"
    ASN = "asn"
    MAC_ADDRESS = "mac_address"
    USER_AGENT = "user_agent"
    MITRE_TECHNIQUE = "mitre_technique"
    OTHER = "other"


HASH_TYPES = frozenset(
    {ObservableType.FILE_HASH_MD5, ObservableType.FILE_HASH_SHA1, ObservableType.FILE_HASH_SHA256}
)

# Extensions that make "name.ext" a file rather than a domain. Some are also real TLDs (.zip, .mov):
# in an incident a bare "invoice.zip" is far more often the attachment than the domain.
EXECUTABLE_EXTENSIONS = frozenset({"exe", "dll", "sys", "scr", "com", "cpl", "msi", "ocx"})
FILE_EXTENSIONS = EXECUTABLE_EXTENSIONS | frozenset(
    {
        "bat",
        "cmd",
        "ps1",
        "psm1",
        "vbs",
        "vbe",
        "js",
        "jse",
        "hta",
        "wsf",
        "lnk",
        "iso",
        "img",
        "vhd",
        "zip",
        "rar",
        "7z",
        "gz",
        "tar",
        "pdf",
        "doc",
        "docx",
        "docm",
        "xls",
        "xlsx",
        "xlsm",
        "ppt",
        "pptx",
        "rtf",
        "one",
        "txt",
        "log",
        "csv",
        "json",
        "xml",
        "html",
        "htm",
        "py",
        "sh",
        "elf",
        "bin",
        "jar",
        "apk",
        "dmg",
        "pkg",
        "plist",
        "mov",
        "mp4",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "svg",
        "eml",
        "msg",
    }
)

# Only a bare "name.com" is ambiguous; ".com" files are rare enough that a domain wins there.
_DOMAIN_WINS = frozenset({"com"})

_REFANG = [
    (re.compile(r"\[\.\]|\(\.\)|\[dot\]|\(dot\)|\{\.\}", re.IGNORECASE), "."),
    (re.compile(r"\[@\]|\(@\)|\[at\]|\(at\)", re.IGNORECASE), "@"),
    (re.compile(r"\[:\]|\[://\]", re.IGNORECASE), lambda m: m.group(0)[1:-1]),
    (re.compile(r"^hxxp", re.IGNORECASE), "http"),
    (re.compile(r"^fxp", re.IGNORECASE), "ftp"),
]

# (pattern, confidence). Checked in full; the highest confidence wins, ties go to the earlier entry.
_PATTERNS: dict[ObservableType, list[tuple[str, float]]] = {
    ObservableType.URL: [(r"^(?:https?|ftp)://[^\s<>\"{}|\\^`\[\]]+$", 1.0)],
    ObservableType.EMAIL: [(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", 1.0)],
    ObservableType.FILE_HASH_SHA256: [(r"^[a-f0-9]{64}$", 1.0)],
    ObservableType.FILE_HASH_SHA1: [(r"^[a-f0-9]{40}$", 1.0)],
    ObservableType.FILE_HASH_MD5: [(r"^[a-f0-9]{32}$", 1.0)],
    ObservableType.CVE: [(r"^CVE-\d{4}-\d{4,}$", 1.0)],
    ObservableType.MITRE_TECHNIQUE: [(r"^T\d{4}(?:\.\d{3})?$", 1.0)],
    ObservableType.ASN: [(r"^AS\d{1,10}$", 1.0)],
    ObservableType.MAC_ADDRESS: [
        (r"^(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", 1.0),
        (r"^(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}$", 1.0),  # Cisco
    ],
    ObservableType.REGISTRY_KEY: [
        (
            r"^(?:HK(?:LM|CU|CR|U|CC)|HKEY_(?:LOCAL_MACHINE|CURRENT_USER|CLASSES_ROOT|USERS|CURRENT_CONFIG))"
            r"(?:\\[^\\]+)+$",
            1.0,
        ),
    ],
    ObservableType.MUTEX: [(r"^(?:Global|Local)\\\S+$", 0.9)],
    ObservableType.FILE_PATH: [
        (r"^[a-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*$", 1.0),  # Windows
        (r"^\\\\[^\\]+\\[^\\]+(?:\\[^\\]+)*$", 1.0),  # UNC
        (r"^(?:~|/)(?:[^/\0]+/)*[^/\0]*$", 0.8),  # Unix
    ],
    ObservableType.BITCOIN_ADDRESS: [
        (r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$", 0.9),
        (r"^bc1[a-z0-9]{39,59}$", 0.9),
    ],
    ObservableType.DOMAIN: [(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", 0.9)],
    ObservableType.USER_AGENT: [(r"^Mozilla/[0-9.]+\s\([^)]+\)", 0.8)],
    ObservableType.PHONE_NUMBER: [
        (r"^\+[1-9]\d{7,14}$", 0.8),
        (r"^\+?\d{1,3}[\s.-]?\(?\d{2,5}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}$", 0.6),
    ],
    ObservableType.USER_ACCOUNT: [
        (r"^[a-z][a-z0-9._-]*\\[a-z0-9._$-]+$", 0.9),  # DOMAIN\user
        (r"^[a-z][a-z0-9._-]{2,30}$", 0.4),
    ],
}

_COMPILED = {
    kind: [(re.compile(p, re.IGNORECASE), c) for p, c in patterns] for kind, patterns in _PATTERNS.items()
}

# Words in a field label or note that point at a type the value's shape can't settle
CONTEXT_KEYWORDS: dict[ObservableType, tuple[str, ...]] = {
    ObservableType.EMAIL_SUBJECT: ("subject",),
    ObservableType.PROCESS_COMMAND: ("command line", "cmdline", "commandline"),
    ObservableType.PROCESS_NAME: ("process", "executable", "pid"),
    ObservableType.SERVICE_NAME: ("service", "daemon"),
    ObservableType.USER_ACCOUNT: ("user", "account", "username", "login"),
    ObservableType.ALIAS: ("alias", "nickname", "handle", "actor"),
    ObservableType.FILE_PATH: ("path", "directory", "folder"),
}


@dataclass(frozen=True)
class Detection:
    """What a value was classified as, and how sure the detector is."""

    kind: ObservableType
    confidence: float
    value: str
    metadata: dict[str, Any] = field(default_factory=dict)


def refang(value: str) -> str:
    """Undo common defanging (``hxxp``, ``[.]``, ``[at]``) and trim whitespace."""
    out = value.strip()
    for pattern, repl in _REFANG:
        out = pattern.sub(repl, out)
    return out


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    if "/" not in value:
        return None
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def _extension(value: str) -> str | None:
    if "." not in value or "/" in value or "\\" in value:
        return None
    return value.rsplit(".", 1)[1].lower()


def _metadata(kind: ObservableType, value: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if kind is ObservableType.IP_ADDRESS and (ip := _ip(value)):
        meta["ip_version"] = ip.version
        meta["scope"] = (
            "private"
            if ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
            else "loopback"
            if ip.is_loopback
            else "link_local"
            if ip.is_link_local
            else "public"
        )
    elif kind is ObservableType.URL:
        if m := re.match(r"^[a-z]+://([^/:?#]+)", value, re.IGNORECASE):
            meta["host"] = m.group(1).lower()
    elif kind is ObservableType.EMAIL:
        meta["domain"] = value.rsplit("@", 1)[1].lower()
    elif kind is ObservableType.FILE_PATH:
        meta["platform"] = "windows" if "\\" in value else "unix"
    return meta


def _context_kind(context: str | None) -> ObservableType | None:
    if not context:
        return None
    lowered = context.lower()
    for kind, words in CONTEXT_KEYWORDS.items():
        if any(w in lowered for w in words):
            return kind
    return None


def detect(raw: str, context: str | None = None) -> Detection:
    """Classify one value. ``context`` is a field label or note that can tip an ambiguous value."""
    value = refang(raw)
    hint = _context_kind(context)

    if _ip(value):
        return Detection(ObservableType.IP_ADDRESS, 1.0, value, _metadata(ObservableType.IP_ADDRESS, value))
    if _network(value):
        return Detection(ObservableType.CIDR, 1.0, value)

    ext = _extension(value)
    if ext in FILE_EXTENSIONS and ext not in _DOMAIN_WINS and " " not in value:
        kind = ObservableType.PROCESS_NAME if ext in EXECUTABLE_EXTENSIONS else ObservableType.FILE_NAME
        return Detection(kind, 0.9, value)

    best: Detection | None = None
    for kind, patterns in _COMPILED.items():
        for pattern, confidence in patterns:
            if not pattern.match(value):
                continue
            if hint is kind:
                confidence = min(1.0, confidence + 0.2)
            if best is None or confidence > best.confidence:
                best = Detection(kind, confidence, value, _metadata(kind, value))

    if best and best.confidence >= 0.8:
        return best
    if hint:
        return Detection(hint, 0.6, value, {"detection_method": "context"})
    if best:
        return best
    if ext and " " not in value:
        return Detection(ObservableType.FILE_NAME, 0.5, value)
    return Detection(ObservableType.OTHER, 0.0, value)


_TOKEN_SPLIT = re.compile(r"[\s,;|]+")
_WRAPPERS = "()[]{}'\"<>.:"  # brackets, quotes and sentence punctuation around a token


def detect_many(text: str, min_confidence: float = 0.8) -> list[Detection]:
    """Pull every confident observable out of a pasted block of text, first occurrence only."""
    seen: set[str] = set()
    found: list[Detection] = []
    for token in _TOKEN_SPLIT.split(refang(text)):
        token = token.strip(_WRAPPERS)
        if len(token) < 3 or token.lower() in seen:
            continue
        seen.add(token.lower())
        result = detect(token)
        if result.kind is not ObservableType.OTHER and result.confidence >= min_confidence:
            found.append(result)
    return found
