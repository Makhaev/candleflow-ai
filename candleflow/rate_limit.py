"""Fixed-window rate limits for login attempts and assistant requests.

The counter store is a strategy: Redis in the running stack, memory in tests. When Redis
is unavailable the limiter fails open and logs it: losing the brute-force guard for a
moment is better than locking every user out of the workshop.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from candleflow.auth import DomainError
from candleflow.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window: int  # seconds
    message: str


LOGIN = Rule("login", 10, 300, "Слишком много попыток входа. Повторите через 5 минут.")
ASSISTANT = Rule("assistant", 20, 60, "Слишком много запросов к ассистенту. Повторите через минуту.")


class RateLimited(DomainError):
    def __init__(self, rule: Rule):
        super().__init__(rule.message, 429)
        self.retry_after = rule.window


class CounterStore(Protocol):
    async def increment(self, key: str, window: int) -> int: ...


class RedisStore:
    def __init__(self, url: str):
        self.redis = Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)

    async def increment(self, key: str, window: int) -> int:
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
            count, _ = await pipe.execute()
        return count


class MemoryStore:
    def __init__(self):
        self.counters: dict[str, int] = defaultdict(int)

    async def increment(self, key: str, window: int) -> int:
        self.counters[key] += 1
        return self.counters[key]


class RateLimiter:
    def __init__(self, store: CounterStore | None, enabled: bool = True):
        self.store = store
        self.enabled = enabled

    async def check(self, rule: Rule, subject: str) -> None:
        if not self.enabled or self.store is None:
            return
        bucket = int(time.time()) // rule.window
        try:
            count = await self.store.increment(f"rate:{rule.name}:{subject}:{bucket}", rule.window)
        except (RedisError, OSError):
            logger.warning("rate limiter unavailable, request allowed rule=%s", rule.name)
            return
        if count > rule.limit:
            raise RateLimited(rule)


limiter = RateLimiter(RedisStore(settings.redis_url), enabled=settings.rate_limits)
