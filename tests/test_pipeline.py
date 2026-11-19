"""The pipeline: completion, many consumers, overflow, failures and the two-stage interrupt.

A scripted fetcher replaces the network so each test controls exactly which jobs are fast,
slow, failing or stuck.
"""

import asyncio
import io
import json
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest

from linkx.models import ErrorKind, FetchError, FetchResult, Job, Outcome, Settings
from linkx.pipeline import Pipeline, extract
from linkx.sink import Sink

SETTINGS = Settings(concurrency=3, consumers=1, queue_max=10, progress_interval=0)


class ScriptedFetcher:
    """Instant success by default; selected jobs fail, hang forever, or take a while."""

    def __init__(
        self,
        *,
        fail: set[int] | frozenset[int] = frozenset(),
        hang: set[int] | frozenset[int] = frozenset(),
        delay: float = 0.0,
    ) -> None:
        self.fail = fail
        self.hang = hang
        self.delay = delay
        self.calls: list[int] = []

    async def fetch(self, job: Job) -> FetchResult:
        self.calls.append(job.index)
        if job.index in self.hang:
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay)
        if job.index in self.fail:
            error = FetchError(ErrorKind.HTTP_STATUS, "HTTP 500")
            return FetchResult(job, job.url, 500, None, error, elapsed_ms=5, attempts=1)
        page = f"<a href='/page{job.index}'>next</a>"
        return FetchResult(job, job.url, 200, page, None, elapsed_ms=5, attempts=1)


def make_jobs(count: int) -> list[Job]:
    return [Job(i, f"http://h{i}.test/") for i in range(count)]


def slow_parser(seconds: float) -> Callable[[str, str], list[str]]:
    def parse(html: str, base_url: str) -> list[str]:
        time.sleep(seconds)  # runs in a worker thread, like the real parser
        return [base_url]

    return parse


def build(
    count: int,
    fetcher: ScriptedFetcher,
    settings: Settings = SETTINGS,
    *,
    parse: Callable[[str, str], list[str]] | None = None,
) -> tuple[Pipeline, io.StringIO]:
    jobs = make_jobs(count)
    stream = io.StringIO()
    kwargs = {} if parse is None else {"parse": parse}
    return Pipeline(jobs, settings, Sink(stream, jobs), fetcher, **kwargs), stream


def records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def by_index(stream: io.StringIO) -> dict[object, dict[str, object]]:
    parsed = records(stream)
    indexes = [r["index"] for r in parsed]
    assert len(indexes) == len(set(indexes)), "a job has more than one record"
    return {r["index"]: r for r in parsed}


# --- completion --------------------------------------------------------------------------


async def test_every_job_gets_one_ok_record_with_links() -> None:
    pipeline, stream = build(20, ScriptedFetcher(delay=0.001))
    summary = await pipeline.run()
    result = by_index(stream)
    assert sorted(result) == list(range(20))  # type: ignore[type-var]
    assert {r["outcome"] for r in result.values()} == {"ok"}
    assert result[7]["links"] == ["http://h7.test/page7"]
    assert summary.counts[Outcome.OK] == 20
    assert not summary.interrupted
    assert summary.duration_s > 0


async def test_consumers_wait_for_slow_producers() -> None:
    pipeline, stream = build(6, ScriptedFetcher(delay=0.05), replace(SETTINGS, concurrency=2))
    await pipeline.run()
    assert len(by_index(stream)) == 6


async def test_several_consumers_share_the_queue() -> None:
    settings = replace(SETTINGS, consumers=4, concurrency=4, queue_policy="block")
    pipeline, stream = build(40, ScriptedFetcher(), settings, parse=slow_parser(0.005))
    summary = await pipeline.run()
    assert len(by_index(stream)) == 40
    assert summary.counts[Outcome.OK] == 40


async def test_each_job_is_fetched_exactly_once() -> None:
    fetcher = ScriptedFetcher(delay=0.001)
    pipeline, _ = build(30, fetcher, replace(SETTINGS, concurrency=7))
    await pipeline.run()
    assert sorted(fetcher.calls) == list(range(30))


async def test_empty_job_list_completes() -> None:
    pipeline, stream = build(0, ScriptedFetcher())
    summary = await pipeline.run()
    assert stream.getvalue() == ""
    assert summary.jobs == 0


# --- failure isolation -------------------------------------------------------------------


async def test_an_output_failure_stops_all_further_writes() -> None:
    class FailsOnce(io.StringIO):
        failed = False
        writes_after_failure = 0

        def write(self, text: str) -> int:
            if self.failed:
                self.writes_after_failure += 1
                return super().write(text)
            self.failed = True
            raise OSError("disk full")

    jobs = make_jobs(50)
    stream = FailsOnce()
    settings = replace(SETTINGS, concurrency=8, queue_max=1, consumers=4)
    pipeline = Pipeline(jobs, settings, Sink(stream, jobs), ScriptedFetcher())
    with pytest.raises(BaseExceptionGroup):
        await pipeline.run()
    assert stream.writes_after_failure == 0


async def test_fetch_errors_are_written_without_parsing() -> None:
    parsed: list[str] = []

    def parse(html: str, base_url: str) -> list[str]:
        parsed.append(base_url)
        return []

    pipeline, stream = build(4, ScriptedFetcher(fail={1, 3}), parse=parse)
    await pipeline.run()
    result = by_index(stream)
    assert [result[i]["outcome"] for i in range(4)] == ["ok", "fetch_error", "ok", "fetch_error"]
    assert result[1]["error"] == {"kind": "http_status", "message": "HTTP 500"}
    assert sorted(parsed) == ["http://h0.test/", "http://h2.test/"]


async def test_a_parser_exception_becomes_a_parse_error_and_the_run_continues() -> None:
    def parse(html: str, base_url: str) -> list[str]:
        if "h2." in base_url:
            raise ValueError("cannot parse this one")
        return []

    pipeline, stream = build(5, ScriptedFetcher(), parse=parse)
    summary = await pipeline.run()
    result = by_index(stream)
    assert result[2]["outcome"] == "parse_error"
    assert result[2]["error"] == {"kind": "parse", "message": "ValueError: cannot parse this one"}
    assert summary.counts[Outcome.OK] == 4


async def test_an_output_failure_ends_the_run() -> None:
    class BrokenStream(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError("disk full")

    jobs = make_jobs(5)
    pipeline = Pipeline(jobs, SETTINGS, Sink(BrokenStream(), jobs), ScriptedFetcher(delay=0.01))
    with pytest.raises(BaseExceptionGroup) as info:
        await pipeline.run()
    assert info.group_contains(OSError, match="disk full", depth=None)


# --- overflow ----------------------------------------------------------------------------


async def test_drop_oldest_drops_when_the_consumer_falls_behind() -> None:
    settings = replace(SETTINGS, concurrency=10, queue_max=1, queue_policy="drop-oldest")
    fetcher = ScriptedFetcher(fail={0, 2, 4, 6, 8})
    pipeline, stream = build(10, fetcher, settings, parse=slow_parser(0.05))
    summary = await pipeline.run()
    result = by_index(stream)
    assert len(result) == 10
    dropped = [r for r in result.values() if r["outcome"] == "dropped"]
    assert dropped
    assert summary.counts[Outcome.DROPPED] == len(dropped)
    assert any(r["error"] is not None for r in dropped), "a dropped failure keeps its error"
    assert summary.max_queue_depth == 1


async def test_each_drop_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    settings = replace(SETTINGS, concurrency=10, queue_max=1)
    pipeline, _ = build(10, ScriptedFetcher(), settings, parse=slow_parser(0.02))
    summary = await pipeline.run()
    drops = [r for r in caplog.records if getattr(r, "event", None) == "drop"]
    assert len(drops) == summary.counts[Outcome.DROPPED] > 0


async def test_block_policy_drops_nothing() -> None:
    settings = replace(SETTINGS, concurrency=10, queue_max=1, queue_policy="block")
    pipeline, stream = build(10, ScriptedFetcher(fail={0, 2}), settings, parse=slow_parser(0.01))
    summary = await pipeline.run()
    assert len(by_index(stream)) == 10
    assert summary.counts[Outcome.DROPPED] == 0
    assert summary.counts[Outcome.OK] == 8


# --- interrupt ---------------------------------------------------------------------------


async def test_interrupt_stops_fetching_and_drains_the_queue() -> None:
    fetcher = ScriptedFetcher(hang={4, 5})
    pipeline, stream = build(6, fetcher, replace(SETTINGS, concurrency=6), parse=slow_parser(0.01))
    asyncio.get_running_loop().call_later(0.05, pipeline.interrupt)
    summary = await pipeline.run()
    result = by_index(stream)
    assert [result[i]["outcome"] for i in range(6)] == ["ok"] * 4 + ["cancelled"] * 2
    assert summary.interrupted
    assert summary.counts[Outcome.CANCELLED] == 2


async def test_first_interrupt_still_parses_every_queued_result() -> None:
    # Jobs 0-4 are fetched at once and queue up behind a slow parser; 5 and 6 never finish.
    # The interrupt lands while job 0 is being parsed and 1-4 are waiting in the queue.
    settings = replace(SETTINGS, concurrency=7, queue_max=5, queue_policy="block")
    pipeline, stream = build(7, ScriptedFetcher(hang={5, 6}), settings, parse=slow_parser(0.05))
    asyncio.get_running_loop().call_later(0.01, pipeline.interrupt)
    summary = await pipeline.run()
    result = by_index(stream)
    assert [result[i]["outcome"] for i in range(7)] == ["ok"] * 5 + ["cancelled"] * 2
    assert summary.interrupted


async def test_second_interrupt_stops_the_drain() -> None:
    settings = replace(SETTINGS, concurrency=6)
    pipeline, stream = build(6, ScriptedFetcher(hang={5}), settings, parse=slow_parser(0.1))
    loop = asyncio.get_running_loop()
    loop.call_later(0.02, pipeline.interrupt)
    loop.call_later(0.05, pipeline.interrupt)
    summary = await pipeline.run()
    result = by_index(stream)
    assert len(result) == 6
    assert summary.interrupted
    cancelled = [i for i, r in result.items() if r["outcome"] == "cancelled"]
    assert 5 in cancelled
    assert len(cancelled) > 1, "queued results were abandoned by the second interrupt"


async def test_interrupt_before_run_starts_no_fetches() -> None:
    fetcher = ScriptedFetcher()
    pipeline, stream = build(3, fetcher)
    pipeline.interrupt()
    summary = await pipeline.run()
    assert fetcher.calls == []
    assert {r["outcome"] for r in records(stream)} == {"cancelled"}
    assert summary.interrupted


async def test_interrupt_cancels_a_worker_blocked_on_a_full_queue() -> None:
    settings = replace(SETTINGS, concurrency=3, queue_max=1, queue_policy="block")
    pipeline, stream = build(6, ScriptedFetcher(), settings, parse=slow_parser(0.05))
    asyncio.get_running_loop().call_later(0.02, pipeline.interrupt)
    summary = await pipeline.run()
    assert len(by_index(stream)) == 6
    assert summary.counts[Outcome.CANCELLED] > 0


@pytest.mark.skipif(threading.current_thread() is not threading.main_thread(), reason="signals")
async def test_ctrl_c_signal_triggers_the_interrupt_and_the_handler_is_restored() -> None:
    before = signal.getsignal(signal.SIGINT)
    pipeline, stream = build(3, ScriptedFetcher(hang={2}))
    asyncio.get_running_loop().call_later(0.05, signal.raise_signal, signal.SIGINT)
    summary = await pipeline.run()
    assert summary.interrupted
    assert by_index(stream)[2]["outcome"] == "cancelled"
    assert signal.getsignal(signal.SIGINT) is before


def test_runs_outside_the_main_thread_without_a_signal_handler() -> None:
    before = signal.getsignal(signal.SIGINT)
    outcome: list[object] = []

    def worker() -> None:
        pipeline, stream = build(3, ScriptedFetcher())
        asyncio.run(pipeline.run())
        outcome.append(len(records(stream)))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert outcome == [3]
    assert signal.getsignal(signal.SIGINT) is before


# --- observability -----------------------------------------------------------------------


async def test_progress_is_logged_while_running(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="linkx")
    settings = replace(SETTINGS, progress_interval=0.01)
    pipeline, _ = build(3, ScriptedFetcher(delay=0.05), settings)
    await pipeline.run()
    progress = [r for r in caplog.records if getattr(r, "event", None) == "progress"]
    assert progress
    assert getattr(progress[0], "total", None) == 3


async def test_each_record_is_logged_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG", logger="linkx")
    pipeline, _ = build(3, ScriptedFetcher(fail={1}))
    await pipeline.run()
    emitted = [r for r in caplog.records if getattr(r, "event", None) == "record"]
    assert sorted(getattr(r, "index", -1) for r in emitted) == [0, 1, 2]


# --- composition -------------------------------------------------------------------------


async def test_extract_wires_the_real_fetcher_and_parser() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bad.test":
            return httpx.Response(404)
        body = '<a href="/a">a</a><a href="https://other.test/b#x">b</a>'
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    jobs = [Job(0, "http://good.test/"), Job(1, "http://bad.test/")]
    stream = io.StringIO()
    settings = replace(SETTINGS, allow_private=True)
    summary = await extract(jobs, settings, stream, transport=httpx.MockTransport(handler))
    result = by_index(stream)
    assert result[0]["links"] == ["http://good.test/a", "https://other.test/b"]
    assert result[1]["outcome"] == "fetch_error"
    assert summary.counts[Outcome.OK] == 1
