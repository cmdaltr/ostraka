import pytest

from ostraka.observables.detect import ObservableType as T
from ostraka.observables.detect import detect, detect_many, refang


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("185.220.101.47", T.IP_ADDRESS),
        ("2001:db8::1", T.IP_ADDRESS),
        ("10.0.0.0/8", T.CIDR),
        ("evil-cdn.net", T.DOMAIN),
        ("login.microsoftonline.com", T.DOMAIN),
        ("https://evil.example/payload.bin", T.URL),
        ("a.user@contoso.co.uk", T.EMAIL),
        ("d41d8cd98f00b204e9800998ecf8427e", T.FILE_HASH_MD5),
        ("da39a3ee5e6b4b0d3255bfef95601890afd80709", T.FILE_HASH_SHA1),
        ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", T.FILE_HASH_SHA256),
        ("invoice.pdf", T.FILE_NAME),
        ("Q3_report.docm", T.FILE_NAME),
        ("update.exe", T.PROCESS_NAME),
        ("rundll32.dll", T.PROCESS_NAME),
        (r"C:\Users\Public\svc.ps1", T.FILE_PATH),
        (r"\\fs01\share\drop", T.FILE_PATH),
        ("/var/tmp/.x/kworker", T.FILE_PATH),  # noqa: S108
        (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", T.REGISTRY_KEY),
        (r"Global\MsWinZonesCacheCounterMutexA", T.MUTEX),
        ("CVE-2024-3400", T.CVE),
        ("T1114.003", T.MITRE_TECHNIQUE),
        ("AS13335", T.ASN),
        ("00:1A:2B:3C:4D:5E", T.MAC_ADDRESS),
        (r"CONTOSO\svc_backup", T.USER_ACCOUNT),
        ("+447700900123", T.PHONE_NUMBER),
        ("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", T.BITCOIN_ADDRESS),
    ],
)
def test_detects_kind(value, kind):
    assert detect(value).kind is kind


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hxxps://evil[.]example/x", "https://evil.example/x"),
        ("HXXP://evil(dot)example", "http://evil.example"),
        ("a.user[at]contoso[.]com", "a.user@contoso.com"),
        ("  185[.]220[.]101[.]47 ", "185.220.101.47"),
    ],
)
def test_refang(value, expected):
    assert refang(value) == expected


def test_defanged_url_is_a_url():
    d = detect("hxxps://evil[.]example/stage2")
    assert d.kind is T.URL
    assert d.metadata["host"] == "evil.example"


def test_free_text_is_not_an_email_subject():
    assert detect("the quick brown fox").kind is T.OTHER


def test_context_tips_an_ambiguous_value():
    assert detect("Urgent: invoice overdue", context="Email subject").kind is T.EMAIL_SUBJECT
    assert detect("powershell -enc SQBFAFgA", context="Command line").kind is T.PROCESS_COMMAND


@pytest.mark.parametrize(
    ("ip", "scope"),
    [
        ("10.1.2.3", "private"),
        ("100.101.102.103", "private"),
        ("8.8.8.8", "public"),
        ("169.254.1.1", "private"),
    ],
)
def test_ip_scope(ip, scope):
    assert detect(ip).metadata["scope"] == scope


def test_detect_many_pulls_confident_observables_once():
    text = """
    Beacon to hxxps://evil[.]example/gate.php from FIN-WS-022 (10.20.1.22).
    Dropped C:\\ProgramData\\upd.exe, sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855;
    same e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 seen twice. Technique T1071.001.
    """
    kinds = [(d.kind, d.value) for d in detect_many(text)]
    assert (T.URL, "https://evil.example/gate.php") in kinds
    assert (T.IP_ADDRESS, "10.20.1.22") in kinds
    assert (T.FILE_PATH, "C:\\ProgramData\\upd.exe") in kinds
    assert (T.MITRE_TECHNIQUE, "T1071.001") in kinds
    assert sum(1 for k, _ in kinds if k is T.FILE_HASH_SHA256) == 1
    assert all(k is not T.OTHER for k, _ in kinds)
