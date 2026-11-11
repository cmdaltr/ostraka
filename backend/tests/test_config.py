import pytest
from pydantic import ValidationError

from ostraka.config import Settings


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "change-me-in-production-use-a-strong-random-key",
        "your-super-secret-key-change-in-production",
        "short",
    ],
)
def test_production_refuses_weak_secrets(secret, monkeypatch):
    monkeypatch.delenv("OSTRAKA_ENV", raising=False)
    with pytest.raises(ValidationError, match="OSTRAKA_SECRET_KEY"):
        Settings(_env_file=None, secret_key=secret)


def test_production_accepts_a_long_random_secret(monkeypatch):
    monkeypatch.delenv("OSTRAKA_ENV", raising=False)
    s = Settings(_env_file=None, secret_key="k" * 48)
    assert s.env == "production"


def test_test_environment_needs_no_secret(monkeypatch):
    monkeypatch.delenv("OSTRAKA_SECRET_KEY", raising=False)
    assert Settings(_env_file=None, env="test").secret_key == ""


def test_ai_provider_is_constrained():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="test", ai_provider="openai")
