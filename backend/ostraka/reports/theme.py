"""The colours and logos a report is drawn in: the Ostrakon palette, or a customer's brand.

A brand gives one or two colours and up to three logos; everything else is derived here, so a report
stays readable whatever was chosen. The primary colour carries headings, rules and the cover bar, so
it's darkened until text in it passes WCAG AA (4.5:1) on the page; the secondary colour is a second
series in charts, so it's darkened to 3:1, the bar for graphics. The neutrals (ink, paper, table bands,
rules) are greys with a trace of the primary's hue. Severity, TLP and a finding's standing never take
a brand's colours.

The theme in force is a context variable, so it follows a render into `asyncio.to_thread` and two
exports with different brands can't see each other's.
"""

from __future__ import annotations

import colorsys
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

RGB = tuple[int, int, int]

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
TEXT_CONTRAST = 4.5
GRAPHIC_CONTRAST = 3.0


@dataclass(frozen=True)
class Palette:
    """Every colour a report uses apart from the fixed severity, TLP and status colours."""

    ink: RGB  # body text and the strongest marks
    clay: RGB  # the primary: headings, rules, the cover bar, a leading hypothesis
    limestone: RGB  # the page ground behind charts and the covers
    paper: RGB  # the lightest ground, and text on dark fills
    muted: RGB  # secondary text
    rule: RGB  # hairlines and table grids
    clay_soft: RGB  # the primary as a tint
    band: RGB  # alternate table rows
    quiet: RGB  # a second series beside a primary one


# The light Ostrakon theme (`frontend/src/styles/tokens.css`)
OSTRAKON = Palette(
    ink=(51, 36, 27),  # --ink #33241b, iron-gall
    clay=(160, 73, 47),  # --accent #a0492f
    limestone=(241, 237, 228),  # --bg #f1ede4
    paper=(250, 247, 241),  # --surface #faf7f1
    muted=(110, 90, 74),  # --muted #6e5a4a
    rule=(221, 211, 195),  # --line #ddd3c3
    clay_soft=(241, 221, 210),  # --accent-soft #f1ddd2
    band=(247, 244, 238),  # limestone at half strength on white
    quiet=(196, 184, 170),
)


@dataclass(frozen=True)
class Theme:
    """A palette and, for a customer's brand, its logos as PNG. The Ostrakon theme draws the sherd and
    the wordmark instead of logos."""

    palette: Palette = OSTRAKON
    cover_logo: bytes | None = None  # large, top of the title page and the back cover
    header_logo: bytes | None = None  # small, in every page header
    client_logo: bytes | None = None  # beside "Prepared for" on the title page
    licensed_to: str = ""  # the licence's organisation, on the back cover
    ostrakon: bool = True
    notes: tuple[str, ...] = field(default=(), compare=False)


OSTRAKON_THEME = Theme()
_current: ContextVar[Theme] = ContextVar("report_theme", default=OSTRAKON_THEME)


def current() -> Theme:
    return _current.get()


def pal() -> Palette:
    """The palette of the report being drawn."""
    return _current.get().palette


@contextmanager
def use(theme: Theme) -> Iterator[Theme]:
    """Draw everything inside the block in `theme`."""
    token = _current.set(theme)
    try:
        yield theme
    finally:
        _current.reset(token)


# ── Deriving a palette from a brand's colours ──────────────────────────────────


def parse_hex(text: str) -> RGB:
    if not HEX.match(text):
        raise ValueError(f"{text!r} isn't a colour like #1f6feb.")
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def luminance(rgb: RGB) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: RGB, b: RGB) -> float:
    """The WCAG contrast ratio, 1 to 21."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _hls(h: float, lightness: float, s: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h, lightness, s)
    return (round(r * 255), round(g * 255), round(b * 255))


def _darken_until(rgb: RGB, ground: RGB, ratio: float) -> RGB:
    """`rgb` with its lightness lowered just enough to reach `ratio` against `ground`."""
    h, lightness, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    out = rgb
    while contrast(out, ground) < ratio and lightness > 0:
        lightness = max(lightness - 0.01, 0)
        out = _hls(h, lightness, s)
    return out


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def derive(primary: str, secondary: str = "") -> tuple[Palette, list[str]]:
    """A full palette from a brand's colours, and a note for each colour that had to change to stay
    readable."""
    notes: list[str] = []
    given = parse_hex(primary)
    h, _, s = colorsys.rgb_to_hls(*(c / 255 for c in given))
    tint = min(s, 0.3)
    paper = _hls(h, 0.975, tint)

    clay = _darken_until(given, paper, TEXT_CONTRAST)
    if clay != given:
        notes.append(
            f"The primary colour {primary.lower()} is too light for headings on a white page; "
            f"reports use {to_hex(clay)}, the nearest shade that reads clearly."
        )
    if secondary:
        second = parse_hex(secondary)
        quiet = _darken_until(second, paper, GRAPHIC_CONTRAST)
        if quiet != second:
            notes.append(
                f"The secondary colour {secondary.lower()} is too faint for chart bars; "
                f"reports use {to_hex(quiet)}."
            )
    else:
        quiet = _hls(h, 0.74, min(s, 0.15))

    palette = Palette(
        ink=_hls(h, 0.14, min(s, 0.35)),
        clay=clay,
        limestone=_hls(h, 0.93, tint * 0.8),
        paper=paper,
        muted=_darken_until(_hls(h, 0.4, min(s, 0.15)), paper, TEXT_CONTRAST),
        rule=_hls(h, 0.84, tint * 0.7),
        clay_soft=_mix(clay, (255, 255, 255), 0.82),
        band=_hls(h, 0.965, tint * 0.7),
        quiet=quiet,
    )
    return palette, notes
