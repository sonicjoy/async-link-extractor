"""What a fetch worker does when the queue is full.

A policy puts one item on the queue and returns the item it evicted, or None.
"""

import asyncio
from collections.abc import Awaitable
from typing import Protocol


class OverflowPolicy(Protocol):
    def __call__[T](self, queue: asyncio.Queue[T], item: T, /) -> Awaitable[T | None]: ...


async def drop_oldest[T](queue: asyncio.Queue[T], item: T) -> T | None:
    """Never waits. If the queue is full, remove the oldest item and return it.

    There is no `await` in this function, so no other task can run between the
    check, the removal and the put: on one event loop the three steps are atomic
    without a lock, however many workers call it.
    """
    evicted = queue.get_nowait() if queue.full() else None
    queue.put_nowait(item)
    return evicted


async def block[T](queue: asyncio.Queue[T], item: T) -> None:
    """Wait for space. The worker fetches nothing else meanwhile: backpressure."""
    await queue.put(item)


POLICIES: dict[str, OverflowPolicy] = {
    "drop-oldest": drop_oldest,
    "block": block,
}
