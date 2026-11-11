"""The licence, the seat rule, brand palettes and logo cleaning, without a database."""

import base64
import io
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image

from ostraka import licence
from ostraka.auth import policy
from ostraka.models import Role, User
from ostraka.reports.brands import LogoProblem, clean_logo
from ostraka.reports.theme import GRAPHIC_CONTRAST, TEXT_CONTRAST, contrast, derive

TODAY = date(2026, 11, 11)


def lic(edition="pro", seats=10, expires=date(2027, 11, 30), **extra) -> licence.Licence:
    return licence.Licence(
        "lic_test", "Northwind Ltd", licence.Edition(edition), seats, TODAY, expires, **extra
    )


# ---- signing and checking ----------------------------------------------------------------------


def test_a_signed_licence_reads_back_exactly(signing_key):
    original = lic(
        "services",
        seats=None,
        expires=None,
        email="ops@northwind.example",
        features=("*",),
        not_before=date(2026, 12, 1),
    )
    assert licence.parse(licence.sign(signing_key, original)) == original


def test_the_payload_is_canonical_json(signing_key):
    _, payload, _ = licence.sign(signing_key, lic()).split(".")
    text = base64.urlsafe_b64decode(payload + "==").decode()
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    assert json.loads(text)["product"] == "ostraka"


def test_a_licence_for_another_product_is_refused(signing_key):
    with pytest.raises(licence.LicenceProblem, match="licence for shikari"):
        licence.parse(licence.sign(signing_key, lic(product="shikari")))


def test_an_edited_licence_is_refused(signing_key):
    head, payload, signature = licence.sign(signing_key, lic(seats=10)).split(".")
    more = json.loads(base64.urlsafe_b64decode(payload + "=="))
    more["seats"] = 500
    forged = base64.urlsafe_b64encode(json.dumps(more).encode()).rstrip(b"=").decode()
    with pytest.raises(licence.LicenceProblem, match="altered"):
        licence.parse(f"{head}.{forged}.{signature}")


def test_a_licence_signed_by_another_key_is_refused(signing_key):
    with pytest.raises(licence.LicenceProblem, match="wasn't issued for Ostraka"):
        licence.parse(licence.sign(Ed25519PrivateKey.generate(), lic()))


@pytest.mark.parametrize("text", ["", "hello", "ostraka-licence-1.!!!.???", "ostraka-licence-2.a.b"])
def test_text_that_isnt_a_licence_is_refused(signing_key, text):
    with pytest.raises(licence.LicenceProblem):
        licence.parse(text)


def test_a_free_licence_grants_nothing(signing_key):
    with pytest.raises(licence.LicenceProblem, match="nothing beyond"):
        licence.parse(licence.sign(signing_key, lic("free")))


def test_the_shipped_key_is_a_real_ed25519_public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    for key in licence.PUBLIC_KEYS:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key))


# ---- what a licence gives, and when it runs out -------------------------------------------------


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def test_no_licence_is_the_free_edition(monkeypatch):
    monkeypatch.setattr(licence, "FREE_SEATS", 4)
    s = licence.standing(None)
    assert (s.edition, s.seats, s.state) == (licence.Edition.FREE, 4, "free")
    assert not s.custom_branding and not s.brand_library


def test_a_licence_runs_to_the_end_of_its_last_day_then_has_thirty_days_grace(monkeypatch):
    monkeypatch.setattr(licence, "FREE_SEATS", 4)
    l = lic("services", seats=25, expires=date(2027, 1, 31))  # noqa: E741
    active = licence.standing(l, at(date(2027, 1, 31)))
    assert (active.state, active.seats, active.brand_library) == ("active", 25, True)
    grace = licence.standing(l, at(date(2027, 2, 1)))
    assert (grace.state, grace.edition, grace.seats) == ("grace", licence.Edition.SERVICES, 25)
    assert licence.standing(l, at(date(2027, 3, 2))).state == "grace"
    lapsed = licence.standing(l, at(date(2027, 3, 3)))
    assert (lapsed.state, lapsed.edition, lapsed.seats, lapsed.organisation) == (
        "lapsed",
        licence.Edition.FREE,
        4,
        "",
    )


def test_pro_brands_reports_but_only_services_keeps_a_library():
    pro = licence.standing(lic("pro"), at(TODAY))
    assert pro.custom_branding and not pro.brand_library
    assert pro.organisation == "Northwind Ltd"


# ---- the seat rule ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("held", "leaders", "role", "refused"),
    [
        (0, 0, Role.RESPONDER, False),
        (2, 1, Role.RESPONDER, False),
        (3, 1, Role.OBSERVER, False),  # the fourth place, with an admin already in
        (4, 1, Role.ADMIN, True),  # full
        (3, 0, Role.RESPONDER, True),  # the last place is kept for someone who can run Ostraka
        (3, 0, Role.TEAM_LEAD, False),
        (3, 0, Role.ADMIN, False),
    ],
)
def test_the_free_edition_has_four_places_and_keeps_one_for_a_leader(held, leaders, role, refused):
    why = policy.seat_refusal(seats=4, held=held, leaders=leaders, role=role, free=True)
    assert (why is not None) == refused


def test_the_refusal_says_how_to_make_room():
    free = policy.seat_refusal(seats=4, held=4, leaders=1, role=Role.RESPONDER, free=True)
    assert free and "free edition is for 4" in free and "install a licence" in free
    paid = policy.seat_refusal(seats=25, held=25, leaders=3, role=Role.RESPONDER, free=False)
    assert paid and "covers 25" in paid and "ask for more seats" in paid


def test_only_admins_manage_the_licence():
    def user(role: Role) -> User:
        return User(id=uuid.uuid4(), role=role, email="x@example.org", name="X")

    assert policy.can_manage_licence(user(Role.ADMIN)) is None
    for role in (Role.TEAM_LEAD, Role.RESPONDER, Role.OBSERVER):
        assert policy.can_manage_licence(user(role))


def test_only_admins_manage_webhooks():
    def user(role: Role) -> User:
        return User(id=uuid.uuid4(), role=role, email="x@example.org", name="X")

    assert policy.can_manage_webhooks(user(Role.ADMIN)) is None
    for role in (Role.TEAM_LEAD, Role.RESPONDER, Role.OBSERVER):
        assert policy.can_manage_webhooks(user(role))


# ---- brand palettes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("primary", "secondary"),
    [
        ("#1f6feb", ""),
        ("#ffeb3b", "#fff59d"),
        ("#ffffff", "#ffffff"),
        ("#000000", ""),
        ("#e91e63", "#00bcd4"),
    ],
)
def test_any_brand_colours_give_a_readable_report(primary, secondary):
    p, _ = derive(primary, secondary)
    for text in (p.ink, p.clay, p.muted):
        assert contrast(text, p.paper) >= TEXT_CONTRAST, text
        assert contrast(text, p.band) >= TEXT_CONTRAST - 0.3, text  # table rows are a shade darker
    assert contrast(p.quiet, p.paper) >= GRAPHIC_CONTRAST or not secondary


def test_a_colour_that_had_to_change_says_so():
    _, notes = derive("#ffeb3b", "#fff59d")
    assert len(notes) == 2 and "#ffeb3b" in notes[0] and "#fff59d" in notes[1]
    assert derive("#1a3d8f")[1] == []


# ---- logos --------------------------------------------------------------------------------------


def png(size=(400, 120), mode="RGBA", fmt="PNG", **save) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (20, 60, 200, 255) if mode == "RGBA" else (20, 60, 200)).save(out, fmt, **save)
    return out.getvalue()


def test_a_logo_is_redrawn_as_a_png_without_its_metadata():
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "<script>alert(1)</script>")
    cleaned = clean_logo(png(pnginfo=info) + b"trailing bytes")
    img = Image.open(io.BytesIO(cleaned))
    assert img.format == "PNG" and img.size == (400, 120) and "Comment" not in img.info
    assert b"trailing" not in cleaned and b"script" not in cleaned


def test_a_jpeg_logo_is_accepted_and_a_big_one_is_scaled_down():
    img = Image.open(io.BytesIO(clean_logo(png((3000, 1000), "RGB", "JPEG"))))
    assert img.format == "PNG" and max(img.size) == 1200


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "PNG or JPEG"),
        (png(fmt="GIF", mode="RGB"), "PNG or JPEG"),
        (png((5000, 5000), "RGB", "JPEG"), "too large"),
        (b"\x89PNG" + b"0" * (3 * 1024 * 1024), "over 2 MB"),
    ],
)
def test_anything_else_is_refused(data, why):
    with pytest.raises(LogoProblem, match=why):
        clean_logo(data)


def test_grace_ends_thirty_days_after_expiry():
    assert licence.grace_ends(lic(expires=date(2027, 1, 31))) == date(2027, 1, 31) + timedelta(days=30)


def test_a_licence_waits_for_its_first_day_and_a_perpetual_one_never_ends(monkeypatch):
    monkeypatch.setattr(licence, "FREE_SEATS", 4)
    later = lic("pro", not_before=date(2026, 12, 1))
    assert licence.standing(later, at(date(2026, 11, 30))).state == "pending"
    assert licence.standing(later, at(date(2026, 11, 30))).seats == 4
    assert licence.standing(later, at(date(2026, 12, 1))).state == "active"
    forever = lic("pro", expires=None)
    assert licence.standing(forever, at(date(2099, 1, 1))).state == "active"
    assert licence.grace_ends(forever) is None


def test_an_edition_grants_its_features_and_a_licence_can_add_more():
    pro = licence.standing(lic("pro"), at(TODAY))
    assert pro.has(licence.REPORT_BRANDING) and not pro.has(licence.BRAND_LIBRARY)
    assert pro.has(licence.AUTOMATION) and not licence.standing(None).has(licence.AUTOMATION)
    plus = licence.standing(lic("pro", features=(licence.BRAND_LIBRARY,)), at(TODAY))
    assert plus.brand_library
    everything = licence.standing(lic("pro", features=("*",)), at(TODAY))
    assert everything.has(licence.MANAGED_SERVICE) and everything.has("anything-later")
    services = licence.standing(lic("services"), at(TODAY))
    assert services.features == {
        licence.REPORT_BRANDING,
        licence.BRAND_LIBRARY,
        licence.AUTOMATION,
        licence.MANAGED_SERVICE,
    }


def test_an_unlimited_licence_never_refuses_a_seat():
    assert policy.seat_refusal(seats=None, held=500, leaders=0, role=Role.RESPONDER, free=False) is None


def test_the_terms_ostraka_ships_are_the_terms_in_the_repo():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "COMMERCIAL-LICENSE.md"
    assert licence.TERMS.read_text() == root.read_text(), "copy COMMERCIAL-LICENSE.md to ostraka/legal/"
    assert licence.terms()[0] == "1.1"


def test_the_code_licence_ostraka_ships_is_the_licence_in_the_repo():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "LICENSE"
    assert licence.CODE_LICENCE.read_text() == root.read_text(), "copy LICENSE to ostraka/legal/"
    assert "Elastic License 2.0" in licence.code_licence()
