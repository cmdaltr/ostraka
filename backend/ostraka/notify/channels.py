"""The ways a notification leaves ostraka: email (SMTP or Microsoft Graph), browser push, and a team's
Teams channel.

Each sender is a plain function of what to send, so tests replace `fetch` and `smtp` and run
everything else for real.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
import ssl
import time
from email.message import EmailMessage
from urllib.parse import quote, urlencode, urlsplit

import httpx

from ostraka.config import get_settings
from ostraka.log import get_logger
from ostraka.notify import push

log = get_logger(__name__)

# Workflows webhooks ("Post to a channel when a webhook request is received") live on these hosts.
# Anything else is refused, so a team lead can't point ostraka's requests at an internal address.
TEAMS_HOSTS = (".logic.azure.com", ".powerplatform.com")


def teams_webhook_problem(url: str) -> str | None:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host.endswith(TEAMS_HOSTS):
        return (
            "Use the URL from a Teams Workflows webhook (Post to a channel when a webhook request is "
            "received). It starts with https:// and ends in logic.azure.com or powerplatform.com."
        )
    return None


async def fetch(url: str, *, content: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """POST and return the status code and body. Replaced in tests."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.post(url, content=content, headers=headers)
        return response.status_code, response.content


async def post(url: str, *, content: bytes, headers: dict[str, str]) -> int:
    return (await fetch(url, content=content, headers=headers))[0]


def _smtp_send(message: EmailMessage) -> None:
    settings = get_settings()
    context = ssl.create_default_context()
    if settings.smtp_tls == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=20, context=context
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        server.starttls(context=context)
    with server:
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


smtp = _smtp_send  # replaced in tests


def email_enabled() -> bool:
    settings = get_settings()
    if settings.mail_transport == "graph":
        return all(
            (
                settings.graph_tenant_id,
                settings.graph_client_id,
                settings.graph_client_secret,
                settings.graph_sender,
            )
        )
    return bool(settings.smtp_host and settings.smtp_from)


GRAPH = "https://graph.microsoft.com/v1.0"
_graph_token: tuple[str, float] = ("", 0.0)


async def _graph_access_token() -> str:
    """An app-only token (client credentials), reused until a minute before it expires."""
    global _graph_token
    token, expires = _graph_token
    if token and expires > time.monotonic():
        return token
    settings = get_settings()
    status, body = await fetch(
        f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/token",
        content=urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": settings.graph_client_id,
                "client_secret": settings.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
            }
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = json.loads(body or b"{}")
    if status != 200 or "access_token" not in data:
        raise RuntimeError(
            f"Entra ID refused the token request ({status}): {data.get('error_description', '')[:200]}"
        )
    _graph_token = (data["access_token"], time.monotonic() + int(data.get("expires_in", 3600)) - 60)
    return data["access_token"]


async def _send_graph(to: str, subject: str, text: str) -> None:
    sender = get_settings().graph_sender
    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": False,
    }
    status, body = await fetch(
        f"{GRAPH}/users/{quote(sender)}/sendMail",
        content=json.dumps(message).encode(),
        headers={
            "Authorization": f"Bearer {await _graph_access_token()}",
            "Content-Type": "application/json",
        },
    )
    if status == 401:  # a revoked or rotated secret: forget the token so the next send asks again
        global _graph_token
        _graph_token = ("", 0.0)
    if status != 202:
        raise RuntimeError(f"Graph sendMail answered {status}: {body[:300]!r}")


async def send_email(to: str, subject: str, text: str) -> bool:
    if not email_enabled():
        return False
    if get_settings().mail_transport == "graph":
        try:
            await _send_graph(to, subject, text)
            return True
        except Exception as exc:
            log.warning("email to %s via Graph failed: %s", to, exc)
            return False
    message = EmailMessage()
    message["From"] = get_settings().smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    try:
        await asyncio.to_thread(smtp, message)
        return True
    except Exception as exc:
        log.warning("email to %s failed: %s", to, exc)
        return False


async def send_push(endpoint: str, p256dh: str, auth: str, payload: dict, *, urgent: bool = False) -> int:
    """The push service's status code; 404 and 410 mean the subscription has gone."""
    request = push.build(endpoint, p256dh, auth, json.dumps(payload).encode(), urgent=urgent)
    try:
        return await post(request.url, content=request.body, headers=request.headers)
    except Exception as exc:
        log.warning("push to %s failed: %s", urlsplit(endpoint).hostname, exc)
        return 0


def adaptive_card(title: str, text: str, url: str) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True,
                        },
                        {"type": "TextBlock", "text": text, "wrap": True},
                    ],
                    "actions": [{"type": "Action.OpenUrl", "title": "Open in Ostraka", "url": url}],
                },
            }
        ],
    }


async def send_teams(webhook: str, title: str, text: str, url: str) -> bool:
    if teams_webhook_problem(webhook):
        return False
    try:
        status = await post(
            webhook,
            content=json.dumps(adaptive_card(title, text, url)).encode(),
            headers={"Content-Type": "application/json"},
        )
    except Exception as exc:
        log.warning("teams webhook failed: %s", exc)
        return False
    if status >= 300:
        log.warning("teams webhook answered %s", status)
    return status < 300
