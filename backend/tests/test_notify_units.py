"""Notification pieces that need no database: push encryption, what may leave ostraka, policy, the hub."""

import asyncio
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import ec

from ostraka import live
from ostraka.auth import policy
from ostraka.models import NotificationKind, Role, Tlp
from ostraka.notify import channel_words, incident_post, outward, push
from ostraka.notify.channels import (
    chat_kind,
    chat_webhook_problem,
    slack_message,
    slack_webhook_problem,
    teams_webhook_problem,
)


def test_push_encryption_matches_rfc_8291_appendix_a():
    sender = ec.derive_private_key(
        int.from_bytes(push.unb64url("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1()
    )
    body = push.encrypt(
        b"When I grow up, I want to be a watermelon",
        push.unb64url(
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
        ),
        push.unb64url("BTBZMqHH6r4Tts7J_aSIgg"),
        salt=push.unb64url("DGv6ra1nlYgDCS1FRnbzlw"),
        sender=sender,
    )
    assert push.b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS"
        "6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_the_vapid_key_is_stable_and_a_p256_point():
    key = push.vapid_public_key()
    assert key == push.vapid_public_key()
    assert len(push.unb64url(key)) == 65 and push.unb64url(key)[0] == 4


def test_vapid_header_names_the_push_service_origin():
    import jwt

    header = push.vapid_header("https://updates.push.services.mozilla.com/wpush/v2/abc", now=1_000_000)
    token = header.split("t=")[1].split(",")[0]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert (
        claims["aud"] == "https://updates.push.services.mozilla.com"
        and claims["exp"] == 1_000_000 + 12 * 3600
    )


def incident(tlp: Tlp):
    return SimpleNamespace(ref="OST-26-007", codename="Folded Lantern", tlp=tlp)


def test_restricted_incidents_send_only_the_reference_and_code_name():
    title, body = outward(NotificationKind.OVERDUE, "TSK-3 Reset the CFO's MFA", "Details", incident(Tlp.RED))
    assert title == "A task is overdue in OST-26-007 Folded Lantern" and "CFO" not in body
    title, _ = outward(NotificationKind.OVERDUE, "TSK-3", "", incident(Tlp.AMBER_STRICT))
    assert title == "A task is overdue in OST-26-007 Folded Lantern"


def test_indicators_are_masked_in_anything_that_leaves():
    title, body = outward(
        NotificationKind.MENTION, "Block 203.0.113.9", "see evil.example", incident(Tlp.AMBER)
    )
    assert title == "Block [ip address]" and body == "see [domain]"


def test_only_workflow_webhooks_are_accepted():
    assert (
        teams_webhook_problem("https://prod-12.uksouth.logic.azure.com/workflows/x/triggers/manual") is None
    )
    assert (
        teams_webhook_problem("https://default1234.ab.environment.api.powerplatform.com/powerautomate/x")
        is None
    )
    assert teams_webhook_problem("http://prod-12.uksouth.logic.azure.com/x")  # not https
    assert teams_webhook_problem("https://evil.example/logic.azure.com")
    assert teams_webhook_problem("https://169.254.169.254/latest")


def test_only_slack_incoming_webhooks_are_accepted():
    assert slack_webhook_problem("https://hooks.slack.com/services/T000/B000/XXXX") is None
    assert slack_webhook_problem("https://hooks.slack-gov.com/services/T000/B000/XXXX") is None
    assert slack_webhook_problem("http://hooks.slack.com/services/T000/B000/XXXX")  # not https
    assert slack_webhook_problem("https://hooks.slack.com/workflows/T000/A000/1/abc")  # Workflow Builder
    assert slack_webhook_problem("https://hooks.slack.com.evil.example/services/x")
    assert slack_webhook_problem("https://evil.example/services/x?h=hooks.slack.com")


def test_a_chat_url_is_teams_or_slack_and_nothing_else():
    assert chat_kind("https://prod-1.uksouth.logic.azure.com/workflows/x") == "teams"
    assert chat_kind("https://hooks.slack.com/services/T/B/X") == "slack"
    assert chat_kind("https://10.0.0.5/hook") is None
    assert chat_webhook_problem("https://10.0.0.5/hook") and not chat_webhook_problem(
        "https://hooks.slack.com/services/T/B/X"
    )


def test_slack_text_cannot_open_links_or_ping_the_channel():
    message = slack_message("TSK-3 <!channel>", "see <https://evil.example|here> & more", "https://o/x")
    section = message["blocks"][1]["text"]["text"]
    assert "<!channel>" not in message["blocks"][0]["text"]["text"] and "&lt;!channel&gt;" in message["text"]
    assert section == "see &lt;https://evil.example|here&gt; &amp; more"
    assert message["blocks"][-1]["elements"][0]["url"] == "https://o/x"


def test_an_incident_channel_hears_about_turns_not_every_edit():
    assert channel_words("incident_updated", {"changes": {"phase": "scope"}}) == "The phase changed"
    assert channel_words("incident_updated", {"changes": {"status": "closed"}}) == "The record was closed"
    assert channel_words("incident_updated", {"changes": {"status": "open"}}) == "The record was reopened"
    assert channel_words("incident_updated", {"changes": {"tlp": "red"}}) == "The TLP changed"
    assert channel_words("incident_updated", {"changes": {"title": "x"}}) is None
    assert channel_words("finding_confirmed", {}) == "A finding was confirmed"
    assert channel_words("commented", {}) is None
    assert channel_words("task_added", {}) is None  # the team channels carry tasks


def test_a_restricted_incident_channel_post_names_only_the_record():
    title, text = incident_post(
        incident(Tlp.RED), "The phase changed", "Sam moved it: CFO mailbox 203.0.113.9"
    )
    assert title == "The phase changed in OST-26-007 Folded Lantern"
    assert "CFO" not in text and "203.0.113.9" not in text
    _, text = incident_post(incident(Tlp.AMBER), "The phase changed", "Block 203.0.113.9")
    assert text == "Block [ip address]"


def person(role: Role, team: str | None = "t1"):
    return SimpleNamespace(role=role, team_id=team)


def test_who_writes_playbooks():
    assert policy.can_manage_playbooks(person(Role.ADMIN))
    assert policy.can_manage_playbooks(person(Role.TEAM_LEAD))
    assert not policy.can_manage_playbooks(person(Role.RESPONDER))
    assert not policy.can_manage_playbooks(person(Role.OBSERVER))


def test_who_connects_a_team_channel():
    assert policy.can_set_team_channel(person(Role.ADMIN, None), "t1")
    assert policy.can_set_team_channel(person(Role.TEAM_LEAD, "t1"), "t1")
    assert not policy.can_set_team_channel(person(Role.TEAM_LEAD, "t2"), "t1")
    assert not policy.can_set_team_channel(person(Role.RESPONDER, "t1"), "t1")


def test_a_users_signals_reach_only_their_sockets():
    class Socket:
        def __init__(self):
            self.got = []

        async def send_json(self, message):
            self.got.append(message)

    hub = live.Hub()
    mine, theirs = Socket(), Socket()
    hub.users["u1"].add(mine)
    hub.users["u2"].add(theirs)
    asyncio.run(hub.deliver(f"{live.USER_CHANNEL}u1", {"type": "notification"}))
    assert mine.got == [{"type": "notification"}] and theirs.got == []
