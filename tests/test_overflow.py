"""Overflow policies on a bounded asyncio.Queue: drop-oldest and block."""

import asyncio

import pytest

from linkx.overflow import POLICIES, block, drop_oldest


async def test_drop_oldest_returns_none_when_there_is_room() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
    assert await drop_oldest(queue, 1) is None
    assert queue.qsize() == 1


async def test_drop_oldest_evicts_and_returns_the_oldest_when_full() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=3)
    for item in (1, 2, 3):
        await drop_oldest(queue, item)
    assert await drop_oldest(queue, 4) == 1
    assert await drop_oldest(queue, 5) == 2
    assert [queue.get_nowait() for _ in range(3)] == [3, 4, 5]


async def test_queue_is_first_in_first_out() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=10)
    for item in range(5):
        await block(queue, item)
    assert [queue.get_nowait() for _ in range(5)] == [0, 1, 2, 3, 4]


async def test_block_waits_for_space_and_loses_nothing() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    await block(queue, 1)
    waiting = asyncio.create_task(block(queue, 2))
    await asyncio.sleep(0.01)
    assert not waiting.done(), "put should wait while the queue is full"
    assert queue.get_nowait() == 1
    await waiting
    assert queue.get_nowait() == 2


async def test_many_concurrent_putters_lose_nothing_unaccounted() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=5)
    evicted: list[int] = []

    async def producer(start: int) -> None:
        for item in range(start, start + 50):
            dropped = await drop_oldest(queue, item)
            if dropped is not None:
                evicted.append(dropped)
            await asyncio.sleep(0)

    async with asyncio.TaskGroup() as tg:
        for start in range(0, 500, 50):
            tg.create_task(producer(start))

    queued = [queue.get_nowait() for _ in range(queue.qsize())]
    assert len(queued) == 5
    assert sorted(queued + evicted) == list(range(500))


async def test_drop_oldest_never_waits_on_a_full_queue() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    async with asyncio.timeout(1):
        for item in range(100):
            await drop_oldest(queue, item)
    assert queue.get_nowait() == 99


def test_policy_table_holds_exactly_the_two_cli_names() -> None:
    assert {"drop-oldest": drop_oldest, "block": block} == POLICIES


@pytest.mark.parametrize("name", ["drop-oldest", "block"])
async def test_policies_from_the_table_put_items(name: str) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    assert await POLICIES[name](queue, "x") is None
    assert queue.get_nowait() == "x"
