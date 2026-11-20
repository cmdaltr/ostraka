"""Sign-in lockout, kept per account and address.

Five wrong passwords or codes from one address lock that account out from that address for
`lockout_minutes`. Keyed on the account alone, anyone who knew an address could lock its owner out
by failing on purpose; keyed on both, the owner signing in from elsewhere isn't affected, and
guessing still stops after five tries from each address. Every account also needs its authenticator,
so a password guessed from many addresses opens nothing by itself.

The address is `sessions.client_ip`, read from the trusted proxies only, so a client can't change
it with a header. Counts live in Redis (in-process with `OSTRAKA_REDIS_URL=memory://`, as in tests)
and expire on their own. If Redis can't be reached, sign-in carries on unthrottled rather than
shutting everyone out, and says so in the log. Request throttles (Forgot password) count in this
process instead: they shut nobody out of their account, and failing open would make an outage an
unlimited way to have Ostraka send email.
"""

from __future__ import annotations

import time
import uuid

from redis.asyncio import Redis

from ostraka.config import get_settings
from ostraka.log import get_logger

log = get_logger(__name__)

PREFIX = "ostraka:lockout"


class _Memory:
    """Expiring counters for one process."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[int, float]] = {}

    def _live(self, key: str) -> int:
        value, expires = self.values.get(key, (0, 0.0))
        return value if expires > time.monotonic() else 0

    async def get(self, key: str) -> int:
        return self._live(key)

    async def incr(self, key: str, seconds: int) -> int:
        value = self._live(key) + 1
        expires = self.values[key][1] if value > 1 else time.monotonic() + seconds
        self.values[key] = (value, expires)
        return value

    async def set(self, key: str, seconds: int) -> None:
        self.values[key] = (1, time.monotonic() + seconds)

    async def clear(self, prefix: str) -> None:
        for key in [k for k in self.values if k.startswith(prefix)]:
            del self.values[key]


class _Redis:
    def __init__(self, url: str) -> None:
        self.redis = Redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> int:
        return int(await self.redis.get(key) or 0)

    async def incr(self, key: str, seconds: int) -> int:
        async with self.redis.pipeline(transaction=True) as pipe:
            value, _ = await pipe.incr(key).expire(key, seconds, nx=True).execute()
        return int(value)

    async def set(self, key: str, seconds: int) -> None:
        await self.redis.set(key, 1, ex=seconds)

    async def clear(self, prefix: str) -> None:
        keys = [key async for key in self.redis.scan_iter(f"{prefix}*")]
        if keys:
            await self.redis.delete(*keys)


_store: _Memory | _Redis | None = None
# Throttle counts while Redis can't be reached
_fallback = _Memory()


def _counters() -> _Memory | _Redis:
    global _store
    if _store is None:
        url = get_settings().redis_url
        _store = _Memory() if url.startswith("memory://") else _Redis(url)
    return _store


def _keys(user_id: uuid.UUID, ip: str) -> tuple[str, str]:
    return f"{PREFIX}:failed:{user_id}:{ip}", f"{PREFIX}:locked:{user_id}:{ip}"


async def locked(user_id: uuid.UUID, ip: str) -> bool:
    """Whether this account is locked out from this address."""
    try:
        return await _counters().get(_keys(user_id, ip)[1]) > 0
    except Exception as exc:  # noqa: BLE001 (a Redis outage mustn't stop everyone signing in)
        log.warning("lockout: can't read (%s); not throttling", exc)
        return False


async def failed(user_id: uuid.UUID, ip: str) -> bool:
    """Count a wrong password or code from this address. True when it locks the account here."""
    settings = get_settings()
    seconds = settings.lockout_minutes * 60
    failures, lock = _keys(user_id, ip)
    try:
        counters = _counters()
        if await counters.incr(failures, seconds) < settings.max_failed_logins:
            return False
        await counters.set(lock, seconds)
        await counters.clear(failures)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("lockout: can't count (%s); not throttling", exc)
        return False


async def clear(user_id: uuid.UUID, ip: str | None = None) -> None:
    """Forget failures and locks: from one address after a sign-in there, or from every address when
    an admin resets the account."""
    try:
        counters = _counters()
        if ip is None:
            await counters.clear(f"{PREFIX}:failed:{user_id}:")
            await counters.clear(f"{PREFIX}:locked:{user_id}:")
        else:
            for key in _keys(user_id, ip):
                await counters.clear(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("lockout: can't clear (%s)", exc)


async def throttled(kind: str, key: str, limit: int, seconds: int) -> bool:
    """Count one more request of `kind` from `key` (an address, or a hash of an email); True once
    there have been more than `limit` within `seconds`. Without Redis, counts in this process."""
    name = f"{PREFIX}:throttle:{kind}:{key}"
    try:
        return await _counters().incr(name, seconds) > limit
    except Exception as exc:  # noqa: BLE001
        log.warning("lockout: can't throttle %s in Redis (%s); counting in this process", kind, exc)
        return await _fallback.incr(name, seconds) > limit
