"""Command-line entry point: read inputs, run the pipeline, report, choose the exit code."""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

import httpx

from linkx.models import Job, Outcome, Settings, Summary
from linkx.overflow import POLICIES
from linkx.pipeline import extract

log = logging.getLogger("linkx")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130
DEFAULT_PORTS = {"http": 80, "https": 443}


# --- arguments ---------------------------------------------------------------------------


def _bounded[T: (int, float)](
    convert: Callable[[str], T], *, minimum: T, inclusive: bool = True
) -> Callable[[str], T]:
    """Build an argparse type that converts a value and enforces a lower bound."""

    def parse(text: str) -> T:
        try:
            value = convert(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
        if value < minimum or (not inclusive and value == minimum):
            relation = "at least" if inclusive else "above"
            raise argparse.ArgumentTypeError(f"must be {relation} {minimum}, got {text}")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkx",
        description="Fetch pages concurrently and write the links on each one as JSON lines.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="URL_OR_FILE",
        help="a URL starting with http:// or https://, or a file with one URL per line",
    )
    parser.add_argument("--out", metavar="FILE", help="write records here instead of stdout")
    parser.add_argument(
        "--keep-duplicates", action="store_true", help="fetch repeated URLs once per occurrence"
    )
    parser.add_argument(
        "--concurrency", type=_bounded(int, minimum=1), default=10, help="fetch workers"
    )
    parser.add_argument(
        "--consumers", type=_bounded(int, minimum=1), default=1, help="parsing consumers"
    )
    parser.add_argument(
        "--queue-max", type=_bounded(int, minimum=1), default=100, help="queue capacity"
    )
    parser.add_argument(
        "--queue-policy",
        choices=list(POLICIES),
        default="drop-oldest",
        help="what a fetch worker does when the queue is full",
    )
    parser.add_argument(
        "--timeout",
        type=_bounded(float, minimum=0.0, inclusive=False),
        default=10.0,
        help="seconds per fetch attempt",
    )
    parser.add_argument(
        "--retries", type=_bounded(int, minimum=0), default=2, help="extra attempts per URL"
    )
    parser.add_argument(
        "--max-bytes",
        type=_bounded(int, minimum=1),
        default=2_000_000,
        help="largest body accepted",
    )
    parser.add_argument(
        "--per-host-delay",
        type=_bounded(float, minimum=0.0),
        default=0.0,
        help="minimum seconds between requests to one host",
    )
    parser.add_argument(
        "--allow-private",
        action="store_true",
        help="allow loopback, private and link-local addresses",
    )
    parser.add_argument("--verbose", action="store_true", help="log each attempt and record")
    parser.add_argument("--log-json", action="store_true", help="write logs as JSON lines")
    return parser


# --- inputs ------------------------------------------------------------------------------


def read_urls(sources: Sequence[str]) -> list[str]:
    """URLs from arguments and files, in order. Raises OSError for an unreadable file."""
    urls: list[str] = []
    for source in sources:
        if source.lower().startswith(("http://", "https://")):
            urls.append(source)
            continue
        with open(source, encoding="utf-8-sig") as lines:
            for line in lines:
                url = line.strip()
                if url and not url.startswith("#"):
                    urls.append(url)
    return urls


def dedupe_key(url: str) -> str:
    """Normalise only what cannot change the response: scheme and host case, an empty
    path, a default port and the fragment."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal: hostname drops the brackets
        host = f"[{host}]"
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = f"{userinfo}@{host}" if userinfo else host
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def make_jobs(urls: Sequence[str], *, keep_duplicates: bool) -> tuple[list[Job], int]:
    """Number the URLs as jobs, skipping repeats unless asked not to."""
    seen: set[str] = set()
    jobs: list[Job] = []
    for url in urls:
        if not keep_duplicates:
            key = dedupe_key(url)
            if key in seen:
                continue
            seen.add(key)
        jobs.append(Job(len(jobs), url))
    return jobs, len(urls) - len(jobs)


# --- logging -----------------------------------------------------------------------------

_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per log line; `extra` fields become top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data.update((k, v) for k, v in vars(record).items() if k not in _RECORD_ATTRIBUTES)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(*, verbose: bool, json_logs: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
        )
    for old in list(log.handlers):
        log.removeHandler(old)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def format_summary(summary: Summary) -> str:
    skipped = summary.duplicates_skipped
    duplicates = f" ({skipped} duplicate{'s' if skipped != 1 else ''} skipped)" if skipped else ""
    outcomes = ", ".join(f"{outcome} {summary.counts.get(outcome, 0)}" for outcome in Outcome)
    if summary.p95_ms is None:
        latency = "no fetches"
    else:
        latency = f"p50 {summary.p50_ms}, p95 {summary.p95_ms}, max {summary.max_ms}"
    return (
        f"summary: {summary.jobs} jobs{duplicates} in {summary.duration_s:.2f}s"
        f"{' (interrupted)' if summary.interrupted else ''}\n"
        f"  outcomes: {outcomes}\n"
        f"  retries {summary.retries}, max queue depth {summary.max_queue_depth}\n"
        f"  fetch latency ms: {latency}"
    )


# --- main --------------------------------------------------------------------------------


def _open_output(path: str | None) -> TextIO:
    """The --out file (closed by main), or stdout switched to UTF-8 with \\n line ends."""
    if path is not None:
        return open(path, "w", encoding="utf-8", newline="\n")
    # A replaced stream may lack reconfigure, or refuse to change.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    return sys.stdout


def _first_cause(exc: BaseException) -> BaseException:
    """The first leaf of a (possibly nested) exception group from the TaskGroups."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def _silence_stdout() -> None:
    """After a broken pipe, point stdout at devnull so the interpreter's final flush
    cannot fail again and replace our exit code."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def main(
    argv: Sequence[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """Run the CLI and return the exit code. `transport` replaces the network in tests."""
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose, json_logs=args.log_json)
    settings = Settings(
        concurrency=args.concurrency,
        consumers=args.consumers,
        queue_max=args.queue_max,
        queue_policy=args.queue_policy,
        timeout=args.timeout,
        retries=args.retries,
        max_bytes=args.max_bytes,
        per_host_delay=args.per_host_delay,
        allow_private=args.allow_private,
    )
    try:
        urls = read_urls(args.inputs)
    except OSError as exc:
        log.error("cannot read input: %s", exc, extra={"event": "usage_error"})
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    jobs, skipped = make_jobs(urls, keep_duplicates=args.keep_duplicates)
    try:
        stream = _open_output(args.out)
    except OSError as exc:
        log.error("cannot open output: %s", exc, extra={"event": "usage_error"})
        return EXIT_USAGE

    log.info(
        "start: %d jobs (%d duplicates skipped), %d workers, %d consumers, queue %d (%s)",
        len(jobs),
        skipped,
        settings.concurrency,
        settings.consumers,
        settings.queue_max,
        settings.queue_policy,
        extra={"event": "start", "jobs": len(jobs), "settings": asdict(settings)},
    )
    try:
        summary = asyncio.run(extract(jobs, settings, stream, transport=transport))
    except KeyboardInterrupt:  # Ctrl-C outside the pipeline's own handler
        log.warning("interrupted", extra={"event": "interrupt"})
        return EXIT_INTERRUPTED
    except Exception as exc:
        cause = _first_cause(exc)
        log.error("run failed: %s", cause, exc_info=cause, extra={"event": "failure"})
        if isinstance(cause, BrokenPipeError) and stream is sys.stdout:
            _silence_stdout()
        return EXIT_FAILURE
    finally:
        if stream is not sys.stdout:
            stream.close()

    summary = replace(summary, duplicates_skipped=skipped)
    log.info(format_summary(summary), extra={"event": "summary", "summary": summary.as_dict()})
    return EXIT_INTERRUPTED if summary.interrupted else EXIT_OK
