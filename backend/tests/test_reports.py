"""Incident reports against Postgres: the sample's ransomware incident as a technical report and a
briefing in both formats, and who may export one over the API.

Set OSTRAKA_REPORT_SAMPLES to a folder to keep the four rendered files for review.
"""

import io
import os
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import select

from ostraka import importer
from ostraka.models import ActivityEvent, Incident, SecurityEvent, User
from ostraka.reports import branded
from ostraka.reports import incident as content
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db


def docx_text(body: bytes) -> str:
    doc = Document(io.BytesIO(body))
    cells = [cell.text for table in doc.tables for row in table.rows for cell in row.cells]
    return "\n".join([p.text for p in doc.paragraphs] + cells)


async def test_the_sample_ransomware_incident_reports_in_full_and_in_brief(
    db, seed_playbooks, client, settle
):
    await importer.run(importer.SAMPLE, http=client)
    await settle()
    incident = await db.scalar(
        select(Incident).where(Incident.title == "Ransomware on the finance file servers")
    )
    amara = await db.scalar(select(User).where(User.name.like("Amara%")))
    record = await content.gather(db, incident, amara)
    assert record.chain_ok and record.contained_after is not None

    keep = os.environ.get("OSTRAKA_REPORT_SAMPLES")
    texts = {}
    for variant, build in (("report", content.technical), ("briefing", content.briefing)):
        cover, blocks = build(record)
        assert cover.tlp == "TLP:AMBER+STRICT" and cover.reference == incident.ref
        for fmt in ("pdf", "docx"):
            body = branded.render(cover, blocks, fmt)
            if fmt == "pdf":
                assert body.startswith(b"%PDF")
            else:
                texts[variant] = docx_text(body)
            if keep:
                Path(keep).mkdir(parents=True, exist_ok=True)
                (Path(keep) / f"{incident.ref}-{variant}.{fmt}").write_bytes(body)

    report, brief = texts["report"], texts["briefing"]
    # Bottom line first, from the current assessments, with the superseded one in its history
    assert "We assess it is likely (55–75%) that the actor copied payroll data off the network" in report
    assert "How the judgement moved" in report and "realistic possibility" in report
    assert "There is no evidence of rclone traffic to a cloud storage provider" in report
    # ACH: every hypothesis, the rejected one with its reason, and the arithmetic stated
    assert (
        "An unannounced red-team exercise" in report and "The CISO confirmed no exercise was booked" in report
    )
    assert "1.414" in report
    assert "Leads, not confirmed" in report and "What we'd change" in report
    assert "verified intact" in report
    # Indicators: defanged in the report, masked in the briefing
    assert "203[.]0[.]113[.]45" in report and "203.0.113.45" not in brief
    assert "9d3c7a1e5b2f48c06e1a7d4b9f3c2e8a1d6b5f4c3a2e1d0f9b8c7a6e5d4c3b2a" not in brief
    assert "What We Know" in brief and "What We'll Change" in brief and "Appendix C" not in brief
    # The custody section: the exhibit's hash and its handover, in the report only
    assert "Evidence and Chain of Custody" in report
    assert "4b1f0e7c9a2d83e65f1c0b9a7e4d2c8f6a3b5e1d9c7f0a2b4e6d8c1a3f5b7e9d" in report
    assert "seal intact. Re-hashed" in report and "SHA-256 re-checked: matches" in report
    assert "Evidence and Chain of Custody" not in brief


async def test_only_people_who_worked_on_it_export_and_each_export_is_recorded(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"], tlp="red")
    url = f"/api/incidents/{incident['id']}/export"

    r = await client.get(url, params={"variant": "briefing", "format": "docx"}, headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == f'attachment; filename="{incident["ref"]}-briefing.docx"'
    assert "TLP:RED" in docx_text(r.content)
    r = await client.get(url, headers=p["soc"].headers)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    for who in ("lead", "exec", "admin"):
        r = await client.get(url, headers=p[who].headers)
        assert r.status_code == 403, who
    assert (await client.get(url, headers=p["dfir"].headers)).status_code == 404  # can't see it at all

    audit = (await db.scalars(select(SecurityEvent).where(SecurityEvent.action == "report_exported"))).all()
    assert [(e.detail["variant"], e.detail["format"]) for e in audit] == [
        ("briefing", "docx"),
        ("report", "pdf"),
    ]
    logged = (await db.scalars(select(ActivityEvent).where(ActivityEvent.action == "report_exported"))).all()
    assert [e.summary for e in logged] == [
        "Sam Soc exported the briefing as DOCX",
        "Sam Soc exported the technical report as PDF",
    ]
