"""Who may do what to whom. Pure functions, so every rule has a unit test."""

from __future__ import annotations

import uuid

from ostraka.models import IncidentRole, Role, Tlp, User

# Roles a team lead may hand out. Only admins create team leads and admins.
LEAD_INVITABLE = frozenset({Role.RESPONDER, Role.OBSERVER})


def can_invite(actor: User, role: Role, team_id: uuid.UUID | None) -> str | None:
    """Why `actor` may not invite someone as `role` into `team_id`, or None if they may."""
    if role is not Role.ADMIN and team_id is None:
        return "Everyone except an admin needs a team."
    if actor.role is Role.ADMIN:
        return None
    if actor.role is not Role.TEAM_LEAD:
        return "Only admins and team leads can invite people."
    if team_id != actor.team_id:
        return "Team leads can only invite people into their own team."
    if role not in LEAD_INVITABLE:
        return "Team leads can invite responders and observers; ask an admin for anything else."
    return None


# Someone who can run the installation: invite people, set up teams, change accounts
LEADERS = frozenset({Role.ADMIN, Role.TEAM_LEAD})


def seat_refusal(*, seats: int | None, held: int, leaders: int, role: Role, free: bool) -> str | None:
    """Why one more account that can sign in, as `role`, would go past the edition's limit, or None.
    `held` counts the accounts and open invitations already holding a place, `leaders` how many of
    them are admins or team leads. If none is, the last place is kept for one. `seats` None is a
    licence without a limit."""
    if seats is None:
        return None
    if held >= seats:
        if free:
            return (
                f"The free edition is for {seats} people who can sign in, and every place is taken. "
                "Deactivate an account or withdraw an invitation, or install a licence for more."
            )
        return (
            f"This installation's licence covers {seats} people who can sign in, and every place is "
            "taken. Deactivate an account or withdraw an invitation, or ask for more seats."
        )
    if held == seats - 1 and not leaders and role not in LEADERS:
        return "The last place is kept for an admin or team lead, so someone can run Ostraka."
    return None


def can_manage_licence(actor: User) -> str | None:
    """Install or remove the licence, and manage report brands: admins only."""
    return None if actor.role is Role.ADMIN else "Only admins can change the licence and report brands."


def can_manage_webhooks(actor: User) -> str | None:
    """Register, change or remove the installation's webhooks: admins only, since a webhook sends
    incident activity to a system outside ostraka."""
    return None if actor.role is Role.ADMIN else "Only admins can manage webhooks."


def can_manage_integrations(actor: User) -> str | None:
    """Connect, change or remove the Rowanholt and Shikari servers: admins only, since their tokens
    act in those products and their answers become part of incidents."""
    return None if actor.role is Role.ADMIN else "Only admins can connect Rowanholt and Shikari."


def can_manage_invitations_of(actor: User, team_id: uuid.UUID | None) -> bool:
    """See or revoke invitations into a team."""
    return actor.role is Role.ADMIN or (actor.role is Role.TEAM_LEAD and team_id == actor.team_id)


def can_change_account(actor: User, target: User) -> str | None:
    """Change someone's role or team, deactivate them, or reset their MFA: admins only, never themselves."""
    if actor.role is not Role.ADMIN:
        return "Only admins can change accounts."
    if actor.id == target.id:
        return "Ask another admin to change your own account."
    return None


def can_manage_playbooks(actor: User) -> bool:
    """Write, change or retire playbooks: admins and team leads."""
    return actor.role in (Role.ADMIN, Role.TEAM_LEAD)


def can_set_team_channel(actor: User, team_id: uuid.UUID) -> bool:
    """Connect or disconnect a team's Teams channel: admins, and that team's own lead."""
    return actor.role is Role.ADMIN or (actor.role is Role.TEAM_LEAD and actor.team_id == team_id)


def can_see_phone(viewer: User, subject: User) -> bool:
    """A colleague's out-of-hours phone: anyone who works incidents, and the person themselves.
    Observers get briefings, not phone numbers."""
    return viewer.id == subject.id or viewer.role is not Role.OBSERVER


def can_export(viewer: User, *, worked_on: bool, is_commander: bool, sensitive: bool) -> str | None:
    """Why `viewer` may not export an incident's reports, or None if they may. A report leaves
    ostraka, so it's for people who worked the incident (held a role in it or recorded something in
    it); a sensitive incident's reports are the commander's alone. Observers never export."""
    if viewer.role is Role.OBSERVER:
        return "Observers can read the briefing in Ostraka but not export it."
    if sensitive:
        return None if is_commander else "This incident is sensitive: only its commander can export reports."
    if worked_on or is_commander:
        return None
    return "Only people who have worked on this incident can export its reports."


# TLP levels whose incidents never signal, to anyone outside them, that they are being worked
_SILENT_TLP = frozenset({Tlp.RED, Tlp.AMBER_STRICT})


def may_signal_work_elsewhere(*, can_view: bool, tlp: Tlp) -> bool:
    """Whether someone working an indicator in another incident is mentioned to this viewer. An
    incident they can see always is (by name). One they can't is already counted in the overlap, so
    they're told only that it's being worked and whom to ask, and not even that for TLP:RED or
    TLP:AMBER+STRICT, whose activity stays inside the incident."""
    return can_view or tlp not in _SILENT_TLP


# The incident roles that answer for a regulator's deadlines
CLOCK_ROLES = frozenset({IncidentRole.COMMANDER, IncidentRole.COMMS})


def can_manage_clocks(actor: User, incident_role: IncidentRole | None) -> str | None:
    """Add, change, close or reopen a regulator clock: the incident's commander or comms, or an
    admin. Observers never."""
    if actor.role is Role.OBSERVER:
        return "Observers can see regulator clocks but not change them."
    if actor.role is Role.ADMIN or incident_role in CLOCK_ROLES:
        return None
    return "Only the incident commander, comms or an admin can add, change or close regulator clocks."


def can_mark_milestone(actor: User, incident_role: IncidentRole | None, *, is_owner: bool) -> str | None:
    """Record a clock's milestone as met (or undo that): whoever may manage the clock, and its owner."""
    if actor.role is not Role.OBSERVER and is_owner:
        return None
    refusal = can_manage_clocks(actor, incident_role)
    if refusal is None:
        return None
    if actor.role is Role.OBSERVER:
        return refusal
    return "Only the clock's owner, the incident commander, comms or an admin can mark its milestones."
