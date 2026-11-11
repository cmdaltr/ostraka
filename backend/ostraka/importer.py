"""Loading records from a file: `ostraka import FILE`, `ostraka import --sample`, and removing what a file
loaded with `--remove`.

The importer is a client of ostraka's own API, run in-process. Each step is made as the person the
file names, through the same routes and rules as the browser: a finding is confirmed only after a
verification, every change lands on the hash-chained activity log, and mentions and nudges notify
as they would. Steps are replayed in time order with the clock set to the time the file gives, so
the log is written in order rather than rewritten afterwards.

The format (`ostraka-import/1`):

    {
      "format": "ostraka-import/1",
      "tag": "sample",                      # added to every record; `--remove` deletes by it
      "teams": [{"name": "...", "description": "..."}],
      "people": [{"key": "amara", "name": "...", "email": "...", "role": "team_lead", "team": "..."}],
      "records": [{
        "key": "rw", "by": "amara", "at": "-6d 07:52",
        "record": {"kind": "incident", "title": "...", "severity": "high", "tlp": "amber",
                   "teams": ["..."], "tags": [], "summary": "...", "aware": "-6d 07:40",
                   "playbooks": ["Ransomware"]},
        "steps": [{"at": "+20m", "by": "dev", "do": "observable", "key": "o1", "with": {...}}, ...]
      }]
    }

People already in ostraka are matched by email. Anyone else becomes an account that can't sign in
(no usable password, no email or push), so the work has names without opening a way in.

Times: "-6d 07:52" is six days ago at 07:52 UTC, "-3h" three hours ago, "+20m" twenty minutes
after the step before, or an ISO 8601 instant. A step without "at" comes two minutes after the one
before. A task's "due" counts from its step: "+4h". Text may name things by key: "@{amara}" is a
mention, "@{team:Northwind SOC}" a team mention, and "#{f1}" the reference of whatever key f1 made.

Steps ("do"): update, member, team, playbook, observable, sighting, asset, asset_update, asset_link,
finding, verify, confirm, refute, task, task_update, tick, claim, release, nudge, wait_on, comment,
link, promote, decide, assess, hypothesis, evidence, rate, reject, lesson, handover. Their fields are the
API's, with keys in place of ids; see `_STEPS`. A playbook step's "keys" name its steps, and each
becomes a task once its step opens.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import accounts, audit, branding, live, notify, webhooks
from ostraka.auth.secrets import new_token, token_hash
from ostraka.db import sessions
from ostraka.log import get_logger
from ostraka.models import (
    ActivityEvent,
    ApiToken,
    Incident,
    IncidentObservable,
    Observable,
    Role,
    Team,
    TokenScope,
    User,
    clock,
)

log = get_logger(__name__)

FORMAT = "ostraka-import/1"
SAMPLE = Path(__file__).parent / "samples" / "sample.json"
# A placeholder account's password hash: not a hash at all, so no password ever matches it
UNUSABLE = accounts.UNUSABLE_PASSWORD

_WHEN = re.compile(
    r"^(?P<sign>[+-])\s*(?:(?P<d>\d+)d)?\s*(?:(?P<h>\d+)h)?\s*(?:(?P<m>\d+)m)?\s*(?P<clock>\d{1,2}:\d{2})?$"
)
_PLACEHOLDER = re.compile(r"@\{(team:)?([^}]+)\}|#\{([^}]+)\}")


class ImportProblem(Exception):
    """The file can't be loaded as it stands; the message says where and why."""


@dataclass
class Outcome:
    records: list[str] = field(default_factory=list)
    people_created: int = 0
    teams_created: int = 0
    steps: int = 0


@dataclass
class _Made:
    """Something a step created, by its key: enough to refer to it, address it and edit it."""

    kind: str
    id: str
    ref: str = ""
    record: str = ""  # the key of the record it belongs to
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Step:
    at: datetime
    order: int
    record: str
    by: str
    do: str
    body: dict[str, Any]


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportProblem(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ImportProblem(f'{path}: not an Ostraka import file (it needs "format": "{FORMAT}").')
    return data


def when(text: str | None, *, now: datetime, previous: datetime) -> datetime:
    """A time from the file: relative to now ("-"), to the step before ("+"), or an ISO instant."""
    if not text:
        return previous + timedelta(minutes=2)
    match = _WHEN.match(text.strip())
    if match:
        span = timedelta(days=int(match["d"] or 0), hours=int(match["h"] or 0), minutes=int(match["m"] or 0))
        base = previous + span if match["sign"] == "+" else now - span
        if match["clock"]:
            hour, minute = (int(part) for part in match["clock"].split(":"))
            base = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return base
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ImportProblem(
            f'"{text}" isn\'t a time Ostraka understands, such as "-2d 09:30" or "+20m".'
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _plan(data: dict[str, Any], now: datetime) -> list[_Step]:
    """Every step in the file, in the order it happened."""
    people = {p["key"] for p in data.get("people", [])}
    keys: set[str] = set()
    steps: list[_Step] = []
    order = 0
    for record in data.get("records", []):
        key = record.get("key")
        # A promoted record is made by a "promote" step earlier in the file; its entry holds only steps
        promoted = record.get("promoted", False)
        if not key or (key in keys) != promoted:
            problem = "no earlier promote step made it" if promoted else "it's missing or used twice"
            raise ImportProblem(f"Record {key!r}: {problem}.")
        keys.add(key)
        if record.get("by") not in people:
            raise ImportProblem(f'Record {key}: "by" must name someone in "people".')
        if promoted:
            first = record["steps"][0].get("at") if record.get("steps") else None
            at = when(first, now=now, previous=now)
        else:
            at = when(record.get("at"), now=now, previous=now - timedelta(days=1))
            steps.append(_Step(at, order, key, record["by"], "declare", record.get("record", {})))
            order += 1
        for step in record.get("steps", []):
            by = step.get("by", record["by"])
            if by not in people:
                raise ImportProblem(
                    f"Record {key}: step {step.get('do')} is by {by!r}, who isn't in \"people\"."
                )
            if step.get("do") not in _STEPS:
                raise ImportProblem(f"Record {key}: {step.get('do')!r} isn't a step Ostraka knows.")
            made = step.get("key")
            if made:
                if made in keys:
                    raise ImportProblem(f"Record {key}: the key {made!r} is used twice.")
                keys.add(made)
            before, at = at, when(step.get("at"), now=now, previous=at)
            if at < before:
                raise ImportProblem(
                    f"Record {key}: the {step['do']} step at {at:%Y-%m-%d %H:%M} "
                    "comes before the step above it."
                )
            steps.append(_Step(at, order, key, by, step["do"], step))
            order += 1
    late = [s for s in steps if s.at > now]
    if late:
        raise ImportProblem(
            f"Record {late[0].record}: a {late[0].do} step is in the future ({late[0].at:%Y-%m-%d %H:%M})."
        )
    # Records interleave in time; within one, the file's order breaks ties
    return sorted(steps, key=lambda s: (s.at, s.order))


class _Run:
    def __init__(
        self, data: dict[str, Any], http: httpx.AsyncClient, people: dict[str, User], tokens: dict[str, str]
    ):
        self.data = data
        self.http = http
        self.people = people
        self.tokens = tokens
        self.teams: dict[str, str] = {}
        self.playbooks: dict[str, str] = {}
        self.made: dict[str, _Made] = {}
        self.outcome = Outcome()
        self.step: _Step | None = None

    # ---- talking to the API ----------------------------------------------------------------------

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None, *, by: str = "") -> Any:
        assert self.step is not None
        headers = {"Authorization": f"Bearer {self.tokens[by or self.step.by]}"}
        token = clock.set(self.step.at)
        try:
            response = await self.http.request(method, path, json=body, headers=headers)
        finally:
            clock.reset(token)
        if response.status_code >= 400:
            detail = (
                response.json().get("detail")
                if response.headers.get("content-type", "").startswith("application/json")
                else response.text
            )
            raise ImportProblem(
                f"Record {self.step.record}, step {self.step.do} at {self.step.at:%Y-%m-%d %H:%M}: "
                f"{response.status_code} {detail}"
            )
        return response.json() if response.content else None

    # ---- resolving keys --------------------------------------------------------------------------

    def get(self, key: str, *kinds: str) -> _Made:
        made = self.made.get(key)
        if made is None or (kinds and made.kind not in kinds):
            raise ImportProblem(
                f"Record {self.step.record if self.step else '?'}: nothing earlier made {key!r}."
            )
        return made

    def person(self, key: str) -> str:
        if key not in self.people:
            raise ImportProblem(f'{key!r} isn\'t in "people".')
        return str(self.people[key].id)

    def team(self, name: str) -> str:
        if name not in self.teams:
            raise ImportProblem(f"No team called {name!r}.")
        return self.teams[name]

    def text(self, value: str) -> str:
        """Placeholders in free text: @{person}, @{team:Name} and #{key}."""

        def swap(match: re.Match[str]) -> str:
            if match[3]:
                return "#" + self.get(match[3]).ref
            if match[1]:
                return f"@[{match[2]}](team:{self.team(match[2])})"
            user = self.people.get(match[2])
            if user is None:
                raise ImportProblem(f'@{{{match[2]}}} isn\'t in "people".')
            return f"@[{user.name}](user:{user.id})"

        return _PLACEHOLDER.sub(swap, value)

    def record_of(self, step: _Step) -> _Made:
        return self.get(step.record, "record")

    def base(self, step: _Step) -> str:
        return f"/api/incidents/{self.record_of(step).id}"

    def due(self, value: str | None) -> str | None:
        assert self.step is not None
        return when(value, now=self.step.at, previous=self.step.at).isoformat() if value else None

    def keep(self, key: str | None, kind: str, record: str, out: dict[str, Any]) -> None:
        if key:
            self.made[key] = _Made(kind, str(out["id"]), out.get("ref", ""), record, out)

    async def task(self, key: str) -> dict[str, Any]:
        """A task as it is now, for its version and checklist."""
        made = self.get(key, "task")
        rows = await self.call("GET", f"/api/incidents/{self.get(made.record, 'record').id}/tasks")
        return next(t for t in rows if t["id"] == made.id)

    async def finding(self, key: str) -> dict[str, Any]:
        made = self.get(key, "finding")
        return await self.call(
            "GET", f"/api/incidents/{self.get(made.record, 'record').id}/findings/{made.id}"
        )

    # ---- steps -----------------------------------------------------------------------------------

    async def declare(self, step: _Step) -> None:
        r = step.body
        tag = self.data.get("tag")
        body = {
            "kind": r.get("kind", "incident"),
            "title": r["title"],
            "summary": self.text(r.get("summary", "")),
            "severity": r.get("severity", "medium"),
            "tlp": r.get("tlp", "amber"),
            "team_ids": [self.team(name) for name in r.get("teams", [])],
            "tags": [*r.get("tags", []), *([tag] if tag else [])],
            "playbook_ids": [self._playbook(name) for name in r.get("playbooks", [])],
        }
        if r.get("aware"):
            body["aware_at"] = when(r["aware"], now=datetime.now(UTC), previous=step.at).isoformat()
        out = await self.call("POST", "/api/incidents", body)
        self.made[step.record] = _Made("record", out["id"], out["ref"], step.record, out)
        self.outcome.records.append(out["ref"])

    def _playbook(self, name: str) -> str:
        if name not in self.playbooks:
            raise ImportProblem(f"No playbook called {name!r}.")
        return self.playbooks[name]

    async def update(self, step: _Step) -> None:
        current = await self.call("GET", self.base(step))
        body = dict(step.body.get("with", {}))
        if "summary" in body:
            body["summary"] = self.text(body["summary"])
        if "tags" in body and self.data.get("tag"):
            body["tags"] = [*body["tags"], self.data["tag"]]
        await self.call("PATCH", self.base(step), {"version": current["version"], **body})

    async def member(self, step: _Step) -> None:
        b = step.body
        await self.call(
            "PUT",
            f"{self.base(step)}/members/{self.person(b['person'])}",
            {"role": b.get("role", "responder")},
        )

    async def add_team(self, step: _Step) -> None:
        await self.call("POST", f"{self.base(step)}/teams", {"team_id": self.team(step.body["team"])})

    async def playbook(self, step: _Step) -> None:
        playbook = self._playbook(step.body["name"])
        await self.call("POST", f"{self.base(step)}/playbooks/{playbook}")
        # Its steps can be named by key, in order: "keys": ["t1", "t2", ...]. A step's task only exists
        # once the step opens, so a key becomes a task when the replay gets there (`_open_steps`).
        for position, key in enumerate(step.body.get("keys", [])):
            if key:
                self.made[key] = _Made(
                    "step",
                    "",
                    "",
                    step.record,
                    {"playbook_id": playbook, "position": position, "by": step.by},
                )

    async def _open_steps(self, step: _Step) -> None:
        """Playbook steps this step names, whose tasks now exist, become tasks."""
        text = json.dumps(step.body)
        waiting: dict[str, list[str]] = {}
        for key, made in self.made.items():
            if made.kind == "step" and (f'"{key}"' in text or f"#{{{key}}}" in text):
                waiting.setdefault(made.record, []).append(key)
        for record, keys in waiting.items():
            runs = await self.call(
                "GET",
                f"/api/incidents/{self.get(record, 'record').id}/playbooks",
                by=self.made[keys[0]].data["by"],
            )
            by_playbook = {r["playbook_id"]: r["steps"] for r in runs}
            for key in keys:
                made = self.made[key]
                step = by_playbook[made.data["playbook_id"]][made.data["position"]]
                if step["task_id"]:
                    self.made[key] = _Made("task", step["task_id"], step["task_ref"], record, {})

    # ---- assessments, ACH and lessons ---------------------------------------------------------------

    def _times(self, body: dict[str, Any], step: _Step) -> dict[str, Any]:
        for name in ("from", "to"):
            if name in body:
                body[f"time_{name}"] = when(
                    body.pop(name), now=datetime.now(UTC), previous=step.at
                ).isoformat()
        return body

    async def assess(self, step: _Step) -> None:
        """An assessment from its parts: "findings" and "supersedes" name keys."""
        b = dict(step.body["with"])
        body = {
            "judgement": self.text(b["judgement"]),
            "probability": b["probability"],
            "finding_ids": [self.get(k, "finding").id for k in b.get("findings", [])],
            "absences": [self._times(dict(a), step) for a in b.get("absences", [])],
            "so_what": self.text(b.get("so_what", "")),
            "what_next": self.text(b.get("what_next", "")),
            "supersedes_id": self.get(b["supersedes"], "assessment").id if b.get("supersedes") else None,
        }
        out = await self.call("POST", f"{self.base(step)}/assessments", body)
        self.keep(step.body.get("key"), "assessment", step.record, out)

    async def hypothesis(self, step: _Step) -> None:
        b = step.body["with"]
        out = await self.call(
            "POST",
            f"{self.base(step)}/ach/hypotheses",
            {"statement": self.text(b["statement"]), "milestone": self.text(b.get("milestone", ""))},
        )
        self.keep(step.body.get("key"), "hypothesis", step.record, out["hypotheses"][-1])

    async def evidence(self, step: _Step) -> None:
        b = dict(step.body["with"])
        if "finding" in b:
            b["finding_id"] = self.get(b.pop("finding"), "finding").id
        if "observable" in b:
            b["observable_id"] = self.get(b.pop("observable"), "observable").id
        b["text"] = self.text(b.get("text", ""))
        out = await self.call("POST", f"{self.base(step)}/ach/evidence", b)
        self.keep(step.body.get("key"), "evidence", step.record, out["evidence"][-1])

    async def rate(self, step: _Step) -> None:
        """One analyst's ratings, {"evidence key": {"hypothesis key": "c" | "i" | "na"}}."""
        for evidence, row in step.body["ratings"].items():
            for hypothesis, value in row.items():
                await self.call(
                    "PUT",
                    f"{self.base(step)}/ach/ratings",
                    {
                        "hypothesis_id": self.get(hypothesis, "hypothesis").id,
                        "evidence_id": self.get(evidence, "evidence").id,
                        "value": value,
                    },
                )

    async def reject(self, step: _Step) -> None:
        made = self.get(step.body["hypothesis"], "hypothesis")
        matrix = await self.call("GET", f"{self.base(step)}/ach")
        version = next(h["version"] for h in matrix["hypotheses"] if h["id"] == made.id)
        await self.call(
            "POST",
            f"{self.base(step)}/ach/hypotheses/{made.id}/reject",
            {"version": version, "reason": self.text(step.body["reason"])},
        )

    async def lesson(self, step: _Step) -> None:
        b = step.body["with"]
        out = await self.call(
            "POST",
            f"{self.base(step)}/lessons",
            {
                **b,
                "observation": self.text(b["observation"]),
                "recommendation": self.text(b.get("recommendation", "")),
            },
        )
        self.keep(step.body.get("key"), "lesson", step.record, out)
        if owner := step.body.get("task"):
            body = (
                {"team_id": self.team(owner["team"])}
                if owner.get("team")
                else {"assignee_id": self.person(owner["assignee"])}
            )
            await self.call("POST", f"{self.base(step)}/lessons/{out['id']}/task", body)

    async def choose(self, step: _Step) -> None:
        """Finish a decision with one of its outcomes; one that promotes names the new incident by "key"."""
        current = await self.task(step.body["task"])
        body: dict[str, Any] = {
            "version": current["version"],
            "outcome": step.body["outcome"],
            "note": self.text(step.body.get("note", "")),
        }
        if promote := step.body.get("with"):
            body["promote"] = {**promote, "summary": self.text(promote.get("summary", ""))}
        await self.call("POST", f"{self._task_path(step.body['task'])}/decide", body)
        if key := step.body.get("key"):
            links = await self.call("GET", f"{self.base(step)}/links")
            await self._promoted(key, links["promoted_to"][-1]["id"])

    async def observable(self, step: _Step) -> None:
        body = dict(step.body["with"])
        body["note"] = self.text(body.get("note", ""))
        if sighting := body.get("sighting"):
            body["sighting"] = self._sighting(sighting)
        out = await self.call("POST", f"{self.base(step)}/observables", body)
        self.keep(step.body.get("key"), "observable", step.record, out)

    def _sighting(self, s: dict[str, Any]) -> dict[str, Any]:
        s = dict(s)
        if "asset" in s:
            s["asset_id"] = self.get(s.pop("asset"), "asset").id
        if "seen" in s:
            s["seen_at"] = when(s.pop("seen"), now=datetime.now(UTC), previous=self.step.at).isoformat()
        return s

    async def sighting(self, step: _Step) -> None:
        made = self.get(step.body["observable"], "observable")
        await self.call(
            "POST", f"{self.base(step)}/observables/{made.id}/sightings", self._sighting(step.body["with"])
        )

    async def asset(self, step: _Step) -> None:
        body = dict(step.body["with"])
        body["notes"] = self.text(body.get("notes", ""))
        out = await self.call("POST", f"{self.base(step)}/assets", body)
        self.keep(step.body.get("key"), "asset", step.record, out)

    async def asset_update(self, step: _Step) -> None:
        made = self.get(step.body["asset"], "asset")
        rows = await self.call("GET", f"{self.base(step)}/assets")
        current = next(a for a in rows if a["id"] == made.id)
        await self.call(
            "PATCH",
            f"{self.base(step)}/assets/{made.id}",
            {"version": current["version"], **step.body["with"]},
        )

    async def asset_link(self, step: _Step) -> None:
        body = dict(step.body["with"])
        body["source_id"] = self.get(body.pop("source"), "asset").id
        body["target_id"] = self.get(body.pop("target"), "asset").id
        if "seen" in body:
            body["seen_at"] = when(body.pop("seen"), now=datetime.now(UTC), previous=step.at).isoformat()
        await self.call("POST", f"{self.base(step)}/asset-links", body)

    async def add_finding(self, step: _Step) -> None:
        body = dict(step.body["with"])
        body["statement"] = self.text(body.get("statement", ""))
        body["observable_ids"] = [self.get(k, "observable").id for k in body.pop("observables", [])]
        if "asset" in body:
            body["asset_id"] = self.get(body.pop("asset"), "asset").id
        if "occurred" in body:
            body["occurred_at"] = when(
                body.pop("occurred"), now=datetime.now(UTC), previous=step.at
            ).isoformat()
        out = await self.call("POST", f"{self.base(step)}/findings", body)
        self.keep(step.body.get("key"), "finding", step.record, out)

    async def verify(self, step: _Step) -> None:
        made = self.get(step.body["finding"], "finding")
        body = dict(step.body["with"])
        for name in ("from", "to"):
            if name in body:
                body[f"time_{name}"] = when(
                    body.pop(name), now=datetime.now(UTC), previous=step.at
                ).isoformat()
        await self.call("POST", f"{self.base(step)}/findings/{made.id}/verifications", body)

    async def decide(self, step: _Step) -> None:
        made = self.get(step.body["finding"], "finding")
        current = await self.finding(step.body["finding"])
        await self.call(
            "POST",
            f"{self.base(step)}/findings/{made.id}/{'confirm' if step.do == 'confirm' else 'refute'}",
            {"version": current["version"], "note": self.text(step.body.get("note", ""))},
        )

    async def add_task(self, step: _Step) -> None:
        b = step.body["with"]
        body = {
            "title": b["title"],
            "description": self.text(b.get("description", "")),
            "team_id": self.team(b["team"]) if b.get("team") else None,
            "assignee_id": self.person(b["assignee"]) if b.get("assignee") else None,
            "due_at": self.due(b.get("due")),
            "checklist": b.get("checklist", []),
            "parent_id": self.get(b["parent"], "task").id if b.get("parent") else None,
        }
        out = await self.call("POST", f"{self.base(step)}/tasks", body)
        self.keep(step.body.get("key"), "task", step.record, out)

    def _task_path(self, key: str) -> str:
        made = self.get(key, "task")
        return f"/api/incidents/{self.get(made.record, 'record').id}/tasks/{made.id}"

    async def task_update(self, step: _Step) -> None:
        current = await self.task(step.body["task"])
        body = dict(step.body["with"])
        if "due" in body:
            body["due_at"] = self.due(body.pop("due"))
        if "assignee" in body:
            body["assignee_id"] = self.person(body.pop("assignee"))
        await self.call("PATCH", self._task_path(step.body["task"]), {"version": current["version"], **body})

    async def tick(self, step: _Step) -> None:
        current = await self.task(step.body["task"])
        for index in step.body.get("items", [step.body.get("item", 0)]):
            item = current["checklist"][index]
            await self.call(
                "PATCH",
                f"{self._task_path(step.body['task'])}/checklist/{item['id']}",
                {"version": item["version"], "done": True},
            )

    async def claim(self, step: _Step) -> None:
        current = await self.task(step.body["task"])
        await self.call(
            "POST", f"{self._task_path(step.body['task'])}/{step.do}", {"version": current["version"]}
        )

    async def handover(self, step: _Step) -> None:
        """Ostraka's draft for the stretch since the last handover, with anything the file adds."""
        made = await self.call("GET", f"{self.base(step)}/handover/draft")
        body = made["text"]
        if extra := step.body.get("add"):
            body = f"{body}\n\n{self.text(extra)}"
        covers = {k: made[k] for k in ("from_position", "through_position")}
        await self.call("POST", f"{self.base(step)}/handover", {"body": body, **covers})

    async def nudge(self, step: _Step) -> None:
        await self.call(
            "POST", f"{self._task_path(step.body['task'])}/nudge", {"message": step.body.get("message", "")}
        )

    async def wait_on(self, step: _Step) -> None:
        await self.call(
            "POST",
            f"{self._task_path(step.body['task'])}/blocked-by",
            {"ref": self.get(step.body["on"], "task").ref},
        )

    async def comment(self, step: _Step) -> None:
        b = step.body
        body: dict[str, Any] = {"body": self.text(b["body"])}
        if on := b.get("on"):
            target = self.get(on, "finding", "asset", "observable", "task")
            body["target_type"], body["target_id"] = target.kind, target.id
        if reply := b.get("reply_to"):
            parent = self.get(reply, "comment")
            body["parent_id"] = parent.id
            body["target_type"] = parent.data.get("target_type", "incident")
            body["target_id"] = parent.data.get("target_id")
        out = await self.call("POST", f"{self.base(step)}/comments", body)
        self.keep(b.get("key"), "comment", step.record, out)

    async def link(self, step: _Step) -> None:
        other = self.get(step.body["to"], "record")
        await self.call(
            "POST", f"{self.base(step)}/links", {"ref": other.ref, "relation": step.body["relation"]}
        )

    async def promote(self, step: _Step) -> None:
        body = dict(step.body["with"])
        body["summary"] = self.text(body.get("summary", ""))
        out = await self.call("POST", f"{self.base(step)}/promote", body)
        await self._promoted(step.body["key"], out["id"])

    async def _promoted(self, key: str, record_id: str) -> None:
        out = await self.call("GET", f"/api/incidents/{record_id}")
        self.made[key] = _Made("record", out["id"], out["ref"], key, out)
        self.outcome.records.append(out["ref"])
        # The promoted incident carries the file's tag like everything else it made
        if tag := self.data.get("tag"):
            await self.call(
                "PATCH",
                f"/api/incidents/{out['id']}",
                {"version": out["version"], "tags": [*out["tags"], tag]},
            )
            out["tags"] = [*out["tags"], tag]

    async def go(self, steps: list[_Step]) -> Outcome:
        for step in steps:
            self.step = step
            await self._open_steps(step)
            handler = _STEPS[step.do] if step.do != "declare" else _Run.declare
            await handler(self, step)
            self.outcome.steps += 1
        return self.outcome


_STEPS: dict[str, Callable[[_Run, _Step], Awaitable[None]]] = {
    "declare": _Run.declare,
    "update": _Run.update,
    "member": _Run.member,
    "team": _Run.add_team,
    "playbook": _Run.playbook,
    "observable": _Run.observable,
    "sighting": _Run.sighting,
    "asset": _Run.asset,
    "asset_update": _Run.asset_update,
    "asset_link": _Run.asset_link,
    "finding": _Run.add_finding,
    "verify": _Run.verify,
    "confirm": _Run.decide,
    "refute": _Run.decide,
    "task": _Run.add_task,
    "task_update": _Run.task_update,
    "tick": _Run.tick,
    "claim": _Run.claim,
    "release": _Run.claim,
    "nudge": _Run.nudge,
    "wait_on": _Run.wait_on,
    "comment": _Run.comment,
    "link": _Run.link,
    "promote": _Run.promote,
    "decide": _Run.choose,
    "assess": _Run.assess,
    "hypothesis": _Run.hypothesis,
    "evidence": _Run.evidence,
    "rate": _Run.rate,
    "reject": _Run.reject,
    "lesson": _Run.lesson,
    "handover": _Run.handover,
}


# ---- people, teams and tokens -----------------------------------------------------------------------


async def _teams(db: AsyncSession, data: dict[str, Any], outcome: Outcome) -> dict[str, str]:
    wanted = {t["name"]: t for t in data.get("teams", [])}
    wanted.update(
        {
            p["team"]: {"name": p["team"]}
            for p in data.get("people", [])
            if p.get("team") and p["team"] not in wanted
        }
    )
    teams: dict[str, str] = {}
    for name, spec in wanted.items():
        team = await db.scalar(select(Team).where(Team.name == name))
        if team is None:
            team = Team(name=name, description=spec.get("description", ""))
            db.add(team)
            await db.flush()
            outcome.teams_created += 1
        teams[name] = str(team.id)
    for team in (await db.scalars(select(Team))).all():
        teams.setdefault(team.name, str(team.id))
    return teams


async def _people(
    db: AsyncSession, data: dict[str, Any], teams: dict[str, str], outcome: Outcome
) -> dict[str, User]:
    people: dict[str, User] = {}
    for spec in data.get("people", []):
        email = spec["email"].strip().lower()
        user = await db.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            user = User(
                email=email,
                name=spec["name"],
                role=Role(spec.get("role", "responder")),
                team_id=uuid.UUID(teams[spec["team"]]) if spec.get("team") else None,
                password_hash=UNUSABLE,
                totp_secret="",
                notify_email=False,
                notify_push=False,
            )
            db.add(user)
            await db.flush()
            outcome.people_created += 1
        elif not user.is_active:
            raise ImportProblem(f"{email} is a deactivated account; reactivate it or leave it out.")
        people[spec["key"]] = user
    return people


async def _tokens(
    db: AsyncSession, people: dict[str, User], name: str
) -> tuple[dict[str, str], list[ApiToken]]:
    """A short-lived token for each person in the file, so each step is made as them."""
    raws: dict[str, str] = {}
    rows: list[ApiToken] = []
    for key, user in people.items():
        raw = branding.TOKEN_PREFIX + new_token(30)
        token = ApiToken(
            user_id=user.id,
            name=name[:80],
            prefix=raw[: len(branding.TOKEN_PREFIX) + 6],
            token_hash=token_hash(raw),
            scope=TokenScope.FULL,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(token)
        raws[key], rows = raw, [*rows, token]
    return raws, rows


async def run(path: Path, *, http: httpx.AsyncClient | None = None) -> Outcome:
    """Load a file. Stops at the first step that fails; what came before it stays, as in the browser."""
    data = load(path)
    now = datetime.now(UTC)
    steps = _plan(data, now)
    outcome = Outcome()
    async with sessions()() as db:
        teams = await _teams(db, data, outcome)
        people = await _people(db, data, teams, outcome)
        raws, tokens = await _tokens(db, people, f"import {path.name}")
        audit.record(db, "records_imported", file=path.name, records=len(data.get("records", [])))
        await db.commit()
        token_ids = [t.id for t in tokens]

    from ostraka.main import create_app  # here, not at the top: the app imports this module's neighbours

    own = http is None
    client = http or httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://ostraka.import"
    )
    try:
        job = _Run(data, client, people, raws)
        job.teams = teams
        job.outcome = outcome
        async with sessions()() as db:
            from ostraka.models import Playbook

            job.playbooks = {p.name: str(p.id) for p in (await db.scalars(select(Playbook))).all()}
        await job.go(steps)
        await notify.wait()
        await webhooks.wait()  # the CLI exits when this returns; let sends in flight finish
    finally:
        if own:
            await client.aclose()
            await live.broker().stop()
        async with sessions()() as db:
            await db.execute(
                update(ApiToken).where(ApiToken.id.in_(token_ids)).values(revoked_at=datetime.now(UTC))
            )
            await db.commit()
    log.info("import: %s records, %s steps from %s", len(outcome.records), outcome.steps, path.name)
    return outcome


async def remove(path: Path) -> tuple[int, int, int]:
    """Delete what a file loaded: records carrying its tag, then its placeholder people and empty teams.
    Real accounts the file only matched by email are left alone."""
    data = load(path)
    tag = data.get("tag")
    if not tag:
        raise ImportProblem(f'{path}: without a "tag", Ostraka can\'t tell what this file loaded.')
    async with sessions()() as db:
        records = (await db.scalars(select(Incident.id).where(Incident.tags.contains([tag])))).all()
        # The database cascades to everything a record holds: tasks, findings, comments, links, activity
        await db.execute(delete(Incident).where(Incident.id.in_(records)))
        # Indicators are shared between records; drop only those nothing else uses now
        await db.execute(
            delete(Observable).where(
                ~select(IncidentObservable.id)
                .where(IncidentObservable.observable_id == Observable.id)
                .exists()
            )
        )

        emails = [p["email"].strip().lower() for p in data.get("people", [])]
        people = (
            await db.scalars(
                select(User).where(func.lower(User.email).in_(emails), User.password_hash == UNUSABLE)
            )
        ).all()
        removed_people = 0
        for user in people:
            # Someone still named on other records' activity keeps their name there: deactivate instead
            if await db.scalar(
                select(func.count()).select_from(ActivityEvent).where(ActivityEvent.actor_id == user.id)
            ):
                user.is_active = False
            else:
                await db.execute(delete(User).where(User.id == user.id))
                removed_people += 1
        await db.flush()

        removed_teams = 0
        for spec in data.get("teams", []):
            team = await db.scalar(select(Team).where(Team.name == spec["name"]))
            if team and not await db.scalar(
                select(func.count()).select_from(User).where(User.team_id == team.id)
            ):
                await db.execute(delete(Team).where(Team.id == team.id))
                removed_teams += 1
        audit.record(db, "records_removed", file=path.name, records=len(records))
        await db.commit()
    return len(records), removed_people, removed_teams
