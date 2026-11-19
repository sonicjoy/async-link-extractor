"""Per-host minimum gap between request attempts."""

import asyncio

from linkx.ratelimit import HostLimiter


class FakeTime:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)  # let other tasks run, as a real sleep would
        self.now += seconds


def limiter(delay: float, time: FakeTime) -> HostLimiter:
    return HostLimiter(delay, clock=time.clock, sleep=time.sleep)


async def test_zero_delay_never_waits() -> None:
    time = FakeTime()
    host_limiter = limiter(0, time)
    for _ in range(5):
        await host_limiter.wait("a.test")
    assert time.sleeps == []


async def test_same_host_is_spaced_by_the_delay() -> None:
    time = FakeTime()
    host_limiter = limiter(1.0, time)
    for _ in range(3):
        await host_limiter.wait("a.test")
    assert time.sleeps == [1.0, 1.0]


async def test_concurrent_waiters_on_one_host_get_successive_slots() -> None:
    time = FakeTime()
    host_limiter = limiter(0.5, time)
    await asyncio.gather(*(host_limiter.wait("a.test") for _ in range(4)))
    assert sorted(time.sleeps) == [0.5, 1.0, 1.5]


async def test_different_hosts_do_not_wait_for_each_other() -> None:
    time = FakeTime()
    host_limiter = limiter(1.0, time)
    for host in ("a.test", "b.test", "c.test"):
        await host_limiter.wait(host)
    assert time.sleeps == []


async def test_a_slot_in_the_past_does_not_wait() -> None:
    time = FakeTime()
    host_limiter = limiter(1.0, time)
    await host_limiter.wait("a.test")
    time.now += 5
    await host_limiter.wait("a.test")
    assert time.sleeps == []
