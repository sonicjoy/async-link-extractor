"""Wire the stages together: N fetch workers -> bounded queue -> M consumers -> sink.

Everything runs as tasks on one event loop. The only thread use is parsing,
through `asyncio.to_thread`, so a large page cannot stall fetches and timers.
"""

import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from types import FrameType
from typing import Protocol, TextIO

import httpx

from linkx.fetch import Fetcher, Resolver, make_client, system_resolve
from linkx.models import FetchResult, Job, Outcome, Record, Settings, Summary
from linkx.overflow import POLICIES
from linkx.parse import extract_links
from linkx.sink import Sink

log = logging.getLogger(__name__)

type Parser = Callable[[str, str], list[str]]


class PageFetcher(Protocol):
    """What the pipeline needs from a fetcher: `linkx.fetch.Fetcher`, or a test double."""

    async def fetch(self, job: Job) -> FetchResult: ...


class Pipeline:
    def __init__(
        self,
        jobs: Sequence[Job],
        settings: Settings,
        sink: Sink,
        fetcher: PageFetcher,
        *,
        parse: Parser = extract_links,
    ) -> None:
        self._total = len(jobs)
        self._pending: Iterator[Job] = iter(jobs)  # shared by all workers
        self._settings = settings
        self._sink = sink
        self._fetcher = fetcher
        self._parse = parse
        self._put = POLICIES[settings.queue_policy]
        self._queue: asyncio.Queue[FetchResult] = asyncio.Queue(maxsize=settings.queue_max)
        self._workers: list[asyncio.Task[None]] = []
        self._consumers: list[asyncio.Task[None]] = []
        self._interrupts = 0
        self._in_flight = 0
        self._max_depth = 0

    @property
    def interrupted(self) -> bool:
        return self._interrupts > 0

    def interrupt(self) -> None:
        """Stop in two stages. Must be called on the event loop.

        First call: cancel the fetch workers; consumers drain what is already queued.
        Later calls: cancel the consumers as well.
        A cancelled task does not fail its TaskGroup, so `run` still returns normally.
        """
        self._interrupts += 1
        first = self._interrupts == 1
        log.warning(
            "interrupted: %s",
            "stopping fetches, finishing queued pages (Ctrl-C again to stop now)"
            if first
            else "stopping now",
            extra={"event": "interrupt", "count": self._interrupts},
        )
        for task in self._workers if first else [*self._workers, *self._consumers]:
            task.cancel()

    async def run(self) -> Summary:
        started = time.perf_counter()
        with self._ctrl_c_calls_interrupt():
            progress = None
            if self._settings.progress_interval > 0:
                progress = asyncio.create_task(self._report_progress())
            try:
                await self._run_stages()
            finally:
                if progress is not None:
                    progress.cancel()
                    await asyncio.wait([progress])
            # Still inside the handler's span: a late Ctrl-C only counts as an interrupt
            # and cannot cut the closing records or the summary short.
            summary = self._sink.close(interrupted=self.interrupted)
        return replace(
            summary, max_queue_depth=self._max_depth, duration_s=time.perf_counter() - started
        )

    async def _run_stages(self) -> None:
        async with asyncio.TaskGroup() as consumer_group:
            self._consumers = [
                consumer_group.create_task(self._consume(), name=f"consumer-{n}")
                for n in range(self._settings.consumers)
            ]
            try:
                if not self.interrupted:
                    async with asyncio.TaskGroup() as worker_group:
                        self._workers = [
                            worker_group.create_task(self._produce(), name=f"worker-{n}")
                            for n in range(self._settings.concurrency)
                        ]
            finally:
                # Producer is done (or stopped): consumers drain the queue, then get
                # QueueShutDown and exit. This is "producer finished AND queue empty".
                self._queue.shutdown()

    async def _produce(self) -> None:
        # next() on the shared iterator never awaits, so two workers never take the same job.
        for job in self._pending:
            self._in_flight += 1
            try:
                result = await self._fetcher.fetch(job)
            finally:
                self._in_flight -= 1
            evicted = await self._put(self._queue, result)
            self._max_depth = max(self._max_depth, self._queue.qsize())
            if evicted is not None:
                # No await since the eviction, so nothing else ran in between.
                log.warning(
                    "[#%d] dropped from the full queue: %s",
                    evicted.job.index,
                    evicted.job.url,
                    extra={"event": "drop", "index": evicted.job.index, "url": evicted.job.url},
                )
                self._emit(Record.dropped(evicted))

    async def _consume(self) -> None:
        while True:
            try:
                result = await self._queue.get()
            except asyncio.QueueShutDown:
                return
            self._emit(await self._to_record(result))

    async def _to_record(self, result: FetchResult) -> Record:
        if result.html is None:  # FetchResult guarantees an error is set instead
            return Record.fetch_error(result)
        base_url = result.final_url or result.job.url
        try:
            links = await asyncio.to_thread(self._parse, result.html, base_url)
        except Exception as exc:  # isolation: a parser failure is this page's error only
            log.warning(
                "[#%d] parse failed: %s",
                result.job.index,
                exc,
                extra={"event": "parse_error", "index": result.job.index, "url": result.job.url},
            )
            return Record.parse_error(result, f"{type(exc).__name__}: {exc}")
        return Record.ok(result, links)

    def _emit(self, record: Record) -> None:
        self._sink.emit(record)
        log.debug(
            "[#%d] %s %s",
            record.index,
            record.outcome,
            record.url,
            extra={"event": "record", "index": record.index, "outcome": str(record.outcome)},
        )

    async def _report_progress(self) -> None:
        while True:
            await asyncio.sleep(self._settings.progress_interval)
            done = self._sink.emitted
            dropped = self._sink.count(Outcome.DROPPED)
            log.info(
                "progress: %d/%d done, %d in flight, queue %d, dropped %d",
                done,
                self._total,
                self._in_flight,
                self._queue.qsize(),
                dropped,
                extra={
                    "event": "progress",
                    "done": done,
                    "total": self._total,
                    "in_flight": self._in_flight,
                    "queue": self._queue.qsize(),
                    "dropped": dropped,
                },
            )

    @contextlib.contextmanager
    def _ctrl_c_calls_interrupt(self) -> Iterator[None]:
        """Route Ctrl-C to `interrupt()` on the loop instead of raising KeyboardInterrupt.

        `signal.signal` is used rather than `loop.add_signal_handler`, which Windows lacks.
        The handler only schedules a call, so cancellation still arrives at an `await`,
        never in the middle of writing a record.
        """
        loop = asyncio.get_running_loop()

        def on_sigint(signum: int, frame: FrameType | None) -> None:
            loop.call_soon_threadsafe(self.interrupt)

        try:
            previous = signal.signal(signal.SIGINT, on_sigint)
        except ValueError:  # not the main thread: keep the default Ctrl-C behaviour
            yield
            return
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)


async def extract(
    jobs: Sequence[Job],
    settings: Settings,
    stream: TextIO,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolve: Resolver = system_resolve,
    parse: Parser = extract_links,
) -> Summary:
    """Run the whole pipeline over `jobs`, writing one record per job to `stream`.

    `transport` replaces the network (tests, the demo). The connected-address check needs
    the transport to report the socket in the response's `network_stream` extension, as
    httpx's own transport does; a transport that does not is not checked.
    """
    sink = Sink(stream, jobs)
    async with make_client(settings, transport) as client:
        fetcher = Fetcher(client, settings, resolve=resolve)
        return await Pipeline(jobs, settings, sink, fetcher, parse=parse).run()
