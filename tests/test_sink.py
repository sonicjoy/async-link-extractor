"""The sink: one flushed line per record, counts, the audit and the summary maths."""

import io
import json

import pytest

from linkx.models import ErrorKind, FetchError, FetchResult, Job, Outcome, Record
from linkx.sink import Sink, percentile

JOBS = [Job(i, f"http://h{i}.test/") for i in range(4)]


class FlushCountingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


def result(
    job: Job, *, elapsed_ms: int = 10, attempts: int = 1, error: bool = False
) -> FetchResult:
    return FetchResult(
        job=job,
        final_url=job.url,
        http_status=500 if error else 200,
        html=None if error else "",
        error=FetchError(ErrorKind.HTTP_STATUS, "HTTP 500") if error else None,
        elapsed_ms=elapsed_ms,
        attempts=attempts,
    )


def lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_each_emit_writes_and_flushes_one_line() -> None:
    stream = FlushCountingStream()
    sink = Sink(stream, JOBS)
    sink.emit(Record.ok(result(JOBS[0]), []))
    assert stream.getvalue().count("\n") == 1
    assert stream.flushes == 1
    sink.emit(Record.cancelled(JOBS[1]))
    assert stream.getvalue().count("\n") == 2
    assert stream.flushes == 2
    assert sink.emitted == 2


def test_double_emit_raises_before_writing() -> None:
    stream = io.StringIO()
    sink = Sink(stream, JOBS)
    sink.emit(Record.cancelled(JOBS[0]))
    with pytest.raises(RuntimeError, match="already has a record"):
        sink.emit(Record.cancelled(JOBS[0]))
    assert len(lines(stream)) == 1


def test_unknown_index_raises() -> None:
    sink = Sink(io.StringIO(), JOBS)
    with pytest.raises(RuntimeError, match="unknown job"):
        sink.emit(Record.cancelled(Job(99, "http://x.test/")))


def test_close_after_interrupt_fills_cancelled_records() -> None:
    stream = io.StringIO()
    sink = Sink(stream, JOBS)
    sink.emit(Record.ok(result(JOBS[2]), []))
    summary = sink.close(interrupted=True)
    records = lines(stream)
    assert sorted(r["index"] for r in records) == [0, 1, 2, 3]  # type: ignore[type-var]
    assert [r["outcome"] for r in records[1:]] == ["cancelled"] * 3
    assert summary.interrupted
    assert summary.counts[Outcome.CANCELLED] == 3
    assert summary.counts[Outcome.OK] == 1


def test_close_of_an_uninterrupted_run_with_a_missing_record_raises() -> None:
    sink = Sink(io.StringIO(), JOBS)
    for job in JOBS[:3]:
        sink.emit(Record.ok(result(job), []))
    with pytest.raises(RuntimeError, match="1 job has no record"):
        sink.close(interrupted=False)


def test_summary_counts_retries_and_latency() -> None:
    sink = Sink(io.StringIO(), JOBS)
    sink.emit(Record.ok(result(JOBS[0], elapsed_ms=100, attempts=1), []))
    sink.emit(Record.fetch_error(result(JOBS[1], elapsed_ms=300, attempts=3, error=True)))
    sink.emit(Record.dropped(result(JOBS[2], elapsed_ms=200, attempts=2)))
    sink.emit(Record.cancelled(JOBS[3]))
    summary = sink.close(interrupted=False)
    assert summary.jobs == 4
    assert summary.counts == {
        Outcome.OK: 1,
        Outcome.FETCH_ERROR: 1,
        Outcome.PARSE_ERROR: 0,
        Outcome.DROPPED: 1,
        Outcome.CANCELLED: 1,
    }
    assert summary.retries == 3
    assert (summary.p50_ms, summary.p95_ms, summary.max_ms) == (200, 300, 300)
    assert not summary.interrupted


def test_summary_with_no_fetches_has_no_percentiles() -> None:
    sink = Sink(io.StringIO(), JOBS)
    summary = sink.close(interrupted=True)
    assert (summary.p50_ms, summary.p95_ms, summary.max_ms) == (None, None, None)


def test_dropped_count_is_live() -> None:
    sink = Sink(io.StringIO(), JOBS)
    sink.emit(Record.dropped(result(JOBS[0])))
    assert sink.count(Outcome.DROPPED) == 1


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ([], 95, None),
        ([7], 50, 7),
        ([7], 95, 7),
        ([1, 2, 3, 4], 50, 2),
        ([1, 2, 3, 4], 95, 4),
        (list(range(1, 101)), 95, 95),
        (list(range(1, 101)), 50, 50),
        ([5, 1, 3], 50, 3),
        ([10, 20], 100, 20),
    ],
)
def test_nearest_rank_percentile(values: list[int], p: float, expected: int | None) -> None:
    assert percentile(values, p) == expected
