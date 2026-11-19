"""Randomised schedules for the one-record-per-job rule (fixed seed, replayable).

Each run picks random worker and consumer counts, queue size and policy; each job is fast,
slow, failing, or stuck; the parser is sometimes slow and sometimes raises; zero, one or two
interrupts arrive at random times. Every run must end with exactly one record per job and
summary counts that sum to the job count. Runs with stuck jobs always get an interrupt.

Pass --break to drop one record in every run (the sink audit must then raise).

Run:  uv run python .review/feat-core/evidence/stress.py [--break]
"""

import asyncio
import io
import json
import random
import sys
import time

from linkx.models import ErrorKind, FetchError, FetchResult, Job, Settings
from linkx.pipeline import Pipeline
from linkx.sink import Sink

SEED = 7
RUNS = 300
BREAK = "--break" in sys.argv


class RandomFetcher:
    def __init__(self, rng: random.Random, count: int) -> None:
        self.kind = [rng.choice(["fast", "fast", "slow", "fail", "stuck"]) for _ in range(count)]
        self.delay = [rng.uniform(0, 0.01) for _ in range(count)]

    async def fetch(self, job: Job) -> FetchResult:
        kind = self.kind[job.index]
        if kind == "stuck":
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay[job.index] * (5 if kind == "slow" else 1))
        if kind == "fail":
            error = FetchError(ErrorKind.CONNECT, "refused")
            return FetchResult(job, None, None, None, error, 1, 3)
        return FetchResult(job, job.url, 200, "<a href='/x'>x</a>", None, 1, 1)


async def one_run(run: int, rng: random.Random) -> str | None:
    count = rng.randint(0, 30)
    jobs = [Job(i, f"http://h{i}.test/") for i in range(count)]
    fetcher = RandomFetcher(rng, count)
    settings = Settings(
        concurrency=rng.randint(1, 8),
        consumers=rng.randint(1, 3),
        queue_max=rng.randint(1, 5),
        queue_policy=rng.choice(["drop-oldest", "block"]),
        progress_interval=0,
    )
    parse_delay = rng.choice([0, 0, 0.002])

    def parse(html: str, base: str) -> list[str]:
        if rng.random() < 0.1:
            raise ValueError("bad page")
        time.sleep(parse_delay)
        return [base]

    stream = io.StringIO()
    pipeline = Pipeline(jobs, settings, Sink(stream, jobs), fetcher, parse=parse)
    if BREAK and count:
        real_emit = pipeline._emit
        skipped = []

        def lossy_emit(record):  # type: ignore[no-untyped-def]
            if not skipped:
                skipped.append(record.index)
                return
            real_emit(record)

        pipeline._emit = lossy_emit  # type: ignore[method-assign]

    loop = asyncio.get_running_loop()
    interrupts = rng.choice([0, 1, 2])
    if "stuck" in fetcher.kind and interrupts == 0:
        interrupts = 1
    for _ in range(interrupts):
        loop.call_later(rng.uniform(0, 0.03), pipeline.interrupt)
    try:
        async with asyncio.timeout(10):
            summary = await pipeline.run()
    except Exception as exc:
        return f"run {run}: raised {type(exc).__name__}: {exc}"

    indexes = [json.loads(line)["index"] for line in stream.getvalue().splitlines()]
    if sorted(indexes) != list(range(count)):
        return f"run {run}: records {sorted(indexes)} for {count} jobs"
    if sum(summary.counts.values()) != count:
        return f"run {run}: summary counts {dict(summary.counts)} for {count} jobs"
    # An interrupt timer may fire after a short run has already finished; then the run is
    # correctly not interrupted. What must hold: cancelled records only after an interrupt,
    # and a run with stuck jobs is always interrupted.
    if summary.counts["cancelled"] and not summary.interrupted:
        return f"run {run}: cancelled records without an interrupt"
    if "stuck" in fetcher.kind and not summary.interrupted:
        return f"run {run}: stuck jobs but run not interrupted"
    return None


async def main() -> int:
    rng = random.Random(SEED)
    failures = [f for run in range(RUNS) if (f := await one_run(run, rng))]
    for line in failures[:10]:
        print("FAIL", line)
    print(f"seed {SEED}: {RUNS} randomised runs, {len(failures)} failed{' (lossy emit, --break)' if BREAK else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
