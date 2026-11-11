"""The optional AI rephrase of a finished update, with the TLP guard.

The model is given the draft's lines, indicators already masked, and asked to reword each one. It
decides nothing: `ostraka.standard.rephrase` checks what comes back, and anything that drops or
changes a claim, citation or probability term is rejected.
"""

from __future__ import annotations

import json
import re

import httpx

from ostraka.config import get_settings
from ostraka.log import get_logger
from ostraka.models import Incident
from ostraka.notify import restricted

log = get_logger(__name__)

PROMPT = (
    "You edit incident updates for senior stakeholders. Reword each line below so it reads plainly and "
    "smoothly. Rules: keep every reference (like FND-12 or ASM-4), every number, every bracketed "
    "placeholder (like [ip address]) and every probability phrase with its percentage exactly as written. "
    "Keep the words confirmed, lead, refuted, ruled out, no evidence and every negation. Don't add facts, "
    "likelihood words, certainty words or adjectives. Return only a JSON array of strings, one per line, "
    "in the same order and the same number of lines.\n\nLines:\n"
)


class Unavailable(Exception):
    """No model may be used for this incident, with the reason."""


def provider_for(incident: Incident) -> str:
    """Which provider may rephrase this incident's update. The TLP guard keeps TLP:RED and
    TLP:AMBER+STRICT on local Ollama, whatever the installation's choice."""
    settings = get_settings()
    ollama = bool(settings.ollama_url and settings.ollama_model)
    anthropic = bool(settings.anthropic_api_key and settings.anthropic_model)
    if settings.ai_provider == "none":
        raise Unavailable("AI is switched off for this installation; the draft stands as Ostraka wrote it.")
    if restricted(incident):
        if ollama:
            return "ollama"
        raise Unavailable(
            f"{incident.ref} is TLP:{incident.tlp.value.upper()}, so only a local Ollama model may read it, "
            "and none is configured."
        )
    if settings.ai_provider == "anthropic" and anthropic:
        return "anthropic"
    if settings.ai_provider == "ollama" and ollama:
        return "ollama"
    raise Unavailable(f"The {settings.ai_provider} provider isn't fully configured (model or key missing).")


async def complete(provider: str, prompt: str) -> str:
    """One completion. Replaced in tests."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
        if provider == "ollama":
            r = await client.post(
                f"{settings.ollama_url.rstrip('/')}/api/generate",
                json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            )
            r.raise_for_status()
            return r.json().get("response", "")
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": settings.anthropic_model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        return "".join(part.get("text", "") for part in r.json().get("content", []))


def parse(reply: str) -> list[str] | None:
    """The JSON array of lines in a model's reply, or None."""
    match = re.search(r"\[.*\]", reply, re.S)
    if not match:
        return None
    try:
        lines = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        return None
    return [x.strip() for x in lines]


async def rephrase(provider: str, lines: list[str]) -> list[str] | None:
    """The reworded lines, or None if the model failed or replied with something else."""
    try:
        reply = await complete(provider, PROMPT + json.dumps(lines, ensure_ascii=False, indent=1))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("rephrase with %s failed: %s", provider, e)
        return None
    return parse(reply)
