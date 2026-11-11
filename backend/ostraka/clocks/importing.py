"""The import file's clock steps (`ostraka-import/1`, added 11 November 2026), run by
`ostraka.importer` through the API like every other step:

    {"do": "clock", "key": "clk_ico", "by": "nina",
     "with": {"regime": "uk_gdpr", "owner": "nina", "title": "", "classified": "-6d 09:00",
              "starts": "-6d 07:40", "milestones": [{"name": "...", "amount": 48, "unit": "hours",
              "basis": "aware"}]}}
    {"do": "clock_met", "clock": "clk_ico", "milestone": "report", "reference": "...", "note": "..."}
    {"do": "clock_close", "clock": "clk_ico", "reason": "..."}

All of "with" but "regime" is optional, and "milestones" is for a custom clock only. "classified" and
"starts" are times in the file's usual form, and can't be later than the step.
A milestone is met at its step's time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ostraka import importer  # the module, not its names: it imports this one

if TYPE_CHECKING:
    from ostraka.importer import _Run, _Step


def _at(run: _Run, step: _Step, value: str | None) -> str | None:
    # As the record's "aware": "-6d 09:00" is six days ago, "+20m" twenty minutes after this step
    return importer.when(value, now=datetime.now(UTC), previous=step.at).isoformat() if value else None


async def _current(run: _Run, key: str) -> tuple[str, dict[str, Any]]:
    made = run.get(key, "clock")
    base = f"/api/incidents/{run.get(made.record, 'record').id}/clocks"
    clock = next(c for c in await run.call("GET", base) if c["id"] == made.id)
    return f"{base}/{made.id}", clock


async def add(run: _Run, step: _Step) -> None:
    b = step.body["with"]
    body = {
        "regime": b["regime"],
        "title": run.text(b.get("title", "")),
        "owner_id": run.person(b["owner"]) if b.get("owner") else None,
        "classified_at": _at(run, step, b.get("classified")),
        "starts_at": _at(run, step, b.get("starts")),
        "milestones": b.get("milestones", []),
    }
    out = await run.call("POST", f"{run.base(step)}/clocks", body)
    run.keep(step.body.get("key"), "clock", step.record, out)


async def met(run: _Run, step: _Step) -> None:
    url, clock = await _current(run, step.body["clock"])
    await run.call(
        "POST",
        f"{url}/milestones/{step.body['milestone']}/met",
        {
            "version": clock["version"],
            "reference": step.body.get("reference", ""),
            "note": run.text(step.body.get("note", "")),
        },
    )


async def close(run: _Run, step: _Step) -> None:
    url, clock = await _current(run, step.body["clock"])
    await run.call(
        "POST", f"{url}/close", {"version": clock["version"], "reason": run.text(step.body["reason"])}
    )
