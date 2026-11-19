"""Per-host politeness: a minimum gap between request attempts to the same host."""

import asyncio
import time
from collections.abc import Awaitable, Callable


class HostLimiter:
    """Hands out start times per host, `delay` seconds apart.

    `wait` reserves the next slot for the host before it sleeps, with no
    `await` between reading and updating the schedule, so concurrent callers
    always get distinct slots. Hosts never wait for each other.
    """

    def __init__(
        self,
        delay: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._delay = delay
        self._clock = clock
        self._sleep = sleep
        self._next_start: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self._delay <= 0:
            return
        now = self._clock()
        start = max(now, self._next_start.get(host, now))
        self._next_start[host] = start + self._delay
        if start > now:
            await self._sleep(start - now)
