"""The branded report shell, in Word and PDF: a limestone title page with the sherd faded across it, the
TLP label, a header and footer on every page after it with page numbers, a table of contents, numbered
sections, the Ostrakon palette, and a back cover.

A report describes its content once, as blocks, and both formats render them:

    ("section", title)                      numbered, on a new page
    ("sub", title)                          numbered within its section
    ("para", text)
    ("bullets", [text, ...])
    ("table", spec, caption, first_col_bold)  spec: (headers, rows, widths in cm, {(row, col): fill})
    ("figure", png, caption, width_cm)
    ("card", title, fill, [(label, value, fill or None), ...])   kept on one page
    ("note", [text, ...])                   a boxed note (the AI disclaimer)
    ("code", text)                          a query or indicators, monospaced, line breaks kept

A trailing "†" marks AI-assisted content. Content code puts it on a section, paragraph or cell, the
contents page carries it with the section's title, and the shell sets each mark in clay.

The PDF embeds the bundled Inter and JetBrains Mono. The Word file names them but can't carry them:
Word substitutes a default font (usually Calibri or Aptos, and Courier New for code) on a machine
without Inter and JetBrains Mono installed, so the PDF is the faithful copy.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from ostraka import branding
from ostraka.log import get_logger
from ostraka.reports import charts
from ostraka.reports import tlp as tlp_mod
from ostraka.reports.charts import SEVERITY, STATUS, ideal_ink  # noqa: F401 (re-exported for report content)
from ostraka.reports.theme import Theme, current, pal

log = get_logger(__name__)

RGB = tuple[int, int, int]
Table = tuple[list[str], list[list[str]], list[float], dict[tuple[int, int], RGB]]

AI_MARK = "†"
TEXT_FONT = "Inter"
MONO_FONT = "JetBrains Mono"


@dataclass
class Cover:
    """What the title page says. `tlp` is a FIRST TLP 2.0 label, shown on the cover and every footer."""

    kind: str  # "Incident Report", "Incident Briefing", "Hunt Report"
    title: str
    reference: str  # "OST-2026-014"
    date: str
    tlp: str
    prepared_for: str = ""
    lines: list[str] = field(default_factory=list)  # under the date: "Incident lead: B. Smith"
    # A briefing: no contents page, and sections run on instead of each starting a page
    compact: bool = False


def _hex(rgb: RGB) -> str:
    return "{:02X}{:02X}{:02X}".format(*rgb)


@lru_cache(maxsize=8)  # the cover art takes about a second to draw and only changes with the brand
def _brand(theme: Theme) -> dict[str, bytes | None]:
    """The theme's images: the cover art, and the marks for the title page, the page header and
    beside "Prepared for". Ostrakon's marks are the wordmark; a customer's are its logos."""
    wordmark = charts.wordmark(160) if theme.ostrakon else None
    return {
        "background": charts.title_background(),
        "back": charts.back_background(),
        "mark": wordmark or theme.cover_logo,
        "header": wordmark or theme.header_logo,
        "client": None if theme.ostrakon else theme.client_logo,
    }


def _fit(png: bytes, max_w: float, max_h: float) -> tuple[float, float]:
    """A logo's size within a box, keeping its proportions (any unit)."""
    from PIL import Image as PILImage

    w, h = PILImage.open(io.BytesIO(png)).size
    scale = min(max_w / w, max_h / h)
    return w * scale, h * scale


def _initials(text: str) -> list[tuple[str, bool]]:
    """The expansion split so each capitalised word's first letter can be set apart: [(text, initial)]."""
    out: list[tuple[str, bool]] = []
    for i, word in enumerate(text.split(" ")):
        lead = " " if i else ""
        if word[:1].isupper():
            out += [(lead, False), (word[0], True), (word[1:], False)]
        else:
            out.append((lead + word, False))
    return [(t, b) for t, b in out if t]


def sections_of(blocks: Sequence[tuple]) -> list[str]:
    """The section titles, in order, as the contents lists them."""
    return [b[1] for b in blocks if b[0] == "section"]


def _footer_who(cover: Cover) -> str:
    return f"Prepared for {cover.prepared_for}" if cover.prepared_for else branding.NAME


def _header_text(cover: Cover) -> str:
    return (
        f"{cover.kind}  ·  {cover.reference}  {cover.title}"
        if cover.reference
        else (f"{cover.kind}  ·  {cover.title}")
    )


# ── Word ──────────────────────────────────────────────────────────────────────


def to_docx(cover: Cover, blocks: Sequence[tuple]) -> bytes:
    """The report as a Word document."""
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    theme = current()
    brand = _brand(theme)
    INK, CLAY, MUTED, RULE, BAND, PAPER = (  # noqa: F841 (the palette, for the helpers below)
        pal().ink,
        pal().clay,
        pal().muted,
        pal().rule,
        pal().band,
        pal().paper,
    )
    label = tlp_mod.normalise(cover.tlp)
    doc = Document()

    def set_fonts(rfonts, name: str) -> None:
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), name)
        # A theme font attribute wins over the named one, so the template's must go.
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
            if rfonts.get(qn(attr)) is not None:
                del rfonts.attrib[qn(attr)]

    rpr = doc.styles.element.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    set_fonts(fonts, TEXT_FONT)

    def style_font(name, size=None, bold=None, colour=None):
        st = doc.styles[name]
        st.font.name = TEXT_FONT
        set_fonts(st.element.rPr.rFonts, TEXT_FONT)
        if size:
            st.font.size = Pt(size)
        if bold is not None:
            st.font.bold = bold
        if colour:
            st.font.color.rgb = RGBColor(*colour)
        return st

    normal = style_font("Normal", 10, colour=INK)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    h1 = style_font("Heading 1", 20, True, INK)
    h1.paragraph_format.space_before = Pt(0)
    h1.paragraph_format.space_after = Pt(10)
    h2 = style_font("Heading 2", 13, True, CLAY)
    h2.paragraph_format.space_before = Pt(14)
    h2.paragraph_format.space_after = Pt(6)
    style_font("Heading 3", 11.5, True, INK)
    for name in ("Caption", "List Bullet", "Title", "Subtitle", "TOC Heading"):
        try:
            style_font(name)
        except KeyError:
            pass
    caption = doc.styles["Caption"]
    caption.font.size = Pt(8.5)
    caption.font.italic = True
    caption.font.bold = False
    caption.font.color.rgb = RGBColor(*MUTED)
    for level in (1, 2):
        try:
            st = doc.styles[f"TOC {level}"]
        except KeyError:
            st = doc.styles.add_style(f"TOC {level}", 1)
            st.base_style = normal
        st.font.name = TEXT_FONT
        st.font.size = Pt(11.5 if level == 1 else 10)
        st.font.bold = level == 1
        st.paragraph_format.left_indent = Cm(0 if level == 1 else 0.8)
        st.paragraph_format.space_before = Pt(8 if level == 1 else 2)
        st.paragraph_format.space_after = Pt(2)

    def add_text(p, text: str, size=None, bold=None, colour=None, mark: RGB | None = CLAY, italic=None):
        """Add text as runs, the AI mark in its own run so it can take the clay."""
        parts = text.split(AI_MARK)
        for i, part in enumerate(parts):
            if part:
                r = p.add_run(part)
                if size:
                    r.font.size = Pt(size)
                if bold is not None:
                    r.font.bold = bold
                if italic is not None:
                    r.font.italic = italic
                if colour:
                    r.font.color.rgb = RGBColor(*colour)
            if i < len(parts) - 1:
                r = p.add_run(AI_MARK)
                if size:
                    r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = RGBColor(*(mark or colour or INK))
        return p

    def border(p, side, colour, size=12, space=4):
        ppr = p._p.get_or_add_pPr()
        bdr = ppr.find(qn("w:pBdr"))
        if bdr is None:
            bdr = OxmlElement("w:pBdr")
            ppr.append(bdr)
        el = OxmlElement(f"w:{side}")
        for k, v in (("val", "single"), ("sz", str(size)), ("space", str(space)), ("color", _hex(colour))):
            el.set(qn(f"w:{k}"), v)
        bdr.append(el)

    def fld(p, code, placeholder="1", size=None, colour=None, bold=False):
        for kind, text in (
            ("begin", None),
            ("instr", code),
            ("separate", None),
            ("text", placeholder),
            ("end", None),
        ):
            run = p.add_run()
            if size:
                run.font.size = Pt(size)
            if colour:
                run.font.color.rgb = RGBColor(*colour)
            run.font.bold = bold
            if kind == "instr":
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = f" {code} "
                run._r.append(el)
            elif kind == "text":
                run.text = text
            else:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), kind)
                run._r.append(el)

    def tlp_badge(p, size):
        """The TLP label as FIRST shows it: its colour on black."""
        r = p.add_run(f" {label} ")
        r.font.size, r.font.bold = Pt(size), True
        r.font.color.rgb = RGBColor(*tlp_mod.colour(label))
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "000000")
        r._r.get_or_add_rPr().append(shd)

    def shade(cell, rgb):
        tcpr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), _hex(rgb))
        tcpr.append(shd)

    def cell_text(cell, text, bold=False, colour=None, size=9, mark: RGB | None = CLAY):
        cell.text = ""
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.line_spacing = 1.05
        add_text(p, text, size=size, bold=bold, colour=colour, mark=mark)

    def grid(t):
        borders = OxmlElement("w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:color"), _hex(RULE))
            borders.append(el)
        t._tbl.tblPr.append(borders)

    def table_width(t, cm_total: float):
        tblpr = t._tbl.tblPr
        for old in tblpr.findall(qn("w:tblW")):
            tblpr.remove(old)
        width = OxmlElement("w:tblW")
        width.set(qn("w:w"), str(int(cm_total * 567)))
        width.set(qn("w:type"), "dxa")
        tblpr.append(width)

    counters = {"figure": 0, "table": 0, "sec": 0, "sub": 0}

    def table(spec: Table, caption_text: str = "", first_col_bold=False):
        headers, rows, widths, fills = spec
        scale = 17.0 / sum(widths)
        widths = [w * scale for w in widths]
        t = doc.add_table(rows=1 + len(rows), cols=len(headers))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        grid(t)
        table_width(t, sum(widths))
        margins = OxmlElement("w:tblCellMar")
        for side, v in (("top", 50), ("bottom", 50), ("left", 90), ("right", 90)):
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:w"), str(v))
            el.set(qn("w:type"), "dxa")
            margins.append(el)
        t._tbl.tblPr.append(margins)
        for j, h in enumerate(headers):
            cell = t.rows[0].cells[j]
            cell_text(cell, h, bold=True, colour=PAPER)
            shade(cell, INK)
            cell.paragraphs[0].paragraph_format.keep_with_next = True  # never alone at the foot of a page
        rep = OxmlElement("w:tblHeader")
        rep.set(qn("w:val"), "true")
        t.rows[0]._tr.get_or_add_trPr().append(rep)
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                cell = t.rows[i + 1].cells[j]
                fill = fills.get((i, j))
                if fill:
                    ink = ideal_ink(fill)
                    cell_text(cell, value, bold=True, colour=ink, mark=ink)
                    shade(cell, fill)
                else:
                    cell_text(cell, value, bold=first_col_bold and j == 0)
                    if i % 2:
                        shade(cell, BAND)
        for row in t.rows:
            for j, w in enumerate(widths):
                row.cells[j].width = Cm(w)
        if caption_text:
            counters["table"] += 1
            cap = doc.add_paragraph(style="Caption")
            add_text(cap, f"Table {counters['table']}: {caption_text}")
            cap.paragraph_format.space_before = Pt(4)
        else:
            doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def card(title, fill, rows):
        t = doc.add_table(rows=1 + len(rows), cols=2)
        t.autofit = False
        table_width(t, 17.0)
        head = t.rows[0].cells[0].merge(t.rows[0].cells[1])
        ink = ideal_ink(fill)
        cell_text(head, title, bold=True, colour=ink, size=10.5, mark=ink)
        shade(head, fill)
        for i, (lab, value, vfill) in enumerate(rows, 1):
            lc, vc = t.rows[i].cells
            cell_text(lc, lab, bold=True, colour=INK)
            shade(lc, BAND)
            if vfill:
                vink = ideal_ink(vfill)
                cell_text(vc, value, bold=True, colour=vink, mark=vink)
                shade(vc, vfill)
            else:
                cell_text(vc, value)
        grid(t)
        for row in t.rows[:-1]:  # keep the card on one page
            for cell in row.cells:
                for cp in cell.paragraphs:
                    cp.paragraph_format.keep_with_next = True
        for row in t.rows[1:]:
            row.cells[0].width, row.cells[1].width = Cm(4.2), Cm(12.8)
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def boxed(fill, bar, paras, font=None, size=8.5, colour=MUTED):
        box = doc.add_table(rows=1, cols=1)
        box.autofit = False
        table_width(box, 17.0)
        cell = box.rows[0].cells[0]
        cell.width = Cm(17.0)
        shade(cell, fill)
        tcb = OxmlElement("w:tcBorders")
        for side, c, sz in (
            ("left", bar, "24"),
            ("top", RULE, "4"),
            ("bottom", RULE, "4"),
            ("right", RULE, "4"),
        ):
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), sz)
            el.set(qn("w:color"), _hex(c))
            tcb.append(el)
        cell._tc.get_or_add_tcPr().append(tcb)
        cell.text = ""
        for i, text in enumerate(paras):
            dp = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
            dp.paragraph_format.space_after = Pt(0 if font else 4)
            dp.paragraph_format.line_spacing = 1.0 if font else 1.1
            if font:
                r = dp.add_run(text)
                r.font.size = Pt(size)
                r.font.color.rgb = RGBColor(*colour)
                r.font.name = font
                set_fonts(r._r.get_or_add_rPr().get_or_add_rFonts(), font)
            else:
                add_text(dp, text, size=size, colour=colour)
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # ── Section 1: the title page, no header or footer ──
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.4)
    sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(1.6)
    anchor_p = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
    run = anchor_p.add_run()
    run.add_picture(io.BytesIO(brand["background"]), width=Cm(21.0), height=Cm(29.7))
    _float_behind(run, Cm(21.0), Cm(29.7))
    if brand["mark"]:
        w, h = _fit(brand["mark"], 6.0, 1.3 if theme.ostrakon else 2.0)
        run.add_picture(io.BytesIO(brand["mark"]), width=Cm(w), height=Cm(h))
    anchor_p.paragraph_format.space_after = Pt(0)

    def title_line(text, size, bold=False, colour=INK, before=0, after=4, italic=False, font=None):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.right_indent = Cm(5.5)
        add_text(p, text, size=size, bold=bold, colour=colour, italic=italic)
        if font:
            for r in p.runs:
                r.font.name = font
                set_fonts(r._r.get_or_add_rPr().get_or_add_rFonts(), font)
        return p

    title_line(cover.kind.upper(), 12, True, CLAY, before=160, after=6)
    t = title_line(cover.title, 30, True, INK, after=8)
    t.paragraph_format.line_spacing = 0.95
    border(t, "bottom", CLAY, size=24, space=8)
    if cover.reference:
        title_line(cover.reference, 13, False, MUTED, before=8, after=2, font=MONO_FONT)
    if brand["client"]:
        w, h = _fit(brand["client"], 4.5, 1.4)
        logo = doc.add_paragraph()
        logo.paragraph_format.space_before = Pt(8)
        logo.add_run().add_picture(io.BytesIO(brand["client"]), width=Cm(w), height=Cm(h))
    if cover.prepared_for:
        title_line(f"Prepared for {cover.prepared_for}", 15, False, CLAY, before=6, after=2)
    title_line(cover.date, 12, False, MUTED, before=2, after=2)
    for line in cover.lines:
        title_line(line, 11, False, MUTED, after=2)
    badge = doc.add_paragraph()
    badge.paragraph_format.space_before = Pt(140)
    badge.paragraph_format.space_after = Pt(3)
    tlp_badge(badge, 12)
    title_line(tlp_mod.rule(label), 9, False, MUTED, after=10)
    title_line(f"Prepared with {branding.NAME}: {branding.TAGLINE.lower()}", 9, False, MUTED, italic=True)

    # ── Section 2 onwards: header, footer, page numbers from 1 ──
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.left_margin = body.right_margin = Cm(2.0)
    body.top_margin, body.bottom_margin = Cm(2.6), Cm(2.2)
    body.header_distance, body.footer_distance = Cm(1.0), Cm(1.0)
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), "1")
    body._sectPr.append(pg)
    for part in (body.header, body.footer):
        part.is_linked_to_previous = False
    sec.header.is_linked_to_previous = False
    sec.footer.is_linked_to_previous = False

    def own_tabs(p, *stops):
        # Clear the Header and Footer styles' own centre and right stops (8.25 and 16.5 cm).
        for pos in (Cm(8.255), Cm(16.51)):
            p.paragraph_format.tab_stops.add_tab_stop(pos, WD_TAB_ALIGNMENT.CLEAR)
        for pos, align in stops:
            p.paragraph_format.tab_stops.add_tab_stop(pos, align)

    hp = body.header.paragraphs[0]
    own_tabs(hp, (Cm(17.0), WD_TAB_ALIGNMENT.RIGHT))
    if brand["header"]:
        w, h = _fit(brand["header"], 4.0, 0.6)
        hp.add_run().add_picture(io.BytesIO(brand["header"]), width=Cm(w), height=Cm(h))
    add_text(hp, f"\t{_header_text(cover)}", size=8.5, colour=MUTED)
    border(hp, "bottom", CLAY, size=12, space=4)

    fp = body.footer.paragraphs[0]
    own_tabs(fp, (Cm(8.5), WD_TAB_ALIGNMENT.CENTER), (Cm(17.0), WD_TAB_ALIGNMENT.RIGHT))
    border(fp, "top", RULE, size=6, space=4)
    tlp_badge(fp, 8)
    add_text(fp, f"\t{_footer_who(cover)}  ·  {cover.date}\tPage ", size=8, colour=MUTED)
    fld(fp, "PAGE", "1", 8, INK, True)
    add_text(fp, " of ", size=8, colour=MUTED)
    fld(fp, "SECTIONPAGES", "1", 8, INK, True)

    # ── Table of Contents (Word fills the page numbers in when it opens the file) ──
    if not cover.compact:
        p = doc.add_heading("Table of Contents", level=1)
        if "TOC Heading" in [s.name for s in doc.styles]:
            p.style = doc.styles["TOC Heading"]
        p.runs[0].font.size, p.runs[0].font.bold = Pt(20), True
        p.runs[0].font.color.rgb = RGBColor(*INK)
        border(p, "bottom", CLAY, size=18, space=6)
        _toc_field(doc.add_paragraph(), [f"{i}  {s}" for i, s in enumerate(sections_of(blocks), 1)])
        _update_fields_on_open(doc)

    for b in blocks:
        kind = b[0]
        if kind == "section":
            counters["sec"] += 1
            counters["sub"] = 0
            if not cover.compact:
                doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
                doc.paragraphs[-1].paragraph_format.space_after = Pt(0)
            border(add_text(doc.add_heading(level=1), f"{counters['sec']}  {b[1]}"), "bottom", CLAY, 18, 6)
        elif kind == "sub":
            counters["sub"] += 1
            add_text(doc.add_heading(level=2), f"{counters['sec']}.{counters['sub']}  {b[1]}", mark=INK)
        elif kind == "para":
            add_text(doc.add_paragraph(), b[1])
        elif kind == "bullets":
            for item in b[1]:
                add_text(doc.add_paragraph(style="List Bullet"), item)
        elif kind == "table":
            table(b[1], b[2], first_col_bold=b[3] if len(b) > 3 else False)
        elif kind == "figure":
            png, text, width_cm = b[1], b[2], b[3] if len(b) > 3 else 17.0
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_with_next = True
            p.add_run().add_picture(io.BytesIO(png), width=Cm(min(width_cm, 17.0)))
            counters["figure"] += 1
            cap = add_text(doc.add_paragraph(style="Caption"), f"Figure {counters['figure']}: {text}")
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif kind == "card":
            card(b[1], b[2], b[3])
        elif kind == "note":
            doc.add_paragraph().paragraph_format.space_before = Pt(10)
            boxed(BAND, CLAY, b[1])
        elif kind == "code":
            boxed(PAPER, CLAY, b[1].splitlines() or [""], font=MONO_FONT, size=8, colour=INK)
        else:
            raise ValueError(f"Unknown report block: {kind}")

    # Back cover: a section of its own, without the header and footer, the page filled by the image.
    back = doc.add_section(WD_SECTION.NEW_PAGE)
    for part in (back.header, back.footer):
        part.is_linked_to_previous = False
    back.left_margin = back.right_margin = Cm(2.4)
    p = doc.add_paragraph()
    run = p.add_run()
    run.add_picture(io.BytesIO(brand["back"]), width=Cm(21.0), height=Cm(29.7))
    _float_behind(run, Cm(21.0), Cm(29.7))
    p.paragraph_format.space_before = Pt(390)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if theme.ostrakon:
        add_text(p, branding.NAME, size=40, bold=True, colour=INK)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for text, initial in _initials(branding.EXPANSION):
            r = p.add_run(text)
            r.font.size = Pt(11)
            r.font.bold = initial
            r.font.color.rgb = RGBColor(*(CLAY if initial else MUTED))
    else:
        for line in _back_lines(theme):
            add_text(p, line, size=9, colour=MUTED, italic=True)
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _float_behind(run, width, height) -> None:
    """Turn the run's last inline picture into one anchored at the page's top left, behind the text."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn

    inline = run._r.findall(".//" + qn("wp:inline"))[-1]
    anchor = parse_xml(
        f'<wp:anchor {nsdecls("wp")} distT="0" distB="0" distL="0" distR="0" simplePos="0" '
        f'relativeHeight="0" behindDoc="1" locked="1" layoutInCell="1" allowOverlap="1">'
        f'<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="page"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="page"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{int(width)}" cy="{int(height)}"/>'
        f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/></wp:anchor>'
    )
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        el = inline.find(qn(tag))
        if el is not None:
            anchor.append(el)
    inline.getparent().replace(inline, anchor)


def _toc_field(p, entries: Sequence[str]) -> None:
    """A TOC field whose cached result lists the sections, so the contents read even before Word
    updates the page numbers."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def fld(kind):
        r = OxmlElement("w:r")
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), kind)
        r.append(el)
        p._p.append(r)

    fld("begin")
    r = OxmlElement("w:r")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-2" \\h \\z \\u '
    r.append(instr)
    p._p.append(r)
    fld("separate")
    for i, entry in enumerate(entries):
        r = OxmlElement("w:r")
        if i:
            r.append(OxmlElement("w:br"))
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = entry
        r.append(t)
        p._p.append(r)
    fld("end")


def _update_fields_on_open(doc) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    doc.settings.element.append(el)


# ── PDF ───────────────────────────────────────────────────────────────────────

_PDF_FONTS = {
    "Inter": "regular",
    "Inter-Italic": "italic",
    "Inter-SemiBold": "semibold",
    "Inter-Bold": "bold",
    "Inter-BoldItalic": "bolditalic",
    "JetBrainsMono": "mono",
    "JetBrainsMono-Bold": "monobold",
}


def _pdf_fonts() -> None:
    """Register the bundled Inter and JetBrains Mono with reportlab, once."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, style in _PDF_FONTS.items():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(charts.font_path(style))))
    addMapping("Inter", 0, 0, "Inter")
    addMapping("Inter", 1, 0, "Inter-Bold")
    addMapping("Inter", 0, 1, "Inter-Italic")
    addMapping("Inter", 1, 1, "Inter-BoldItalic")
    addMapping("JetBrainsMono", 0, 0, "JetBrainsMono")
    addMapping("JetBrainsMono", 1, 0, "JetBrainsMono-Bold")
    addMapping("JetBrainsMono", 0, 1, "JetBrainsMono")
    addMapping("JetBrainsMono", 1, 1, "JetBrainsMono-Bold")


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if stringWidth(trial, font, size) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line] if line else lines


def _wrap_code(text: str, chars: int) -> list[str]:
    """Hard-wrap code at a character count, continuation lines indented, so nothing is clipped."""
    out: list[str] = []
    for line in text.expandtabs(4).splitlines() or [""]:
        out.append(line[:chars])
        rest = line[chars:]
        while rest:
            out.append("  " + rest[: chars - 2])
            rest = rest[chars - 2 :]
    return out


def _back_lines(theme: Theme) -> list[str]:
    """What a customer-branded back cover says under the logo."""
    lines = [f"Prepared with {branding.NAME}: {branding.TAGLINE.lower()}"]
    if theme.licensed_to:
        lines.append(f"Licensed to {theme.licensed_to}")
    return lines


def _marked(text: str, mark: RGB | None | bool = True) -> str:
    """Paragraph markup for text, with the AI mark set in clay (or the given colour; None leaves it)."""
    body = escape(text)
    if mark is None:
        return body
    if mark is True:
        mark = pal().clay
    return body.replace(AI_MARK, f'<font color="#{_hex(mark)}">{AI_MARK}</font>')


def to_pdf(cover: Cover, blocks: Sequence[tuple]) -> bytes:
    """The report as a PDF."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        Image,
        KeepTogether,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Preformatted,
        Spacer,
        TableStyle,
    )
    from reportlab.platypus import Table as RLTable
    from reportlab.platypus.tableofcontents import TableOfContents

    _pdf_fonts()
    theme = current()
    brand = _brand(theme)
    INK, CLAY, MUTED, RULE, BAND, PAPER = (  # noqa: F841 (the palette, for the helpers below)
        pal().ink,
        pal().clay,
        pal().muted,
        pal().rule,
        pal().band,
        pal().paper,
    )
    label = tlp_mod.normalise(cover.tlp)
    regular, semibold, bold, italic, mono = (
        "Inter",
        "Inter-SemiBold",
        "Inter-Bold",
        "Inter-Italic",
        "JetBrainsMono",
    )
    page_w, page_h = A4

    def col(rgb: RGB):
        return colors.Color(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)

    ink, clay, muted, rule_c, band, paper = col(INK), col(CLAY), col(MUTED), col(RULE), col(BAND), col(PAPER)
    st = {
        "body": ParagraphStyle(
            "body", fontName=regular, fontSize=10, leading=14.5, textColor=ink, spaceAfter=6
        ),
        "H1": ParagraphStyle(
            "H1", fontName=bold, fontSize=20, leading=25, textColor=ink, spaceAfter=10, keepWithNext=1
        ),
        "H2": ParagraphStyle(
            "H2",
            fontName=semibold,
            fontSize=13,
            leading=17,
            textColor=clay,
            spaceBefore=12,
            spaceAfter=6,
            keepWithNext=1,
        ),
        "cap": ParagraphStyle(
            "cap",
            fontName=italic,
            fontSize=8.5,
            leading=11,
            textColor=muted,
            alignment=TA_CENTER,
            spaceBefore=3,
            spaceAfter=10,
        ),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.5, leading=11, textColor=ink),
        "cellb": ParagraphStyle("cellb", fontName=semibold, fontSize=8.5, leading=11, textColor=ink),
        "bullet": ParagraphStyle(
            "bullet",
            fontName=regular,
            fontSize=10,
            leading=14,
            textColor=ink,
            leftIndent=14,
            bulletIndent=2,
            spaceAfter=4,
            bulletColor=clay,
        ),
        "code": ParagraphStyle("code", fontName=mono, fontSize=8, leading=10.5, textColor=ink),
        "toc1": ParagraphStyle("toc1", fontName=semibold, fontSize=11.5, leading=18, textColor=ink),
        "toc2": ParagraphStyle(
            "toc2", fontName=regular, fontSize=10, leading=14, leftIndent=18, textColor=ink
        ),
    }

    class Doc(BaseDocTemplate):
        def afterFlowable(self, f):
            if isinstance(f, Paragraph) and f.style.name in ("H1", "H2") and getattr(f, "_toc", True):
                key = f"h{id(f)}"
                self.canv.bookmarkPage(key)
                self.notify(
                    "TOCEntry", (0 if f.style.name == "H1" else 1, f.getPlainText(), self.page - 1, key)
                )

    buf = io.BytesIO()
    doc = Doc(
        buf,
        pagesize=A4,
        title=f"{cover.kind}: {cover.reference} {cover.title}".replace("  ", " "),
        author=branding.NAME,
        subject=cover.kind,
        creator=branding.NAME,
    )
    margin = 2.0 * cm
    width = page_w - 2 * margin
    frame = Frame(margin, 2.2 * cm, width, page_h - 4.8 * cm, id="body", leftPadding=0, rightPadding=0)
    cover_frame = Frame(0, 0, page_w, page_h, id="cover")
    bg = ImageReader(io.BytesIO(brand["background"]))
    back_bg = ImageReader(io.BytesIO(brand["back"]))
    mark = ImageReader(io.BytesIO(brand["mark"])) if brand["mark"] else None
    header_mark = ImageReader(io.BytesIO(brand["header"])) if brand["header"] else None
    client = ImageReader(io.BytesIO(brand["client"])) if brand["client"] else None

    def badge(cv, x, y, size):
        """The TLP label as FIRST shows it: its colour on black."""
        w = stringWidth(label, bold, size) + size * 0.8
        cv.setFillColor(colors.black)
        cv.rect(x, y - size * 0.3, w, size * 1.25, stroke=0, fill=1)
        cv.setFillColor(col(tlp_mod.colour(label)))
        cv.setFont(bold, size)
        cv.drawString(x + size * 0.4, y, label)

    def on_title(cv, _doc):
        cv._kind = "title"
        cv.drawImage(bg, 0, 0, page_w, page_h)
        if mark:
            w, h = _fit(brand["mark"], 6 * cm, (1.3 if theme.ostrakon else 2.0) * cm)
            cv.drawImage(mark, 2.4 * cm, page_h - 2.1 * cm - h, w, h, mask="auto")
        x, y, measure = 2.4 * cm, page_h - 9.6 * cm, 12.5 * cm
        cv.setFillColor(clay)
        cv.setFont(semibold, 12)
        cv.drawString(x, y, cover.kind.upper(), charSpace=1.2)
        y -= 1.45 * cm
        cv.setFillColor(ink)
        for line in _wrap(cover.title, bold, 30, measure):
            cv.setFont(bold, 30)
            cv.drawString(x, y, line)
            y -= 1.15 * cm
        cv.setStrokeColor(clay)
        cv.setLineWidth(3)
        cv.line(x, y + 0.65 * cm, x + measure, y + 0.65 * cm)
        y -= 0.55 * cm
        if cover.reference:
            cv.setFillColor(muted)
            cv.setFont(mono, 13)
            cv.drawString(x, y, cover.reference)
            y -= 0.85 * cm
        if client:
            w, h = _fit(brand["client"], 4.5 * cm, 1.4 * cm)
            y -= h - 0.5 * cm
            cv.drawImage(client, x, y, w, h, mask="auto")
            y -= 0.75 * cm
        if cover.prepared_for:
            cv.setFillColor(clay)
            cv.setFont(regular, 15)
            cv.drawString(x, y, f"Prepared for {cover.prepared_for}")
            y -= 0.75 * cm
        cv.setFillColor(muted)
        cv.setFont(regular, 12)
        cv.drawString(x, y, cover.date)
        y -= 0.6 * cm
        cv.setFont(regular, 11)
        for line in cover.lines:
            cv.drawString(x, y, line)
            y -= 0.5 * cm
        badge(cv, x, 4.1 * cm, 12)
        cv.setFillColor(muted)
        cv.setFont(regular, 9)
        for i, line in enumerate(_wrap(tlp_mod.rule(label), regular, 9, measure)):
            cv.drawString(x, 3.45 * cm - i * 0.4 * cm, line)
        cv.setFont(italic, 9)
        cv.drawString(x, 2.2 * cm, f"Prepared with {branding.NAME}: {branding.TAGLINE.lower()}")

    header_text = _header_text(cover)

    def on_body(cv, _doc):
        cv._kind = "body"
        cv.saveState()
        hh = 0.6 * cm
        top = page_h - 1.2 * cm - hh
        mark_w = 0.0
        if header_mark:
            mark_w, h = _fit(brand["header"], 4 * cm, hh)
            cv.drawImage(header_mark, margin, top + (hh - h) / 2, mark_w, h, mask="auto")
        room = width - mark_w - 1.0 * cm
        text = header_text
        while stringWidth(text, regular, 8.5) > room and len(text) > 4:
            text = text[:-2].rstrip() + "…"
        cv.setFont(regular, 8.5)
        cv.setFillColor(muted)
        cv.drawRightString(page_w - margin, top + 0.12 * cm, text)
        cv.setStrokeColor(clay)
        cv.setLineWidth(1.2)
        cv.line(margin, top - 0.25 * cm, page_w - margin, top - 0.25 * cm)
        cv.setStrokeColor(rule_c)
        cv.setLineWidth(0.6)
        cv.line(margin, 1.55 * cm, page_w - margin, 1.55 * cm)
        badge(cv, margin, 1.05 * cm, 8)
        cv.setFont(regular, 8)
        cv.setFillColor(muted)
        cv.drawCentredString(page_w / 2, 1.05 * cm, f"{_footer_who(cover)}  ·  {cover.date}")
        cv.restoreState()

    def on_back(cv, _doc):
        cv._kind = "back"
        cv.drawImage(back_bg, 0, 0, page_w, page_h)
        if not theme.ostrakon:
            cv.setFillColor(muted)
            cv.setFont(italic, 9)
            for i, line in enumerate(_back_lines(theme)):
                cv.drawCentredString(page_w / 2, page_h * 0.53 - 1.6 * cm - i * 0.5 * cm, line)
            return
        cv.setFillColor(ink)
        cv.setFont(semibold, 40)
        cv.drawCentredString(page_w / 2, page_h * 0.53 - 2.2 * cm, branding.NAME)
        parts = _initials(branding.EXPANSION)
        size = 11
        widths = [stringWidth(t, semibold if b else regular, size) for t, b in parts]
        x = (page_w - sum(widths)) / 2
        y = page_h * 0.53 - 3.3 * cm
        for (text, initial), w in zip(parts, widths, strict=True):
            cv.setFillColor(clay if initial else muted)
            cv.setFont(semibold if initial else regular, size)
            cv.drawString(x, y, text)
            x += w

    class Numbered(rl_canvas.Canvas):
        """Page x of y in the footer, counting from the first page after the title."""

        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self._states = []
            self._kind = "title"

        def showPage(self):  # noqa: N802 (reportlab's name)
            self._states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            body_pages = sum(1 for s in self._states if s.get("_kind") == "body")
            n = 0
            for state in self._states:
                self.__dict__.update(state)
                if state.get("_kind") == "body":
                    n += 1
                    self.setFont(regular, 8)
                    self.setFillColor(muted)
                    self.drawRightString(page_w - margin, 1.05 * cm, f"Page {n} of {body_pages}")
                super().showPage()
            super().save()

    doc.addPageTemplates(
        [
            PageTemplate(id="title", frames=[cover_frame], onPage=on_title),
            PageTemplate(id="body", frames=[frame], onPage=on_body),
            PageTemplate(id="back", frames=[cover_frame], onPage=on_back),
        ]
    )

    def para(text, style="body", mark: RGB | None = CLAY):
        return Paragraph(_marked(text, mark), st[style])

    def rule_line():
        r = RLTable([[""]], colWidths=[width], rowHeights=[2])
        r.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 2, clay)]))
        return r

    def table(spec: Table, caption: str, first_col_bold: bool):
        headers, rows, widths, fills = spec
        scale = width / (sum(widths) * cm)
        head_style = ParagraphStyle("th", parent=st["cellb"], textColor=paper)
        cells = [[Paragraph(_marked(h, PAPER), head_style) for h in headers]]
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), ink),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, rule_c),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]
        for i, row in enumerate(rows):
            if i % 2:
                style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), band))
            line = []
            for j, value in enumerate(row):
                fill = fills.get((i, j))
                if fill:
                    fink = ideal_ink(fill)
                    style.append(("BACKGROUND", (j, i + 1), (j, i + 1), col(fill)))
                    line.append(
                        Paragraph(
                            _marked(value, fink), ParagraphStyle("f", parent=st["cellb"], textColor=col(fink))
                        )
                    )
                else:
                    line.append(para(value, "cellb" if first_col_bold and j == 0 else "cell"))
            cells.append(line)
        t = RLTable(cells, colWidths=[w * cm * scale for w in widths], repeatRows=1)
        t.setStyle(TableStyle(style))
        if caption:
            counters["table"] += 1
            return [t, para(f"Table {counters['table']}: {caption}", "cap")]
        return [t, Spacer(1, 8)]

    def card(title, fill, rows):
        fink = ideal_ink(fill)
        head = Paragraph(
            _marked(title, fink),
            ParagraphStyle("fh", parent=st["cellb"], fontSize=10.5, leading=14, textColor=col(fink)),
        )
        cells = [[head, ""]]
        style = [
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), col(fill)),
            ("GRID", (0, 0), (-1, -1), 0.5, rule_c),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (0, -1), band),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for i, (lab, value, vfill) in enumerate(rows, 1):
            if vfill:
                vink = ideal_ink(vfill)
                style.append(("BACKGROUND", (1, i), (1, i), col(vfill)))
                v = Paragraph(
                    _marked(value, vink), ParagraphStyle("v", parent=st["cellb"], textColor=col(vink))
                )
            else:
                v = para(value, "cell")
            cells.append([para(lab, "cellb"), v])
        t = RLTable(cells, colWidths=[4.2 * cm, width - 4.2 * cm])
        t.setStyle(TableStyle(style))
        return [KeepTogether([t]), Spacer(1, 10)]

    def code(text: str):
        # One row per line, so a long query breaks across pages instead of overflowing one.
        pad = 10
        chars = max(int((width - 2 * pad - 3) / stringWidth("M", mono, st["code"].fontSize)), 20)
        rows = [[Preformatted(line or " ", st["code"])] for line in _wrap_code(text, chars)]
        t = RLTable(rows, colWidths=[width])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), paper),
                    ("BOX", (0, 0), (-1, -1), 0.5, rule_c),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, clay),
                    ("LEFTPADDING", (0, 0), (-1, -1), pad + 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, 0), 7),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
                ]
            )
        )
        return [t, Spacer(1, 10)]

    counters = {"figure": 0, "table": 0, "sec": 0, "sub": 0}
    toc = TableOfContents()
    toc.levelStyles = [st["toc1"], st["toc2"]]
    toc.dotsMinLevel = 0
    contents = Paragraph("Table of Contents", st["H1"])
    contents._toc = False
    story: list = [NextPageTemplate("body"), PageBreak()]
    if not cover.compact:
        story += [contents, rule_line(), Spacer(1, 10), toc]

    for b in blocks:
        kind = b[0]
        if kind == "section":
            counters["sec"] += 1
            counters["sub"] = 0
            if not cover.compact:
                story.append(PageBreak())
            elif counters["sec"] > 1:
                story.append(Spacer(1, 16))
            story += [para(f"{counters['sec']}  {b[1]}", "H1"), rule_line(), Spacer(1, 8)]
        elif kind == "sub":
            counters["sub"] += 1
            story.append(para(f"{counters['sec']}.{counters['sub']}  {b[1]}", "H2", mark=INK))
        elif kind == "para":
            story.append(para(b[1]))
        elif kind == "bullets":
            story += [Paragraph(_marked(t), st["bullet"], bulletText="•") for t in b[1]]
        elif kind == "table":
            story += table(b[1], b[2], b[3] if len(b) > 3 else False)
        elif kind == "figure":
            png, text, width_cm = b[1], b[2], b[3] if len(b) > 3 else 17.0
            iw, ih = ImageReader(io.BytesIO(png)).getSize()
            w = min(width_cm * cm, width)
            counters["figure"] += 1
            story.append(
                KeepTogether(
                    [
                        Image(io.BytesIO(png), w, w * ih / iw),
                        para(f"Figure {counters['figure']}: {text}", "cap"),
                    ]
                )
            )
        elif kind == "card":
            story += card(b[1], b[2], b[3])
        elif kind == "note":
            note = ParagraphStyle("note", parent=st["cell"], textColor=muted, spaceAfter=4)
            box = RLTable([[[Paragraph(_marked(t), note) for t in b[1]]]], colWidths=[width])
            box.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), band),
                        ("BOX", (0, 0), (-1, -1), 0.5, rule_c),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, clay),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story += [Spacer(1, 14), KeepTogether([box])]
        elif kind == "code":
            story += code(b[1])
        else:
            raise ValueError(f"Unknown report block: {kind}")

    story += [NextPageTemplate("back"), PageBreak(), Spacer(1, 1)]
    doc.multiBuild(story, canvasmaker=Numbered)
    return buf.getvalue()


def render(cover: Cover, blocks: Sequence[tuple], fmt: str) -> bytes:
    """The report as "pdf" or "docx" bytes."""
    if fmt == "pdf":
        return to_pdf(cover, blocks)
    if fmt == "docx":
        return to_docx(cover, blocks)
    raise ValueError(f"Unknown report format: {fmt}. Use pdf or docx")


def write_sample(out_dir: Path) -> list[Path]:
    """Render the demonstration report (`ostraka.reports.demo`) to both formats in `out_dir`."""
    from ostraka.reports import demo

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in ("pdf", "docx"):
        path = out_dir / f"renderer-sample.{fmt}"
        path.write_bytes(render(demo.cover(), demo.blocks(), fmt))
        written.append(path)
        log.info(f"Wrote {path}")
    return written
