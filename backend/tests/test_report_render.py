"""The branded report shell renders every block, chart and TLP label to PDF and Word."""

from __future__ import annotations

import io
import re

import pytest
from docx import Document

from ostraka import branding
from ostraka.reports import attack, branded, charts, demo, tlp
from ostraka.reports.theme import OSTRAKON, contrast

TLP_LABEL = "TLP:AMBER+STRICT"


@pytest.fixture(scope="module")
def blocks() -> list[tuple]:
    return demo.blocks()


@pytest.fixture(scope="module")
def pdf(blocks) -> bytes:
    return branded.render(demo.cover(), blocks, "pdf")


@pytest.fixture(scope="module")
def docx_text(blocks) -> str:
    doc = Document(io.BytesIO(branded.render(demo.cover(), blocks, "docx")))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts += [cell.text for row in table.rows for cell in row.cells]
    for section in doc.sections:
        parts += [p.text for p in section.header.paragraphs + section.footer.paragraphs]
    return "\n".join(parts)


def test_demo_uses_every_block(blocks):
    kinds = {b[0] for b in blocks}
    assert kinds == {"section", "sub", "para", "bullets", "table", "figure", "card", "note", "code"}
    assert any(b[0] == "para" and b[1].endswith("†") for b in blocks)


def test_pdf_renders_with_cover_pages_and_back(pdf):
    assert pdf.startswith(b"%PDF")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    # Cover, contents, four sections (the findings table runs onto a second page), back cover.
    assert pages >= 8
    # Text in an embedded subset font isn't searchable in the bytes, so check the fonts instead.
    assert re.search(rb"/FontName /[A-Z]{6}\+Inter", pdf)
    assert re.search(rb"/FontName /[A-Z]{6}\+JetBrainsMono", pdf)


def test_docx_has_sections_tlp_and_expansion(docx_text, blocks):
    for title in branded.sections_of(blocks):
        assert title in docx_text
    assert TLP_LABEL in docx_text
    assert "OST-2026-014" in docx_text
    assert "Findings†" in docx_text
    assert branding.EXPANSION in docx_text.replace("\n", "")


def test_render_refuses_unknown_format_and_block():
    with pytest.raises(ValueError):
        branded.render(demo.cover(), [], "html")
    with pytest.raises(ValueError):
        branded.render(demo.cover(), [("chart", "x")], "pdf")


def test_tlp_labels_normalise():
    assert tlp.normalise("amber+strict") == TLP_LABEL
    assert tlp.normalise("TLP:WHITE") == "TLP:CLEAR"
    assert tlp.colour("red") == (255, 43, 43)
    with pytest.raises(ValueError):
        tlp.normalise("purple")


def test_attack_lookup():
    assert attack.technique("T1059.001") == ("PowerShell", ["execution"])
    assert attack.technique("T9999") == ("", [])
    assert attack.TACTICS[0][0] == "reconnaissance"
    assert "MITRE" in attack.TERMS


def test_charts_are_pngs():
    fill = charts.SEVERITY["high"]
    for png in (
        charts.headline_cards([("3", "Hosts", fill)]),
        charts.horizontal_bars([("Confirmed", 2, OSTRAKON.ink)], total=4),
        charts.attack_matrix({"Execution": [("T1059.001", fill)]}, legend=[("High", fill)]),
        charts.phase_bar([("detect", 1.0, OSTRAKON.ink), ("contain", 0.0, OSTRAKON.clay)]),
        charts.ach_scores([("H1", 0, True), ("H2", -2.5, False)]),
        charts.wordmark(),
    ):
        assert png.startswith(b"\x89PNG")


def test_ideal_ink_reads_on_every_fill():
    for fill in [*charts.SEVERITY.values(), *charts.STATUS.values()]:
        assert contrast(charts.ideal_ink(fill), fill) >= 4.5, fill


def test_only_network_indicators_are_defanged():
    from ostraka.reports.incident import defang

    assert defang("ip_address", "203.0.113.45") == "203[.]0[.]113[.]45"
    assert defang("url", "https://evil.example/x.php") == "hxxps://evil[.]example/x[.]php"
    assert defang("email", "ap@supplier.example") == "ap[@]supplier[.]example"
    assert defang("file_name", "HOW_TO_RESTORE.txt") == "HOW_TO_RESTORE.txt"
    assert defang("process_command", "rclone.exe copy x") == "rclone.exe copy x"
