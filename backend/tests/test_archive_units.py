"""Archiving's fixed rules: when a closed record moves on, and who may change or restore it."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ostraka import archive
from ostraka.auth import policy
from ostraka.incident_schemas import ArchivingIn
from ostraka.models import ArchiveState, IncidentStatus, Role, User

CLOSED = datetime(2026, 9, 1, 12, tzinfo=UTC)
SOC = uuid.uuid4()


def person(role: Role, team=SOC) -> User:
    return User(id=uuid.uuid4(), role=role, team_id=team, email="x@example.org", name="X")


def due(days: float, *, restored_days: float | None = None, auto=True, status_=IncidentStatus.CLOSED):
    restored = CLOSED + timedelta(days=restored_days) if restored_days is not None else None
    return archive.state_due(
        status_=status_,
        closed_at=CLOSED,
        restored_at=restored,
        auto=auto,
        archive_days=30,
        cold_days=90,
        now=CLOSED + timedelta(days=days),
    )


def test_a_closed_record_is_archived_at_30_days_and_cold_at_90():
    assert due(29.9) is ArchiveState.ACTIVE
    assert due(30) is ArchiveState.ARCHIVED
    assert due(89.9) is ArchiveState.ARCHIVED
    assert due(90) is ArchiveState.COLD
    assert due(400) is ArchiveState.COLD  # long closed before archiving existed: straight to cold


def test_an_open_record_or_one_kept_out_stays_active():
    assert due(400, status_=IncidentStatus.OPEN) is ArchiveState.ACTIVE
    assert due(400, auto=False) is ArchiveState.ACTIVE


def test_a_restore_starts_the_days_again():
    assert archive.counted_from(CLOSED, None) == CLOSED
    assert archive.counted_from(CLOSED, CLOSED + timedelta(days=95)) == CLOSED + timedelta(days=95)
    assert archive.counted_from(None, CLOSED) is None
    assert due(100, restored_days=95) is ArchiveState.ACTIVE
    assert due(125, restored_days=95) is ArchiveState.ARCHIVED
    # a restore before the record last closed doesn't hold it back
    assert archive.counted_from(CLOSED, CLOSED - timedelta(days=1)) == CLOSED


def test_the_next_move_is_said_with_its_date():
    def move(state: ArchiveState, **kw):
        return archive.next_move(
            status_=kw.get("status_", IncidentStatus.CLOSED),
            closed_at=CLOSED,
            restored_at=None,
            auto=kw.get("auto", True),
            archive_days=30,
            cold_days=90,
            state=state,
        )

    assert move(ArchiveState.ACTIVE) == (ArchiveState.ARCHIVED, CLOSED + timedelta(days=30))
    assert move(ArchiveState.ARCHIVED) == (ArchiveState.COLD, CLOSED + timedelta(days=90))
    assert move(ArchiveState.COLD) is None
    assert move(ArchiveState.ACTIVE, auto=False) is None
    assert move(ArchiveState.ACTIVE, status_=IncidentStatus.OPEN) is None


def test_a_records_days_must_put_cold_store_after_archiving():
    assert ArchivingIn(version=1, auto=True, archive_after_days=14, cold_after_days=60)
    for archive_days, cold_days in ((30, 30), (60, 30), (0, 90), (30, 3651)):
        with pytest.raises(ValidationError):
            ArchivingIn(version=1, auto=True, archive_after_days=archive_days, cold_after_days=cold_days)


def test_anyone_who_works_records_may_ask_for_one_back_but_not_observers():
    assert policy.can_ask_restore(person(Role.RESPONDER), can_view=True) is None
    assert policy.can_ask_restore(person(Role.TEAM_LEAD), can_view=True) is None
    assert "Observers" in policy.can_ask_restore(person(Role.OBSERVER), can_view=True)
    assert policy.can_ask_restore(person(Role.RESPONDER), can_view=False) is not None


def test_another_senior_person_approves_a_restore():
    lead = person(Role.TEAM_LEAD)
    admin = person(Role.ADMIN, None)
    asker = person(Role.RESPONDER)
    assert policy.can_decide_archive_request(admin, requester_id=asker.id, leads_a_team_on_it=False) is None
    assert policy.can_decide_archive_request(lead, requester_id=asker.id, leads_a_team_on_it=True) is None
    # a lead whose team isn't on the record, a responder, an observer: no
    assert policy.can_decide_archive_request(lead, requester_id=asker.id, leads_a_team_on_it=False)
    for role in (Role.RESPONDER, Role.OBSERVER):
        assert policy.can_decide_archive_request(person(role), requester_id=asker.id, leads_a_team_on_it=True)
    # never your own request, even as an admin
    assert "Someone else" in policy.can_decide_archive_request(
        admin, requester_id=admin.id, leads_a_team_on_it=True
    )


def test_archiving_is_set_by_whoever_manages_the_record():
    assert policy.can_set_retention(person(Role.RESPONDER), can_manage=True) is None
    assert policy.can_set_retention(person(Role.ADMIN), can_manage=False) is not None


def test_a_record_can_be_archived_early_only_further_along_and_once_closed():
    assert archive.targets(IncidentStatus.CLOSED, ArchiveState.ACTIVE) == [
        ArchiveState.ARCHIVED,
        ArchiveState.COLD,
    ]
    assert archive.targets(IncidentStatus.CLOSED, ArchiveState.ARCHIVED) == [ArchiveState.COLD]
    assert archive.targets(IncidentStatus.CLOSED, ArchiveState.COLD) == []
    assert archive.targets(IncidentStatus.OPEN, ArchiveState.ACTIVE) == []


def test_archiving_early_is_asked_by_whoever_manages_the_record():
    assert policy.can_ask_archive(person(Role.RESPONDER), manages=True) is None
    assert policy.can_ask_archive(person(Role.ADMIN), manages=False) is not None
