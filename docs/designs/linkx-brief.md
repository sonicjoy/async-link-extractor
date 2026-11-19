# linkx: concurrent web link extractor

* **Author:** Joe Luo
* **Date:** 2026-11-19

## Overview

`linkx` is a Python asyncio command-line tool (built on 3.15, runs on 3.13+) for someone who has a list of URLs and wants the hyperlinks on each page. A producer fetches the URLs concurrently and puts each result on a bounded in-memory queue. A consumer parses the HTML off the event loop and writes one JSONL record per input URL. The value is an output that can be audited against the input: every URL ends as ok, fetch error, parse error or dropped, with counts and p95 latency in a summary.

## Analysis

All seven lenses agree the core is sound and that `asyncio.Queue` with `shutdown()` is the right base. They found six contradictions.

1. **The consumer cannot own "one record per URL".** A trimmed entry never reaches it. Pattern (all lenses): a single synchronous `sink.emit(record)` that owns the file and the counts. The eviction function returns the evicted `FetchResult`; the evicting fetch task and the consumer both call `emit`. With no `await` inside, lines cannot interleave and no lock or second queue is needed. The summary is derived in the sink, so it cannot disagree with the file.
2. **A trimmed fetch error loses its cause.** Errors travel through the queue, so eviction turns one into "dropped". Keep one path and have the dropped record retain `http_status`, `error` and `elapsed_ms`.
3. **TaskGroup works against isolation.** One uncaught exception cancels every sibling. Each fetch must catch `Exception` (never `CancelledError`) at its boundary and return a `FetchResult`, with a test using an unexpected exception type. Keep the consumer in the same group so a writer failure cancels the producers.
4. **"Cancellation-safe shutdown" is core, but Ctrl-C is an extra.** They are the same code. `shutdown(immediate=True)` discards queued items, and unfetched URLs get no record, so the invariant as stated fails on interrupt.
5. **Memory is not bounded.** The queue is bounded by count, not bytes, and a semaphore still creates one task per URL up front. The real bound is (`concurrency` + `queue-max`) × maximum response size. A fixed pool of N workers pulling from a URL iterator bounds the task count and removes the semaphore.
6. **The drop-oldest rationale does not fit a finite list.** A drop discards a fetch already paid for, and parsing outpaces fetching, so drops will not occur at the defaults. The README must say this plainly; the demo needs an injected consumer delay through a test seam, not a CLI flag.

Technical corrections: httpx has no size-cap option (stream and count bytes); its timeouts are per phase, so wrap each attempt in `asyncio.timeout()` and retry on both timeout types; `follow_redirects` is off by default; `html.parser` does not raise on bad markup, so the parse-error test needs an injected parser; `RobotFileParser.read()` blocks; `add_signal_handler` is unavailable on Windows; `--queue-max 0` means unbounded and must be rejected. httpx and pytest-asyncio support for 3.15 was not verified.

Secondary trade-offs: output is in completion order, so records need an input index; `to_thread` buys loop responsiveness, not throughput.

**Ideal final result:** every input index reaches exactly one terminal outcome through one emit point; memory is bounded by construction; the only custom concurrency code is one await-free eviction function; the run checks that records emitted equals inputs.

| Lens | Status |
| --- | --- |
| egm-design | completed |
| egm-codebase-details | completed |
| egm-zoom-out | completed |
| egm-comprehension | completed |
| egm-implementation-readiness | completed |
| egm-structural | completed |
| egm-inventive-catalyst | completed |

## Goals vs. Non-Goals

* **In-Scope Goals:** concurrent fetch with timeouts, a response size cap, content-type check and minimal retries; bounded queue with drop-oldest default and `block` by flag; completion through `Queue.shutdown()`; parsing through `asyncio.to_thread`; a single sink writing one record per input; summary and logs on stderr; a test per failure path; the drop demo; CI; a minimal interrupt path.
* **Out-of-Scope Non-Goals:** crawling beyond the supplied list; per-host rate limit, robots.txt and Dockerfile (design notes unless time remains; six lenses advise deferring them now); byte-based queue bound; a message broker (one README paragraph); security hardening (later review); a second-language implementation.

## Alternatives Weighed

* **Blocking as the default policy.** Rejected by author: the problem statement asks for trimming the oldest entries, and for a live feed the newest data matters most. Blocking stays available by flag. Reviewers accept the decision but find the live-feed reason weak for a finite list.
* **Sentinel item for completion.** Rejected by author: one sentinel per consumer, and putting it into a full drop-oldest queue trims a real entry.
* **Hand-written queue class with `close()`.** Rejected by author: `asyncio.Queue.shutdown()` exists from 3.13, so only the trimming rule needs custom code.
* **Python 3.12 minimum.** Rejected by author: it lacks `Queue.shutdown()`.
* **robots.txt on by default or built early.** Rejected by author: the tool does not crawl; it adds a fetch per host, a cache and a failure policy. Opt-in, built last.
* **Second-language implementation for comparison.** Rejected by author: the time is better spent on the quality of one implementation.
* **Second unbounded records queue, or a tombstone left in the queue, for dropped records.** Rejected by reviewers: the first defeats the bound, the second frees no slot.
* **`collections.deque(maxlen)` buffer.** Rejected by reviewers: discards silently and loses `shutdown()` and blocking mode.
* **`join()`/`task_done()` accounting.** Rejected by reviewers: every eviction needs its own `task_done()` or `join()` hangs; `shutdown()` plus `QueueShutDown` is sufficient.
* **`ProcessPoolExecutor` for parsing.** Rejected by reviewers: pickling cost; named in the README as the CPU-scaling route.

## Key Decisions & Staging for Next Steps

**Resolved (reviewer consensus on the five questions):**

1. Dropped records reach the output through the single synchronous sink described above.
2. `{url, status, links, error}` is not sufficient. Use `{index, url, final_url, outcome, http_status, links, error: {kind, message}, elapsed_ms, attempts}`; `outcome` is `ok|fetch_error|parse_error|dropped`; `links` is `null` unless parsed.
3. Drop-oldest on `asyncio.Queue` is correct with many fetch tasks only if `put_nowait` → `QueueFull` → `get_nowait` → `put_nowait` contains no `await`. Call `shutdown()` once, after all fetchers exit.
4. Keep `to_thread`. The README claims loop responsiveness only, notes free-threaded builds, and states that a running parse cannot be cancelled.
5. Summary and logs go to stderr; the sink flushes per line and asserts that outcome counts sum to the input count.

**Open for the design draft:**

* **Interrupt:** add a `cancelled` outcome, or scope the invariant to normal completion.
* **Fetch concurrency:** N workers over an iterator (five lenses), or semaphore with a task per URL (author's current choice).
* **Block mode:** hold the fetch slot while awaiting `put()` (true backpressure) or release it first. Reviewers split.
* **Outcome mapping:** where 4xx, non-HTML, oversize, invalid URL and decode failure land; whether a bad href skips one link or fails the page.
* **Exit codes:** suggested 0, 1 for any non-ok (always or with `--strict`), 2 usage, 130 interrupt.
* **p95:** per attempt or per URL including backoff; failures included or not; behaviour for small or empty samples.
* **Input rules:** file versus URL positionals (or `--input FILE`), blank lines, comments, duplicates (one record each is implied by `index`).
* **Numeric defaults:** timeouts, total per-URL deadline, retry count, backoff, size cap, redirect limit, 429 handling.
* **Output:** `--out` truncate or append; UTF-8 and `\n` on Windows.
* **Extras order:** one lens would put per-host rate limit ahead of the Dockerfile.
