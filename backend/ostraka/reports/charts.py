"""The report palette, brand images and figures, drawn with Pillow so reports need no plotting library.

Every function returns PNG bytes drawn at about 270 dpi for a 17 cm figure. Text is Inter and indicators
JetBrains Mono, both bundled under `fonts/`, so a figure looks the same on any machine.

Colours come from the theme in force (`theme.pal()`): the light Ostrakon palette, or a customer's brand.
Severity colours are the fixed light-mode `--sev-*` values and never change with either.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ostraka import branding
from ostraka.reports import attack
from ostraka.reports.theme import OSTRAKON, contrast, current, pal

RGB = tuple[int, int, int]

HERE = Path(__file__).parent
FONTS = HERE / "fonts"
SHERD = HERE / "brand" / "ostrakon-clay.png"

WHITE: RGB = (255, 255, 255)

SEVERITY: dict[str, RGB] = {
    "critical": (180, 35, 24),  # --sev-critical #b42318
    "high": (196, 50, 10),  # --sev-high #c4320a
    "medium": (161, 92, 7),  # --sev-medium #a15c07
    "low": (71, 84, 103),  # --sev-low #475467
}
# A finding's standing. Confirmed is the strongest mark on the page; a lead is ochre, the colour of
# something not yet settled; a refuted one fades to a grey-brown so it reads as set aside.
STATUS: dict[str, RGB] = {
    "confirmed": OSTRAKON.ink,
    "lead": (201, 138, 32),  # #c98a20
    "refuted": (168, 155, 142),  # #a89b8e
}
OK: RGB = (6, 118, 71)  # --ok #067647

_FONT_FILES = {
    "regular": "Inter-Regular.ttf",
    "italic": "Inter-Italic.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
    "bolditalic": "Inter-BoldItalic.ttf",
    "mono": "JetBrainsMono-Regular.ttf",
    "monobold": "JetBrainsMono-Bold.ttf",
}


def font_path(style: str = "regular") -> Path:
    """The bundled TTF for a style: regular, italic, semibold, bold, bolditalic, mono or monobold."""
    return FONTS / _FONT_FILES[style]


@cache
def _font(size: int, style: str = "regular") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(style)), size)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _jpeg(img: Image.Image) -> bytes:
    """A full-page ground: no transparency, so JPEG at a quality nobody can tell from lossless,
    at a tenth of the size."""
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


def ideal_ink(fill: RGB) -> RGB:
    """Ink or paper, whichever has the higher WCAG contrast on the fill."""
    ink, paper = pal().ink, pal().paper
    return ink if contrast(fill, ink) >= contrast(fill, paper) else paper


def _text_w(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    return int(d.textlength(text, font=font))


def _wrap(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if _text_w(d, trial, font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line] if line else lines


# ── Brand images ──────────────────────────────────────────────────────────────


@cache
def _sherd() -> Image.Image:
    img = Image.open(SHERD).convert("RGBA")
    return img.crop(img.getbbox())


def _faded(img: Image.Image, opacity: float) -> Image.Image:
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * opacity)))
    return out


def title_background(width_px: int = 1654, height_px: int = 2339) -> bytes:
    """A4 portrait at 200 dpi: limestone, the sherd large and faded running off the bottom right,
    and a clay bar down the left edge. A customer's brand gets the wash and the bar in its own colours,
    without the sherd."""
    page = Image.new("RGB", (width_px, height_px), pal().limestone)

    # A faint wash from paper (top left, behind the title) to limestone (bottom right).
    wash = Image.linear_gradient("L").resize((width_px, height_px))
    page = Image.composite(
        Image.new("RGB", page.size, pal().limestone), Image.new("RGB", page.size, pal().paper), wash
    )
    if not current().ostrakon:
        ImageDraw.Draw(page).rectangle([0, 0, int(width_px * 0.018), height_px], fill=pal().clay)
        return _jpeg(page)

    sherd = _sherd()
    scale = (width_px * 1.15) / sherd.width
    sherd = sherd.resize((int(sherd.width * scale), int(sherd.height * scale)), Image.LANCZOS)
    sherd = sherd.rotate(-9, resample=Image.BICUBIC, expand=True)
    # Strongest at the bottom right, fading out towards the title.
    ramp = Image.linear_gradient("L").rotate(90).transpose(Image.FLIP_LEFT_RIGHT).resize(sherd.size)
    ramp = Image.blend(ramp, Image.linear_gradient("L").resize(sherd.size), 0.5)
    alpha = Image.composite(ramp, Image.new("L", sherd.size, 0), sherd.getchannel("A"))
    sherd.putalpha(alpha.point(lambda v: int(v * 0.14)))
    page.paste(sherd, (width_px - int(sherd.width * 0.74), height_px - int(sherd.height * 0.72)), sherd)

    # A paper veil keeps the title block and the TLP block at the foot on a clean ground.
    veil = Image.new("L", page.size, 0)
    vd = ImageDraw.Draw(veil)
    vd.rectangle([0, 0, int(width_px * 0.66), int(height_px * 0.62)], fill=170)
    vd.rectangle([0, int(height_px * 0.82), int(width_px * 0.62), height_px], fill=200)
    veil = veil.filter(ImageFilter.GaussianBlur(width_px // 14))
    page = Image.composite(Image.new("RGB", page.size, pal().paper), page, veil)

    ImageDraw.Draw(page).rectangle([0, 0, int(width_px * 0.018), height_px], fill=pal().clay)
    return _jpeg(page)


def back_background(width_px: int = 1654, height_px: int = 2339) -> bytes:
    """The back cover: limestone, with the sherd full strength above the centre. A customer's brand
    gets its own logo there instead."""
    page = Image.new("RGB", (width_px, height_px), pal().limestone)
    if not current().ostrakon:
        if logo := current().cover_logo:
            mark = Image.open(io.BytesIO(logo)).convert("RGBA")
            mark.thumbnail((int(width_px * 0.5), int(height_px * 0.14)), Image.LANCZOS)
            page.paste(mark, ((width_px - mark.width) // 2, int(height_px * 0.47) - mark.height), mark)
        return _jpeg(page)
    sherd = _sherd()
    w = int(width_px * 0.46)
    sherd = sherd.resize((w, int(sherd.height * w / sherd.width)), Image.LANCZOS)
    page.paste(sherd, ((width_px - w) // 2, int(height_px * 0.47) - sherd.height), sherd)
    return _jpeg(page)


def wordmark(height_px: int = 160, colour: RGB | None = None) -> bytes:
    """The clay sherd small beside the name in Inter, lower case, on a transparent ground."""
    colour = colour or pal().ink
    sherd = _sherd()
    sh = height_px
    sherd = sherd.resize((int(sherd.width * sh / sherd.height), sh), Image.LANCZOS)
    font = _font(int(height_px * 0.78), "semibold")
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    gap = int(height_px * 0.28)
    img = Image.new(
        "RGBA", (sherd.width + gap + _text_w(probe, branding.NAME, font) + 8, height_px), (0, 0, 0, 0)
    )
    img.paste(sherd, (0, 0), sherd)
    # Centre the x-height on the sherd, not the ascenders: lower case reads centred that way.
    x_height = -probe.textbbox((0, 0), "x", font=font, anchor="ls")[1]
    baseline = (height_px + x_height) / 2
    ImageDraw.Draw(img).text(
        (sherd.width + gap, baseline), branding.NAME, font=font, fill=colour, anchor="ls"
    )
    return _png(img.crop(img.getbbox()))


# ── Charts ────────────────────────────────────────────────────────────────────


def headline_cards(cards: Sequence[tuple[str, str, RGB]], width: int = 1800) -> bytes:
    """Headline numbers as coloured cards: [(value, label, fill), ...]."""
    n = max(len(cards), 1)
    gap, h = 28, 300
    cw = (width - gap * (n - 1)) // n
    img = Image.new("RGB", (width, h), WHITE)
    d = ImageDraw.Draw(img)
    for i, (value, label, fill) in enumerate(cards):
        x0 = i * (cw + gap)
        d.rounded_rectangle([x0, 0, x0 + cw, h], radius=22, fill=fill)
        ink = ideal_ink(fill)
        size = 116
        while size > 50 and _text_w(d, value, _font(size, "bold")) > cw - 40:
            size -= 4
        d.text((x0 + cw / 2, 128), value, font=_font(size, "bold"), fill=ink, anchor="mm")
        lf = _font(36, "semibold")
        lines = _wrap(d, label, lf, cw - 40)[:2]
        y = 238 - (len(lines) - 1) * 22
        for line in lines:
            d.text((x0 + cw / 2, y), line, font=lf, fill=ink, anchor="mm")
            y += 44
    return _png(img)


def horizontal_bars(
    bars: Sequence[tuple[str, float, RGB]], total: float | None = None, width: int = 1800
) -> bytes:
    """One bar per row, [(label, value, fill), ...], with the value at its end.

    With a total, each bar is its share of the total and shows the percentage; without, the largest
    bar fills the track.
    """
    row, top, left = 104, 20, 420
    img = Image.new("RGB", (width, top + row * max(len(bars), 1) + 10), WHITE)
    d = ImageDraw.Draw(img)
    peak = total if total else max([v for _, v, _ in bars] + [1])
    span = width - left - 220
    lf = _font(40)
    for i, (label, value, fill) in enumerate(bars):
        y = top + i * row
        mid = y + row / 2
        lines = _wrap(d, label, lf, left - 40)[:2]
        ly = mid - (len(lines) - 1) * 24
        for line in lines:
            d.text((left - 30, ly), line, font=lf, fill=pal().ink, anchor="rm")
            ly += 48
        w = max(int(span * value / peak), 8 if value else 0) if peak else 0
        d.rectangle([left, y + 20, left + span, y + row - 20], fill=pal().limestone)
        if w:
            d.rectangle([left, y + 20, left + w, y + row - 20], fill=fill)
        text = _number(value)
        if total:
            text += f"  ({100 * value / total:.0f}%)"
        d.text((left + w + 20, mid), text, font=_font(42, "semibold"), fill=pal().ink, anchor="lm")
    return _png(img)


def _number(value: float) -> str:
    return f"{value:g}" if float(value).is_integer() else f"{value:.1f}"


def _legend(d: ImageDraw.ImageDraw, items: Sequence[tuple[str, RGB]], y: int, width: int) -> None:
    f = _font(38)
    total = sum(66 + _text_w(d, label, f) + 50 for label, _ in items)
    x = max((width - total) // 2, 0)
    for label, fill in items:
        d.rounded_rectangle([x, y, x + 44, y + 44], radius=8, fill=fill)
        d.text((x + 60, y + 22), label, font=f, fill=pal().ink, anchor="lm")
        x += 66 + _text_w(d, label, f) + 50


def _wrap_centred(d: ImageDraw.ImageDraw, text: str, font, box: tuple[int, int, int, int], fill: RGB) -> None:
    x0, y0, x1, y1 = box
    lines = _wrap(d, text, font, (x1 - x0) - 16)
    lh = font.size * 1.15
    y = (y0 + y1) / 2 - lh * (len(lines) - 1) / 2
    for line in lines:
        d.text(((x0 + x1) / 2, y), line, font=font, fill=fill, anchor="mm")
        y += lh


def _tactic_order(tactics: Sequence[str]) -> list[str]:
    """Tactics in ATT&CK kill-chain order, whether given as shortnames or names; unknown ones last."""
    rank = {}
    for i, (short, name) in enumerate(attack.TACTICS):
        rank[short] = rank[name.lower()] = i
    return sorted(tactics, key=lambda t: rank.get(t.lower(), rank.get(t.lower().replace(" ", "-"), 99)))


def attack_matrix(
    columns: Mapping[str, Sequence[tuple[str, RGB]]],
    legend: Sequence[tuple[str, RGB]] = (),
    width: int = 1800,
    per_band: int = 7,
) -> bytes:
    """An ATT&CK-style matrix: a column per tactic in kill-chain order, each technique a cell in its fill.

    columns: {tactic shortname or name: [(technique ID, fill), ...]}. More than `per_band` tactics wrap
    onto a second band so the IDs stay legible.
    """
    tactics = _tactic_order(list(columns)) or ["No techniques"]
    bands = [tactics[i : i + per_band] for i in range(0, len(tactics), per_band)]
    cols = min(len(tactics), per_band)
    gap, head, cell, cell_gap, band_gap = 14, 130, 74, 10, 50
    cw = (width - gap * (cols - 1)) // cols
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    longest = max([tid for v in columns.values() for tid, _ in v] or ["T0000"], key=len)
    size = 38
    while size > 20 and _text_w(probe, longest, _font(size, "monobold")) > cw - 18:
        size -= 2
    cf = _font(size, "monobold")
    hf = _font(30 if cw < 230 else 34, "semibold")

    heights = [
        head + max([len(columns.get(t, [])) for t in band] + [1]) * (cell + cell_gap) for band in bands
    ]
    legend_h = 110 if legend else 10
    img = Image.new("RGB", (width, sum(heights) + band_gap * (len(bands) - 1) + legend_h), WHITE)
    d = ImageDraw.Draw(img)
    y0 = 0
    for band, bh in zip(bands, heights, strict=True):
        for c, tactic in enumerate(band):
            x = c * (cw + gap)
            d.rectangle([x, y0, x + cw, y0 + head - 16], fill=pal().ink)
            _wrap_centred(d, attack.tactic_name(tactic), hf, (x, y0, x + cw, y0 + head - 16), pal().paper)
            for r, (tid, fill) in enumerate(columns.get(tactic, [])):
                y = y0 + head + r * (cell + cell_gap)
                d.rounded_rectangle([x, y, x + cw, y + cell], radius=10, fill=fill)
                d.text((x + cw / 2, y + cell / 2), tid, font=cf, fill=ideal_ink(fill), anchor="mm")
        y0 += bh + band_gap
    if legend:
        _legend(d, legend, y0 - band_gap + 40, width)
    return _png(img)


def _hours(h: float) -> str:
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} d"


def phase_bar(phases: Sequence[tuple[str, float, RGB]], width: int = 1800) -> bytes:
    """Time spent in each incident phase as one stacked bar, [(phase, hours, fill), ...], with a key
    beneath giving each phase's time and share."""
    total = sum(h for _, h, _ in phases)
    bar_top, bar_h = 70, 130
    key_cols = 3
    key_rows = -(-len(phases) // key_cols)
    img = Image.new("RGB", (width, bar_top + bar_h + 50 + key_rows * 76 + 10), WHITE)
    d = ImageDraw.Draw(img)
    # The total in hours, like the key, with days beside it once it runs past two.
    total_text = f"{total:.1f} h" + (f" ({total / 24:.1f} days)" if total >= 48 else "")
    d.text((0, 30), "Total", font=_font(38), fill=pal().muted, anchor="lm")
    d.text(
        (_text_w(d, "Total ", _font(38)), 30),
        total_text,
        font=_font(38, "semibold"),
        fill=pal().ink,
        anchor="lm",
    )
    d.rounded_rectangle([0, bar_top, width, bar_top + bar_h], radius=14, fill=pal().limestone)
    x = 0.0
    for name, hours, fill in phases:
        if not total or not hours:
            continue
        w = width * hours / total
        d.rectangle([x, bar_top, x + w, bar_top + bar_h], fill=fill)
        ink = ideal_ink(fill)
        label = name.capitalize()
        if w > _text_w(d, label, _font(36, "semibold")) + 24:
            d.text(
                (x + w / 2, bar_top + bar_h / 2 - 22),
                label,
                font=_font(36, "semibold"),
                fill=ink,
                anchor="mm",
            )
            d.text(
                (x + w / 2, bar_top + bar_h / 2 + 24), _hours(hours), font=_font(32), fill=ink, anchor="mm"
            )
        elif w > _text_w(d, _hours(hours), _font(30)) + 12:
            d.text((x + w / 2, bar_top + bar_h / 2), _hours(hours), font=_font(30), fill=ink, anchor="mm")
        x += w
        # A paper hairline between segments so neighbours in similar colours stay apart.
        d.line([x, bar_top, x, bar_top + bar_h], fill=pal().paper, width=4)
    kw = width // key_cols
    for i, (name, hours, fill) in enumerate(phases):
        kx, ky = (i % key_cols) * kw, bar_top + bar_h + 50 + (i // key_cols) * 76
        d.rounded_rectangle([kx, ky, kx + 44, ky + 44], radius=8, fill=fill)
        share = f"{100 * hours / total:.0f}%" if total else "0%"
        d.text((kx + 60, ky + 22), name.capitalize(), font=_font(38), fill=pal().ink, anchor="lm")
        d.text(
            (kx + kw - 40, ky + 22),
            f"{_hours(hours)}  {share}",
            font=_font(36, "semibold"),
            fill=pal().ink,
            anchor="rm",
        )
    return _png(img)


def ach_scores(scores: Sequence[tuple[str, float, bool]], width: int = 1800) -> bytes:
    """Analysis of competing hypotheses: each hypothesis's inconsistency score as a bar, the leading one
    in clay. Scores are zero or below; the least negative leads, so the shortest bar is the strongest."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lf = _font(40, "semibold")
    wrapped = [_wrap(probe, label, lf, width - 40) for label, _, _ in scores]
    bar_h, gap = 64, 46
    heights = [len(lines) * 50 + 14 + bar_h + gap for lines in wrapped]
    img = Image.new("RGB", (width, sum(heights) + 80), WHITE)
    d = ImageDraw.Draw(img)
    worst = max([abs(s) for _, s, _ in scores] + [1])
    span = width - 260
    y = 10
    for (_, score, leading), lines in zip(scores, wrapped, strict=True):
        for line in lines:
            d.text((0, y), line, font=lf, fill=pal().clay if leading else pal().ink, anchor="lt")
            y += 50
        y += 14
        w = max(int(span * abs(score) / worst), 6)
        d.rectangle([0, y, span, y + bar_h], fill=pal().paper, outline=pal().rule, width=2)
        d.rectangle([0, y, w, y + bar_h], fill=pal().clay if leading else pal().quiet)
        value = "0" if score == 0 else f"−{_number(abs(score))}"
        if leading:
            value += "   leading"
        d.text(
            (w + 20, y + bar_h / 2),
            value,
            font=_font(40, "semibold"),
            fill=pal().clay if leading else pal().ink,
            anchor="lm",
        )
        y += bar_h + gap
    d.text(
        (0, y + 10),
        "Inconsistency score: closer to zero is better supported",
        font=_font(34, "italic"),
        fill=pal().muted,
        anchor="lt",
    )
    return _png(img)
