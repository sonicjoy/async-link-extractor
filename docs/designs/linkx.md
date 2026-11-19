# linkx: concurrent web link extractor

*   **Author:** Joe Luo
*   **Status:** Approved (wording updated 2026-11-19 to match review fixes, approved by the author)
*   **Date:** 2026-11-19

--------------------------------------------------------------------------------

## Overview

`linkx` is a command-line tool that takes a list of URLs and writes, for each one, the hyperlinks found on that page. A producer stage fetches pages concurrently and puts each fetch result on a bounded in-memory queue. A consumer stage takes results off the queue, parses them and writes one JSON record per URL. It runs on Python 3.13 or newer and is built and shipped on 3.15.

The review that shaped this design, and further options it rejected, are in [linkx-brief.md](linkx-brief.md).

## The Problem

Fetching many pages is slow and unreliable: hosts time out, return errors, or send non-HTML or very large bodies.

The requirement is a producer and a consumer that run concurrently with a queue between them, where one bad fetch or parse does not affect the others, and where the oldest queue entries are dropped if the queue grows too large.

## Goals

*   **Concurrent stages.** The consumer stops when fetching has finished and the queue is empty.
*   **Concurrent fetching** with a limit, timeout, bounded retries, size cap and content-type check.
*   **Failure isolation.** A bad fetch or parse produces an error record for that URL and affects no other.
*   **No wasted work.** Each distinct URL is fetched once by default.
*   **Per-host politeness.** An optional minimum gap between requests to one host.
*   **Safe by default.** Non-public addresses are refused, including on redirects.
*   **Bounded queue with two overflow policies:** `drop-oldest` (default) and `block`.
*   **Auditable output.** Once the pipeline has started, every job has exactly one record whether the run completes or is interrupted, and the tool checks this.
*   **Operational summary** on stderr.
*   **Clean stop on Ctrl-C:** results already queued are still written, unless Ctrl-C is pressed again.
*   **Tests for every failure path**, with a coverage gate in CI.
*   **A reproducible demonstration** of both overflow policies under a slow consumer.

## Non-Goals

*   Crawling: links are reported, never followed. No JavaScript rendering.
*   Bounding the queue by bytes. The bound is by entry count.
*   A message broker or multi-process deployment.
*   Rate limiting consumers, or restarting failed tasks.
*   A Dockerfile and robots.txt checking. They follow once this design is implemented and reviewed, if the schedule allows.

## Open Questions

None.

## Critical User Journeys (CUJs)

### CUJ 1: Extract links from a list

*   **Given**: a file of 100 distinct URLs, a few of them dead, slow or not HTML.
*   **When**: the operator runs `linkx urls.txt --out results.jsonl`.
*   **Then**: the file holds 100 lines; good pages list their links, bad ones carry an error; the summary counts sum to 100.

### CUJ 2: Consumer falls behind

*   **Given**: the demo script: canned responses, a small queue and a slow parser.
*   **When**: it runs with `drop-oldest`, then with `block`.
*   **Then**: the first run finishes at fetch speed with some records `dropped`; the second takes longer and drops none.

### CUJ 3: Interrupted run

*   **Given**: a long run in progress.
*   **When**: the operator presses Ctrl-C.
*   **Then**: in-flight fetches are cancelled, queued results are parsed and written, every remaining job gets a `cancelled` record, the summary is printed, and the exit status is 130. A second Ctrl-C also stops the parsing of queued results, once any running parse ends.

## Technical Plan (Architecture)

Everything runs as asyncio tasks on one event loop. The only thread use is `asyncio.to_thread` for parsing.

```text
jobs (index, url)
      |  one shared iterator
      v
N fetch workers  --put via overflow policy-->  asyncio.Queue(maxsize)  --get-->  M consumers
      |  evicted result (drop-oldest)                                               |
      +----------------------->  Sink.emit(record)  <-------------------------------+
```

A **job** is one URL to fetch, a **result** is what a fetch produced and the queue holds, and a **record** is one output line.

### Components (under `src/linkx/`)

| Module | Responsibility |
| --- | --- |
| `models.py` | `Outcome`, `Job`, `FetchResult`, `Record`, `Summary`, `Settings` |
| `fetch.py` | `Fetcher(client, settings, limiter, resolve, sleep, rng, clock)`; `await fetcher.fetch(job) -> FetchResult`, which never raises `Exception` |
| `ratelimit.py` | `HostLimiter(delay, clock, sleep)` |
| `overflow.py` | The overflow policies and `POLICIES`, a name-to-policy table |
| `parse.py` | `extract_links(html, base_url) -> list[str]`. Pure function |
| `sink.py` | `Sink(stream, jobs)`: `emit`, `close`. Synchronous; it holds the jobs so `close` can write `cancelled` records |
| `pipeline.py` | `Pipeline(jobs, settings, sink, fetcher, parse=extract_links)`: `await run()`, `interrupt()` |
| `cli.py` | Arguments, input reading, signal handler, exit code |

`resolve`, `sleep`, `rng`, `clock` and `parse` default to the real implementations and are replaced in tests.

### Input

An argument starting with `http://` or `https://`, in any letter case, is a URL; anything else is a file of URLs, one per line, with blank lines and `#` comments ignored. By default a URL equal to an earlier one is skipped and counted by `cli.py`, which adds the count to the summary. Two URLs are equal if they match after lowercasing the scheme and host, treating an empty path as `/`, removing a default port (80 for `http`, 443 for `https`) and removing the fragment. Nothing else is normalised. `--keep-duplicates` turns this off. Each remaining URL becomes a job with the next index, and its record carries the URL as the operator wrote it.

### Producers: a fixed pool of fetch workers

`--concurrency N` starts N worker tasks. Each loops over one shared iterator of jobs: fetch, then hand the result to the overflow policy.

The fetcher first validates the URL: it must parse, have scheme `http` or `https`, and have a host. A URL that fails is `invalid_url` with no attempt and no network use.

One attempt is:

1.  `await limiter.wait(host)`, which enforces `--per-host-delay` and is outside the timeout.
2.  Under `asyncio.timeout(--timeout)`: send the request, let httpx follow up to 9 redirects, and stream the body. httpx's own timeout is set to the same value.

A request hook on the shared `httpx.AsyncClient` runs before the first request and before every redirect hop (confirmed by experiment). It applies the same validation to each redirect target and, unless `--allow-private` is set, raises `blocked_address` unless every address the host resolves to is public: `ipaddress.is_global`, not multicast, not in 192.0.0.0/24 or 192.88.99.0/24, and an IPv6 address that embeds an IPv4 one (mapped, NAT64, IPv4-compatible) is judged by that IPv4 address. Because httpx resolves the name again to connect, a response hook also checks the address actually connected to, on every hop and before the body is read; this stops DNS rebinding from reading an internal response, although the request itself has been sent. The client ignores proxy settings from the environment so that the connected address is the real server. Redirect hops are not rate limited.

A non-2xx final response, including a redirect with no `Location`, is `http_status`. A 2xx must have media type `text/html` or `application/xhtml+xml`, checked before the body is read, else `not_html`. A declared `Content-Length` over `--max-bytes` fails at once. `linkx` asks only for gzip or deflate and decompresses them itself in bounded steps, so the body never grows past `--max-bytes` before the attempt ends with `too_large`; any other content encoding is `unexpected`. The body is decoded with the response charset, or UTF-8 when the charset is unknown or not a text encoding, replacing undecodable bytes.

Exceptions map to kinds as follows: `TimeoutError` and `httpx.TimeoutException` to `timeout`; a resolver failure or any other `httpx.TransportError` to `connect`; `httpx.TooManyRedirects` to `too_many_redirects`; any other `Exception` to `unexpected`. URLs are validated before any request, and an unparseable redirect `Location` reaches `linkx` as `httpx.RemoteProtocolError`, so it is `connect` and retried. `CancelledError` is not an `Exception` and passes through.

`--retries R` allows up to R further attempts. `timeout`, `connect`, 5xx and 429 are retried; nothing else is. Before retry k (from 1) the fetcher sleeps a random time between 0 and `min(5, 0.5 * 2**k)` seconds. `Retry-After` is ignored.

`HostLimiter` keeps per host the earliest time the next attempt may start. `wait` reserves the next slot, with no `await` between reading and updating it, then sleeps until it. A waiting worker keeps its place in the pool.

### Queue and overflow policy

The queue is `asyncio.Queue(maxsize=queue_max)`, first-in first-out. Completion uses `Queue.shutdown()`, added in Python 3.13, which is why 3.12 is not supported.

A policy is one async function, `put(queue, result) -> FetchResult | None`, returning the result it evicted, or `None`.

*   **`drop-oldest`**: if the queue is full, `get_nowait()` the oldest result, `put_nowait()` the new one and return the evicted one. No `await` separates these steps or the worker's following `emit` of a `dropped` record, so no other task can run between them.
*   **`block`**: `await queue.put(result)`. A waiting worker fetches nothing else, so a full queue slows fetching to the consumers' pace.

### Consumers

`--consumers M` (default 1) starts M tasks. Each loops on `await queue.get()` and exits on `QueueShutDown`, raised only when the queue is shut down and empty. A result carrying a fetch error is emitted as it is. Otherwise the consumer awaits `asyncio.to_thread(parse, html, final_url)` inside a catch of `Exception` and emits `ok` or `parse_error`. The thread keeps the event loop responsive; more than one consumer adds parsing throughput only on a free-threaded build.

`extract_links` uses the standard-library `html.parser`. It collects `<a href>`, skips empty and fragment-only values, resolves the rest against `<base href>` if present, else the final URL, keeps `http` and `https` results, removes fragments and de-duplicates in order. An href that cannot be resolved is skipped.

### Completion and interrupt

```text
Pipeline.run:
    with SIGINT handler calling interrupt():
        async with TaskGroup:             # consumers
            start M consumers
            try:
                async with TaskGroup:     # fetch workers
                    start N workers
            finally:
                queue.shutdown()
        summary = sink.close(interrupted)
    return summary

Pipeline.interrupt:                       # called on the loop
    first call:  cancel the worker tasks
    later calls: cancel the worker and consumer tasks
```

`interrupt()` records that it was called. `run` starts no workers if that has already happened, and `interrupted` is true whenever it was called, which is also the rule for exit 130.

`run` installs a `SIGINT` handler that schedules `interrupt()` on the loop with `call_soon_threadsafe`, and restores the previous handler after the sink is closed, so a late Ctrl-C cannot cut the closing records or the summary short. A second Ctrl-C records the job whose parse is running as `cancelled`; that parse thread still finishes before the process exits. A cancelled child task does not fail its group, so `run` returns normally on either interrupt. A `KeyboardInterrupt` outside that span, such as while reading the input, is caught in `cli.py`, which exits 130; no records exist yet.

`Sink.emit` raises on a repeated index; otherwise it marks the index as written, then writes and flushes the line. `Sink.close(interrupted)` emits a `cancelled` record for each index with no record if the run was interrupted, and raises if one is missing on an uninterrupted run: that is a bug, not a result.

### Failure handling

An output failure or a bug is not caught in the pipeline: `emit` is outside the fetcher's and the parse call's catches. The failing task ends, the groups cancel the rest, and `cli.py` reports the error and exits 1. The sink refuses every write after one has failed, so no record follows a gap.

### Record and outcomes

One JSON object per line, UTF-8, `\n` endings, in completion order. U+2028, U+2029 and U+0085 are escaped so that no line splitter breaks a record:

```json
{"index": 0, "url": "...", "final_url": "...", "outcome": "ok", "http_status": 200,
 "links": ["..."], "error": null, "elapsed_ms": 123, "attempts": 1}
```

| Outcome | When | `links` | `error` |
| --- | --- | --- | --- |
| `ok` | 2xx HTML parsed | list | `null` |
| `fetch_error` | the fetch did not yield a 2xx HTML body | `null` | `{kind, message}` |
| `parse_error` | the parser raised | `null` | `{kind: "parse", message}` |
| `dropped` | evicted from the queue | `null` | the fetch's error, or `null` |
| `cancelled` | no record when an interrupted run ended | `null` | `null` |

Fetch error kinds: `invalid_url`, `blocked_address`, `timeout`, `connect`, `too_many_redirects`, `http_status`, `not_html`, `too_large`, `unexpected`. `final_url` and `http_status` are `null` when the failure happened before a response to the final request arrived; a hop refused by the address check has none either. `elapsed_ms` and `attempts` are `null` for `cancelled`; otherwise `elapsed_ms` is the whole fetch including limiter waits, retries and backoff.

### Command line

```text
linkx URL_OR_FILE [URL_OR_FILE ...] [--out FILE] [--keep-duplicates]
      [--concurrency 10] [--consumers 1]
      [--queue-max 100] [--queue-policy drop-oldest|block]
      [--timeout 10] [--retries 2] [--max-bytes 2000000]
      [--per-host-delay 0] [--allow-private] [--verbose] [--log-json]
```

`--concurrency`, `--consumers`, `--queue-max` and `--max-bytes` must be at least 1; `--timeout` above 0; `--retries` and `--per-host-delay` at least 0. `--out` truncates; without it records go to stdout. The summary and logs go to stderr.

| Exit code | Meaning |
| --- | --- |
| 0 | Run completed, whatever the per-URL outcomes |
| 1 | Output failure or internal error |
| 2 | Usage error, unreadable input, or `--out` cannot be opened |
| 130 | Interrupted |

### Summary

Jobs, duplicates skipped, counts per outcome, total retries, the highest queue depth reached, run time, and p50, p95 and maximum of `elapsed_ms` over jobs that reached a fetch result. Percentiles use the nearest-rank method and are omitted when there are no samples.

### Logging

The records are the per-URL event log. Operational logs use the standard `logging` module, logger `linkx`, on stderr:

| Level | Events |
| --- | --- |
| `INFO` (default) | start, with the effective settings; a progress line every 5 seconds (done, total, in flight, queue depth, dropped); the summary |
| `WARNING` | each retry, each drop, each interrupt |
| `ERROR` | an output failure or internal error, with traceback |
| `DEBUG` (`--verbose`) | each attempt and each record emitted |

Every per-URL line carries the job index, so a log line can be matched to its record. `--log-json` writes each log line, including the summary, as one JSON object. Metrics export and tracing are not built; the README maps the summary fields to the counters and histograms a service would publish.

## Alternatives Considered

All were rejected by the author; more are in the brief.

*   **Threads for the producer and consumer.** Eviction would need a lock and a thread-safe queue, and a thread cannot be cancelled. The work is waiting on the network, which tasks on one event loop handle without locks.
*   **One task per URL behind a semaphore.** The shortest code, but it creates a task per job up front. A fixed pool bounds the task count and gives backpressure with no extra mechanism.
*   **`block` as the default policy.** It loses nothing and suits a finite list, but the requirement asks for dropping, which suits a live feed where newer data replaces older. A drop discards a completed fetch, so `block` is one flag away.
*   **Relying on `asyncio.run` for Ctrl-C.** The first Ctrl-C cancels the consumers along with the workers, and a second raises `KeyboardInterrupt` at an arbitrary point, including inside a write (both confirmed by experiment). An explicit handler keeps cancellation at `await` points.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Memory:** at most N + `queue-max` + M bodies are held, each up to `max-bytes` decoded and up to four times that as a string: about 250 MB typical and 900 MB worst case with defaults.
*   **Security:** see the request hook under Producers. TLS verification stays on.

### Future Improvements

Both are outside this design; the non-goal on crawling stands.

*   **Recursive crawl.** The links in `ok` records can be fed back to the producers as new jobs, turning the pipeline into a loop. `Job` would gain a `depth`, set to the parent's depth plus one, and a `--max-depth` limit would stop the loop. Without a limit a crawl does not end.
*   **URL store.** A crawl needs a store of every URL seen with the time it was last parsed. It replaces the in-memory duplicate check, stops a page being fetched twice across runs, and lets a scheduler re-fetch pages older than a chosen age.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Each step writes its tests first, shows them failing, and leaves CI green.

### Target Files to Create/Modify

1.  **Scaffold** (Create): `pyproject.toml`, `uv.lock`, `.python-version` (3.15), `LICENSE` (MIT), the package skeleton, `src/linkx/cli.py` (argument parser only), `tests/test_cli.py` (`--help` and usage errors). Build backend `hatchling`; `requires-python >= 3.13`; dependency `httpx>=0.28,<1`; dev group `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`), `pytest-cov`, `ruff`, `mypy`; coverage source `src/linkx` with branches; script `linkx = linkx.cli:main`.
2.  **CI** (Create): `.github/workflows/ci.yml`. The Verification Plan on `ubuntu-latest` for Python 3.13, 3.14 and 3.15, and on `windows-latest` for 3.15.
3.  **Models** (Create): `tests/test_models.py`, `src/linkx/models.py`. Record serialisation for each outcome, including the `null` rules.
4.  **Parser** (Create): `tests/test_parse.py`, `src/linkx/parse.py`. One test per rule under Consumers, plus malformed HTML and an empty page.
5.  **Overflow policies** (Create): `tests/test_overflow.py`, `src/linkx/overflow.py`. FIFO order; `drop-oldest` returns `None` when not full and the oldest when full; `block` waits and loses nothing; many concurrent putters leave every result queued or returned; `POLICIES` holds exactly the two names the CLI offers.
6.  **Sink** (Create): `tests/test_sink.py`, `src/linkx/sink.py`. One flushed line per emit; counts; double emit raises; both `close` cases; percentiles for empty, single and many samples.
7.  **Rate limiter and fetcher** (Create): `tests/test_ratelimit.py`, `tests/test_fetch.py`, `src/linkx/ratelimit.py`, `src/linkx/fetch.py`. With `httpx.MockTransport` and fake `sleep`, `rng` and `resolve`: one test per fetch error kind and per retry rule, plus 5xx then success, retries exhausted, backoff bounds, a redirect followed, a redirect to a private address refused and allowed with the flag, invalid URLs sending no request, and the limiter wait not counted against the timeout. Limiter with a fake clock: same host spaced, different hosts not delayed.
8.  **Pipeline** (Create): `tests/test_pipeline.py`, `src/linkx/pipeline.py`. Consumers stop only when workers are done and the queue is empty; several consumers; drops become `dropped` records keeping the fetch error; `block` drops nothing; a raising parser yields `parse_error`; a failing sink ends the run; `interrupt()` before `run`, during fetching, and twice; a warning logged per drop and per retry; the progress line; record count equals job count in every case.
9.  **CLI and end-to-end** (Modify/Create): `src/linkx/cli.py`, `tests/test_cli.py`, `tests/test_e2e.py`. One test per rule under Input and Command line; exit codes 0, 1 (a stream that raises `OSError`), 2 and 130. The end-to-end test runs the CLI with `--allow-private` against a local `http.server` with one dead URL among good ones.
10. **Demo and README** (Create): `scripts/demo_backpressure.py`, `README.md`. The demo prints drop counts and run times for both policies and exits non-zero unless `drop-oldest` dropped some and `block` dropped none.

### Verification Plan

*   **Compilation/Build**: `uv sync`, then `uv run linkx --help`.
*   **Unit Tests**: `uv run pytest --cov --cov-fail-under=95`.
*   **Static Analysis & Linting**: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`.
*   **Test Coverage Requirement**: 95% line and branch, as above.
*   **E2E/Integration**: `tests/test_e2e.py` runs with the unit tests; `uv run python scripts/demo_backpressure.py` runs in CI from step 10.

--------------------------------------------------------------------------------

*Drafted with EGM*
