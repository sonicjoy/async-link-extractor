"""Data passed between the stages, and the output record.

A *job* is one URL to fetch, a *result* is what a fetch produced (and what the
queue holds), and a *record* is one output line.
"""

import enum
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class Outcome(enum.StrEnum):
    OK = "ok"
    FETCH_ERROR = "fetch_error"
    PARSE_ERROR = "parse_error"
    DROPPED = "dropped"
    CANCELLED = "cancelled"


class ErrorKind(enum.StrEnum):
    INVALID_URL = "invalid_url"
    BLOCKED_ADDRESS = "blocked_address"
    TIMEOUT = "timeout"
    CONNECT = "connect"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    HTTP_STATUS = "http_status"
    NOT_HTML = "not_html"
    TOO_LARGE = "too_large"
    UNEXPECTED = "unexpected"
    PARSE = "parse"


@dataclass(frozen=True, slots=True)
class Job:
    index: int
    url: str


@dataclass(frozen=True, slots=True)
class FetchError:
    kind: ErrorKind
    message: str


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What one fetch produced. Exactly one of `html` and `error` is set."""

    job: Job
    final_url: str | None
    http_status: int | None
    html: str | None
    error: FetchError | None
    elapsed_ms: int
    attempts: int


@dataclass(frozen=True, slots=True)
class Record:
    """One output line. Build it with the constructors below, one per outcome."""

    index: int
    url: str
    outcome: Outcome
    final_url: str | None = None
    http_status: int | None = None
    links: Sequence[str] | None = None
    error: FetchError | None = None
    elapsed_ms: int | None = None
    attempts: int | None = None

    @classmethod
    def _from_result(
        cls,
        result: FetchResult,
        outcome: Outcome,
        *,
        links: Sequence[str] | None = None,
        error: FetchError | None = None,
    ) -> "Record":
        return cls(
            index=result.job.index,
            url=result.job.url,
            outcome=outcome,
            final_url=result.final_url,
            http_status=result.http_status,
            links=links,
            error=error,
            elapsed_ms=result.elapsed_ms,
            attempts=result.attempts,
        )

    @classmethod
    def ok(cls, result: FetchResult, links: Sequence[str]) -> "Record":
        return cls._from_result(result, Outcome.OK, links=links)

    @classmethod
    def fetch_error(cls, result: FetchResult) -> "Record":
        return cls._from_result(result, Outcome.FETCH_ERROR, error=result.error)

    @classmethod
    def parse_error(cls, result: FetchResult, message: str) -> "Record":
        return cls._from_result(
            result, Outcome.PARSE_ERROR, error=FetchError(ErrorKind.PARSE, message)
        )

    @classmethod
    def dropped(cls, result: FetchResult) -> "Record":
        """A result evicted from the queue. It keeps the fetch's error, if any."""
        return cls._from_result(result, Outcome.DROPPED, error=result.error)

    @classmethod
    def cancelled(cls, job: Job) -> "Record":
        return cls(index=job.index, url=job.url, outcome=Outcome.CANCELLED)

    def to_json(self) -> str:
        error = (
            None if self.error is None else {"kind": self.error.kind, "message": self.error.message}
        )
        data: dict[str, Any] = {
            "index": self.index,
            "url": self.url,
            "final_url": self.final_url,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "links": None if self.links is None else list(self.links),
            "error": error,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
        }
        return json.dumps(data, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Settings:
    concurrency: int = 10
    consumers: int = 1
    queue_max: int = 100
    queue_policy: str = "drop-oldest"
    timeout: float = 10.0
    retries: int = 2
    max_bytes: int = 2_000_000
    max_redirects: int = 9
    per_host_delay: float = 0.0
    allow_private: bool = False
    progress_interval: float = 5.0


@dataclass(frozen=True, slots=True)
class Summary:
    jobs: int
    counts: Mapping[Outcome, int]
    retries: int
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None
    interrupted: bool
    duplicates_skipped: int = 0
    max_queue_depth: int = 0
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "jobs": self.jobs,
            "duplicates_skipped": self.duplicates_skipped,
            "counts": {str(outcome): count for outcome, count in self.counts.items()},
            "retries": self.retries,
            "max_queue_depth": self.max_queue_depth,
            "duration_s": round(self.duration_s, 3),
            "latency_ms": {"p50": self.p50_ms, "p95": self.p95_ms, "max": self.max_ms},
            "interrupted": self.interrupted,
        }
