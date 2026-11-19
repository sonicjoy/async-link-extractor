"""Show both overflow policies when the consumer is slower than the fetchers.

No network: canned pages are served through httpx.MockTransport with a small delay. Each
page holds 1,000 links, so the real parser spends about 10 ms of CPU on it and the queue
fills. Exits non-zero unless drop-oldest drops something and block drops nothing.

The last row adds consumers. Parsing is pure-Python CPU work, so with the GIL on, extra
consumer threads cannot parse in parallel; on a free-threaded build (python3.15t) they can.

Run:  uv run python scripts/demo_backpressure.py
"""

import asyncio
import io
import sys
import time
from dataclasses import dataclass, replace

import httpx

from linkx.models import Job, Outcome, Settings
from linkx.pipeline import extract

JOBS = 200
FETCH_DELAY_S = 0.002
PAGE = "".join(f'<p>item {n}</p><a href="/page/{n}?q={n}">{n}</a>' for n in range(1000))


async def serve(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(FETCH_DELAY_S)
    return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)


@dataclass
class Row:
    label: str
    ok: int
    dropped: int
    seconds: float
    max_depth: int


async def run(label: str, settings: Settings) -> Row:
    jobs = [Job(i, f"http://site{i % 10}.test/{i}") for i in range(JOBS)]
    started = time.perf_counter()
    summary = await extract(jobs, settings, io.StringIO(), transport=httpx.MockTransport(serve))
    return Row(
        label,
        summary.counts[Outcome.OK],
        summary.counts[Outcome.DROPPED],
        time.perf_counter() - started,
        summary.max_queue_depth,
    )


async def main() -> int:
    base = Settings(
        concurrency=20,
        queue_max=10,
        retries=0,
        max_bytes=10_000_000,
        allow_private=True,
        progress_interval=0,
    )
    rows = [
        await run("drop-oldest, 1 consumer", base),
        await run("block, 1 consumer", replace(base, queue_policy="block")),
        await run("block, 4 consumers", replace(base, queue_policy="block", consumers=4)),
    ]
    gil = "on" if getattr(sys, "_is_gil_enabled", lambda: True)() else "off"
    print(f"{JOBS} jobs, 20 fetch workers, queue of 10, 1,000 links per page")
    print(f"Python {sys.version.split()[0]}, GIL {gil}\n")
    print(f"{'policy':<26}{'ok':>6}{'dropped':>9}{'seconds':>9}{'max depth':>11}")
    for row in rows:
        print(f"{row.label:<26}{row.ok:>6}{row.dropped:>9}{row.seconds:>9.2f}{row.max_depth:>11}")

    drop_oldest, block = rows[0], rows[1]
    if drop_oldest.dropped == 0 or block.dropped != 0:
        print("\nFAIL: expected drops under drop-oldest and none under block", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
