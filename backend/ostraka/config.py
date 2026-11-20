"""Settings, read from `OSTRAKA_*` environment variables.

Outside development and test, ostraka refuses to start with a missing, short or placeholder secret,
so a deployment can't come up on a key that's published in the repo.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "change-me-in-production",
        "change-me-in-production-use-a-strong-random-key",
        "change-me-in-production-use-strong-key",
        "your-super-secret-key-change-in-production",
    }
)
MIN_SECRET_LENGTH = 32
# Where links point when OSTRAKA_PUBLIC_URL isn't set and there's no request to take an address from
LOCAL_URL = "http://localhost:24747"


class Environment(StrEnum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"
    TEST = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OSTRAKA_", env_file=".env", extra="ignore")

    env: Environment = Environment.PRODUCTION
    secret_key: str = ""
    database_url: str = "postgresql+asyncpg://ostraka@localhost:5432/ostraka"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    # Origin the browser uses, e.g. https://ostraka.example.org; used for links in emails and pushes.
    # Left empty, links the API hands back use the address the request came to, and everything else
    # (emails, pushes, the CLI's links) uses LOCAL_URL
    public_url: str = ""
    # Who reports are prepared for, on their cover ("Contoso Ltd"); left off when empty
    organisation: str = ""
    ai_provider: str = Field(default="none", pattern="^(none|ollama|anthropic)$")
    # The AI rephrase of a finished draft. TLP:RED and TLP:AMBER+STRICT only ever use Ollama.
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    ai_timeout_seconds: float = 60

    access_token_minutes: int = 15
    session_days: int = 14
    # Invitations, the first admin's link and access resets: each works once, within this
    invitation_hours: int = 24
    # Proxies in front of the backend that each add the address they were reached from to
    # X-Forwarded-For: 1 is the web container's nginx, 2 with Caddy, the homelab proxy or your own in
    # front of that. Addresses are read that many from the right, so one a client writes is never believed
    proxy_hops: int = Field(default=1, ge=0, le=5)
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Email goes by SMTP or Microsoft Graph. Without the transport's settings, email is off.
    mail_transport: str = Field(default="smtp", pattern="^(smtp|graph)$")

    # SMTP with STARTTLS (port 587) or implicit TLS (465)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: str = Field(default="starttls", pattern="^(starttls|ssl)$")

    # Microsoft Graph: an Entra app registration with the Mail.Send application permission, sending
    # as one mailbox (graph_sender). Limit it to that mailbox in Exchange Online (see README).
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_sender: str = ""

    # Nudges: when the scheduler reminds and escalates (see docs/ROADMAP.md, Task escalation)
    scheduler_enabled: bool = True
    scheduler_seconds: int = 60
    due_soon_minutes: int = 60
    overdue_repeat_hours: float = 4
    escalate_lead_hours: float = 4
    escalate_commander_hours: float = 24

    @property
    def public_origin(self) -> str:
        """The public URL without a trailing slash, or LOCAL_URL when it isn't set."""
        return self.public_url.strip().rstrip("/") or LOCAL_URL

    @model_validator(mode="after")
    def _refuse_weak_secret(self) -> Settings:
        if self.env is Environment.PRODUCTION and (
            self.secret_key.strip().lower() in PLACEHOLDER_SECRETS or len(self.secret_key) < MIN_SECRET_LENGTH
        ):
            raise ValueError(
                f"OSTRAKA_SECRET_KEY must be at least {MIN_SECRET_LENGTH} random characters in production "
                "(make env writes one into .env)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
