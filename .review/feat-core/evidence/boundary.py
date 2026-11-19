"""Boundary drive: each limit at the bound, just past it, and the next ordinary input after.

Drives the real Fetcher (and the real pipeline for the queue) through httpx.MockTransport.
Pass --break to shift every bound by one; the script must then fail, which shows the checks
can fail.

Run:  uv run python .review/feat-core/evidence/boundary.py [--break]
"""

import asyncio
import io
import sys
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx

from linkx.fetch import Fetcher, make_client
from linkx.models import Job, Settings
from linkx.pipeline import extract

BREAK = "--break" in sys.argv
LIMIT = 1_000
SETTINGS = Settings(max_bytes=LIMIT - 1 if BREAK else LIMIT, timeout=0.3, retries=2)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))


async def chunks(total: int) -> AsyncIterator[bytes]:
    sent = 0
    while sent < total:
        piece = min(300, total - sent)
        sent += piece
        yield b"x" * piece


def handler(request: httpx.Request) -> httpx.Response:
    path, query = request.url.path, dict(request.url.params)
    html = {"content-type": "text/html"}
    if path == "/fixed":
        return httpx.Response(200, headers=html, content=b"x" * int(query["n"]))
    if path == "/stream":
        return httpx.Response(200, headers=html, content=chunks(int(query["n"])))
    if path == "/hops":
        left = int(query["left"])
        if left == 0:
            return httpx.Response(200, headers=html, content=b"<a href='/x'>x</a>")
        return httpx.Response(302, headers={"location": f"/hops?left={left - 1}"})
    if path == "/flaky":
        flaky_calls[query["id"]] = flaky_calls.get(query["id"], 0) + 1
        if flaky_calls[query["id"]] <= int(query["fails"]):
            return httpx.Response(503)
        return httpx.Response(200, headers=html, content=b"ok")
    return httpx.Response(200, headers=html, content=b"<a href='/ordinary'>o</a>")


flaky_calls: dict[str, int] = {}


async def slow_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/slow":
        await asyncio.sleep(float(request.url.params["s"]))
    return handler(request)


async def no_sleep(seconds: float) -> None:
    return None


async def main() -> int:
    transport = httpx.MockTransport(slow_handler)
    async with make_client(SETTINGS, transport) as client:
        fetcher = Fetcher(client, replace(SETTINGS, allow_private=True), sleep=no_sleep)

        async def fetch(url: str) -> tuple[str, int]:
            result = await fetcher.fetch(Job(0, url))
            kind = "ok" if result.error is None else str(result.error.kind)
            return kind, result.attempts

        async def ordinary_after(name: str) -> None:
            kind, _ = await fetch("http://a.test/ordinary")
            check(f"{name}: next ordinary request still succeeds", kind == "ok", kind)

        # max_bytes, declared length and streamed
        for path in ("fixed", "stream"):
            at, _ = await fetch(f"http://a.test/{path}?n={LIMIT}")
            past, _ = await fetch(f"http://a.test/{path}?n={LIMIT + 1}")
            check(f"max_bytes ({path}): body of exactly {LIMIT} bytes accepted", at == "ok", at)
            check(f"max_bytes ({path}): {LIMIT + 1} bytes refused as too_large", past == "too_large", past)
            await ordinary_after(f"max_bytes ({path})")

        # redirects: 9 allowed
        hops = SETTINGS.max_redirects
        at, _ = await fetch(f"http://a.test/hops?left={hops - (1 if BREAK else 0)}")
        past, _ = await fetch(f"http://a.test/hops?left={hops + 1}")
        check(f"max_redirects: {hops} redirects followed", at == "ok", at)
        check(f"max_redirects: {hops + 1} refused as too_many_redirects", past == "too_many_redirects", past)
        await ordinary_after("max_redirects")

        # timeout per attempt (0.3 s): 0.1 s inside, 0.5 s past
        inside = 0.5 if BREAK else 0.1
        at, _ = await fetch(f"http://a.test/slow?s={inside}")
        past, attempts = await fetch("http://a.test/slow?s=0.5")
        check("timeout: response at 0.1 s inside a 0.3 s timeout succeeds", at == "ok", at)
        check("timeout: response at 0.5 s is a timeout after 3 attempts", (past, attempts) == ("timeout", 3), f"{past}, {attempts}")
        await ordinary_after("timeout")

        # retries=2: two failures then success is ok; three failures exhausts
        at, attempts_at = await fetch("http://a.test/flaky?id=a&fails=2")
        past, attempts_past = await fetch("http://a.test/flaky?id=b&fails=3")
        check("retries: 2 failures then success is ok on attempt 3", (at, attempts_at) == ("ok", 3), f"{at}, {attempts_at}")
        check("retries: 3 failures is http_status after 3 attempts", (past, attempts_past) == ("http_status", 3), f"{past}, {attempts_past}")
        await ordinary_after("retries")

    # queue_max through the real pipeline: block keeps all, drop-oldest drops past the bound
    jobs = [Job(i, f"http://a.test/ordinary?{i}") for i in range(30)]

    def slow_parse(html: str, base: str) -> list[str]:
        import time

        time.sleep(0.01)
        return []

    settings = replace(SETTINGS, concurrency=10, queue_max=5, allow_private=True, progress_interval=0)
    drop = await extract(jobs, settings, io.StringIO(), transport=transport, parse=slow_parse)
    block = await extract(
        jobs, replace(settings, queue_policy="block"), io.StringIO(), transport=transport, parse=slow_parse
    )
    total = lambda s: sum(s.counts.values())  # noqa: E731
    check("queue_max=5: depth never exceeds 5", drop.max_queue_depth <= 5 and block.max_queue_depth <= 5, f"{drop.max_queue_depth}, {block.max_queue_depth}")
    check("queue_max: drop-oldest drops and still accounts for all 30", drop.counts["dropped"] > 0 and total(drop) == 30, f"dropped={drop.counts['dropped']}, records={total(drop)}")
    check("queue_max: block drops none and accounts for all 30", block.counts["dropped"] == 0 and total(block) == 30, f"dropped={block.counts['dropped']}, records={total(block)}")

    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
    print(f"\n{len(results) - failed} passed, {failed} failed{' (bounds shifted by --break)' if BREAK else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
