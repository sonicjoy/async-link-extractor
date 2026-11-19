# linkx

[![CI](https://github.com/sonicjoy/async-link-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/sonicjoy/async-link-extractor/actions/workflows/ci.yml)

A concurrent producer/consumer web link extractor built on Python asyncio. A pool of fetch workers downloads pages and puts them on a bounded queue; consumers take them off, extract the hyperlinks in a worker thread, and write one JSON line per input URL.

- Python 3.13 or newer (built and tested on 3.13, 3.14 and 3.15, Linux and Windows).
- One runtime dependency, [httpx](https://www.python-httpx.org/). The HTML parser is the standard library's `html.parser`.
- 100% line and branch test coverage; CI fails below 95%.

## Contents

- [Quick start](#quick-start)
- [Output](#output)
- [Options and exit codes](#options-and-exit-codes)
- [How it works](#how-it-works)
- [Trade-offs](#trade-offs)
- [Measured behaviour](#measured-behaviour)
- [Assumptions](#assumptions)
- [Not built, and how I would build it](#not-built-and-how-i-would-build-it)
- [Running this in production](#running-this-in-production)
- [Development](#development)

## Quick start

```bash
uv sync                                    # or: pip install .
uv run linkx urls.txt --out results.jsonl --concurrency 10 --queue-max 100
uv run linkx https://example.com https://www.python.org/ https://httpbin.org/status/503 \
    http://example.com/ https://httpbin.org/json --retries 1 --out results.jsonl
```

`urls.txt` holds one URL per line; blank lines and lines starting with `#` are ignored. URL arguments and files can be mixed. Records go to `--out` or, without it, to stdout. Logs and the summary go to stderr:

```text
21:35:31 INFO    start: 5 jobs (duplicates skipped: 0), workers 10, consumers 1, queue 100 (drop-oldest)
21:35:32 WARNING [#2] attempt 1 failed (http_status: HTTP 503); retrying in 0.95s
21:35:33 INFO    summary: 5 jobs, 1.74s
  outcomes: ok 3, fetch_error 2, parse_error 0, dropped 0, cancelled 0
  retries 1, max queue depth 1
  fetch latency ms: p50 200, p95 1735, max 1735
```

## Output

One JSON object per line, UTF-8, in completion order. Every job gets exactly one record, so the output can be checked against the input. A job is one distinct input URL: repeats are skipped and counted in the summary, unless `--keep-duplicates` is given. The line separators U+2028, U+2029 and U+0085 are escaped, so splitting on any line break gives one record per line.

From the run above (the 127 python.org links shortened here):

```json
{"index": 1, "url": "https://www.python.org/", "final_url": "https://www.python.org/", "outcome": "ok", "http_status": 200, "links": ["https://www.python.org/", "https://www.python.org/psf/", "..."], "error": null, "elapsed_ms": 167, "attempts": 1}
{"index": 4, "url": "https://httpbin.org/json", "final_url": "https://httpbin.org/json", "outcome": "fetch_error", "http_status": 200, "links": null, "error": {"kind": "not_html", "message": "content type 'application/json' is not HTML"}, "elapsed_ms": 417, "attempts": 1}
{"index": 2, "url": "https://httpbin.org/status/503", "final_url": "https://httpbin.org/status/503", "outcome": "fetch_error", "http_status": 503, "links": null, "error": {"kind": "http_status", "message": "HTTP 503"}, "elapsed_ms": 1735, "attempts": 2}
```

| `outcome` | Meaning | `links` | `error` |
| --- | --- | --- | --- |
| `ok` | 2xx HTML page, parsed | list | `null` |
| `fetch_error` | the fetch did not produce a 2xx HTML body | `null` | `{kind, message}` |
| `parse_error` | the parser raised | `null` | `{kind: "parse", message}` |
| `dropped` | fetched, then evicted from the full queue (see [trade-offs](#overflow-policy-drop-oldest-or-block)) | `null` | the fetch's error, or `null` |
| `cancelled` | the run was interrupted before this URL had a record | `null` | `null` |

Fetch error kinds: `invalid_url`, `blocked_address`, `timeout`, `connect`, `too_many_redirects`, `http_status`, `not_html`, `too_large`, `unexpected`.

Links are absolute `http`/`https` URLs from `<a href>`, resolved against `<base href>` if present, otherwise against the final URL after redirects, without fragments, de-duplicated in first-seen order. `javascript:`, `mailto:`, other schemes, empty and fragment-only hrefs are skipped.

## Options and exit codes

| Option | Default | Meaning |
| --- | --- | --- |
| `--out FILE` | stdout | where records go (the file is truncated) |
| `--concurrency N` | 10 | fetch workers (producers) |
| `--consumers M` | 1 | parsing consumers |
| `--queue-max N` | 100 | queue capacity (at least 1; 0 would mean unbounded) |
| `--queue-policy` | `drop-oldest` | `drop-oldest` or `block` when the queue is full |
| `--timeout S` | 10 | seconds per attempt: request, redirects and body together |
| `--retries N` | 2 | extra attempts after timeouts, connection errors, 5xx and 429 |
| `--max-bytes N` | 2,000,000 | largest body accepted, counted after decompression |
| `--per-host-delay S` | 0 | minimum gap between requests to one host |
| `--allow-private` | off | permit loopback, private and link-local addresses |
| `--keep-duplicates` | off | fetch a repeated URL once per occurrence |
| `--verbose` | off | log each attempt and each record |
| `--log-json` | off | one JSON object per log line |

Two more settings exist for library use only (`Settings` in [`models.py`](src/linkx/models.py)): `max_redirects` (9) and `progress_interval` (5 s; 0 turns the progress line off).

| Exit code | Meaning |
| --- | --- |
| 0 | run completed, whatever the per-URL outcomes |
| 1 | output failure or internal error |
| 2 | usage error, unreadable input, or `--out` cannot be opened |
| 130 | interrupted with Ctrl-C |

## How it works

```text
URLs (file / arguments) --> jobs (index, url), duplicates skipped
                              |  one shared iterator
                              v
              N fetch workers --put via overflow policy--> asyncio.Queue(maxsize) --get--> M consumers
                              |  evicted result (drop-oldest)                                 |  parse in a thread
                              +---------------------> Sink.emit(record) <--------------------+
                                                       one JSON line, counts, latencies
```

Everything runs as tasks on one event loop. The work is almost all waiting on the network, so one thread can hold many requests open; switching happens only at `await`, so shared state needs no locks.

**Producers.** `--concurrency N` starts N worker tasks that share one iterator of jobs. `next()` never awaits, so no job is taken twice, and the task count stays at N however long the list is. Each worker fetches, then hands the result to the overflow policy.

**Fetching** ([`fetch.py`](src/linkx/fetch.py)). One shared `httpx.AsyncClient`. An attempt is: wait for the host's slot (`--per-host-delay`), then send the request, follow up to 9 redirects and stream the body under one `asyncio.timeout`. httpx's own timeouts apply per phase (connect, read, ...), so they do not bound an attempt on their own. The body is counted as it streams and the attempt stops past `--max-bytes`. `linkx` asks only for gzip or deflate and decompresses them itself in bounded steps (`zlib` with `max_length`), because httpx inflates a whole network chunk at once: 64 KB of gzip can become 64 MB before any cap sees it. A 50 MB gzip bomb is refused while holding under 5 MB (a test checks this). Only `text/html` and `application/xhtml+xml` are accepted. Timeouts, connection errors, 5xx and 429 are retried with full-jitter exponential backoff (a random wait up to 0.5 s × 2^k, capped at 5 s). `fetch()` turns every failure into a result with an error kind and never raises `Exception`.

**Queue and overflow** ([`overflow.py`](src/linkx/overflow.py)). A plain `asyncio.Queue(maxsize)`. A policy is one function that puts an item and returns what it evicted, if anything. `drop-oldest` checks, evicts and puts with no `await` in between, so on one event loop the three steps cannot interleave with another worker's, without a lock. The worker then writes a `dropped` record for the evicted result straight away.

**Consumers** ([`pipeline.py`](src/linkx/pipeline.py)). Each loops on `await queue.get()`. A failed fetch is written as is; otherwise the page is parsed with `asyncio.to_thread`, so a large page does not stall the event loop.

**Completion.** The consumers run in an outer `TaskGroup` and the workers in an inner one. When the inner group ends, the queue is shut down (`Queue.shutdown()`, Python 3.13). `get()` keeps returning queued items and raises `QueueShutDown` only once the queue is empty, so each consumer stops exactly when the producers have finished **and** the queue is drained.

**Failure isolation.** A bad URL or page becomes a record for that URL and nothing else. `CancelledError` is not an `Exception`, so the `except Exception` boundaries never swallow cancellation. Failures that are not about one URL, such as a full disk on the output or a bug, are not caught: the task fails, the `TaskGroup`s cancel the rest, and the run exits 1 rather than continuing to produce nothing.

**The single writer** ([`sink.py`](src/linkx/sink.py)). Both stages write through one synchronous `Sink.emit`. With no `await` inside, two records can never interleave. It refuses a second record for the same job, flushes each line, and keeps the counts and latencies for the summary. At the end it checks that every job has a record: after an interrupt the missing ones become `cancelled`; on a normal run a missing record is a bug and raises.

**Ctrl-C.** The run installs a `SIGINT` handler with `signal.signal` (Windows has no `loop.add_signal_handler`) that schedules `Pipeline.interrupt()` on the loop. The first Ctrl-C cancels the fetch workers and lets the consumers finish what is already queued; a second cancels the consumers too. Because the handler only schedules a call, cancellation always lands at an `await`, never half-way through writing a record. Without this, `asyncio.run` would cancel the consumers on the first Ctrl-C and lose the queued results; I checked both behaviours on Python 3.13 and 3.15 before choosing.

**Non-public addresses.** By default `linkx` refuses loopback, private, link-local, carrier-grade NAT and multicast addresses, including those hidden inside IPv6 forms (IPv4-mapped, NAT64 `64:ff9b::/96`, IPv4-compatible). Two httpx hooks do the checking, on the first request and on every redirect hop, so a public URL cannot redirect into an internal network (for example a cloud metadata endpoint):

- a request hook resolves the host name and refuses it if any address is not public;
- a response hook checks the address the connection actually reached, before the body is read. httpx resolves the name again to connect, so a hostile DNS server could answer "public" to the first lookup and "internal" to the second (DNS rebinding). The second check stops anything the internal service returns from being read or written out; the request itself has already been sent by then. A test reproduces the attack against a real local server.

Proxy settings from the environment are ignored, so the connected address is always the real server. `--allow-private` turns both checks off.

**Observability.** Records are the per-URL log. Operational logs go to stderr through the standard `logging` module, each per-URL line tagged with the job index: start settings, a progress line every 5 s (done, in flight, queue depth, dropped), each retry, drop and interrupt, and the summary (counts per outcome, retries, highest queue depth, p50/p95/max fetch latency). `--log-json` makes every line a JSON object for a log collector.

## Trade-offs

### Overflow policy: drop-oldest or block

| | `drop-oldest` (default) | `block` |
| --- | --- | --- |
| Data loss | the oldest queued results when consumers fall behind | none |
| Fetch speed | never slowed | slowed to the consumers' pace (backpressure) |
| Memory | bounded | bounded |
| Suits | a live feed, where a newer value replaces an older one | a complete crawl or batch job |

The requirement asks for trimming the oldest entries when the queue balloons, so `drop-oldest` is the default. For a finite list of URLs that choice has a real cost: a drop throws away a fetch that has already been paid for. Every dropped URL still gets a `dropped` record that keeps the fetch's status and error, so nothing disappears silently, and `--queue-policy block` is the right setting when completeness matters more than freshness.

### Tasks, threads or processes

Tasks for fetching: one thread holds many open requests, and the drop-oldest rule needs no lock. Threads only for parsing, to keep the loop responsive, not for speed: under the GIL, pure-Python parsing in several threads is no faster than in one (see the numbers below). Processes are the route to parallel parsing on the standard build, and are how this scales out (below).

### A fixed worker pool, not a semaphore per URL

The common pattern is one task per URL behind an `asyncio.Semaphore`. It creates every task up front, so memory grows with the list, and in `block` mode a task waiting on a full queue must hold or release its semaphore slot by hand. N workers pulling from one iterator bound the task count and give backpressure with no extra mechanism.

### `Queue.shutdown()`, not a sentinel item

A sentinel ("no more items") needs one per consumer, and on a full drop-oldest queue it would either evict a real result or have to wait for space while the run is stopping. `shutdown()` never waits and stops any number of consumers. It needs Python 3.13, which is why 3.12 is not supported.

### Duplicates

Repeated URLs are fetched once by default. Normalisation is deliberately light: only differences that cannot change the response are ignored (scheme and host case, an empty path versus `/`, a default port, the fragment). `http://a.com` and `HTTP://A.com:80/#top` are the same; `/x` and `/x/`, or reordered query parameters, are not, because a server may treat them differently.

## Measured behaviour

[`scripts/demo_backpressure.py`](scripts/demo_backpressure.py) runs 200 canned pages of 1,000 links each (about 10 ms of parser CPU per page) through a queue of 10 with 20 fetch workers. No network is involved. CI runs it and fails if `drop-oldest` drops nothing or `block` drops anything.

```text
Python 3.15.2, GIL on                                Python 3.15.2, GIL off (free-threaded)
policy                    ok  dropped  seconds       policy                    ok  dropped  seconds
drop-oldest, 1 consumer   11      189     0.17       drop-oldest, 1 consumer   14      186     0.18
block, 1 consumer        200        0     2.19       block, 1 consumer        200        0     2.27
block, 4 consumers       200        0     2.16       block, 4 consumers       200        0     0.82
```

- `drop-oldest` finishes at fetch speed and loses most pages; `block` loses nothing and runs at the parser's pace.
- With the GIL on, four consumers are no faster than one, because parsing is pure-Python CPU work. On the free-threaded build the same four consumers are 2.8 times faster.

## Assumptions

- The input is a finite list of URLs, and links found are reported, not followed.
- "The markup" is the HTML the server returns; JavaScript is not run.
- Only `<a href>` counts as a hyperlink (not `<link>`, `<area>` or `<img>`).
- Non-HTML responses, error statuses and oversize bodies are reported as fetch errors, not parsed.
- Output order is completion order; `index` gives input order.
- A non-2xx status after retries is the URL's result, not a reason to fail the run (exit 0).
- The URL list may come from an untrusted source, hence the default refusal of non-public addresses.

## Not built, and how I would build it

| Item | Approach |
| --- | --- |
| robots.txt | Opt-in `--respect-robots`: fetch `/robots.txt` once per host through the same client and checks, parse it with `urllib.robotparser`, cache per host, and record disallowed URLs as `fetch_error` with kind `robots`. Off by default because this tool fetches a list it is given rather than crawling, but respecting site terms matters for any production fetcher. |
| Dockerfile | Multi-stage: `uv sync --locked --no-dev` into a slim Python 3.15 image, non-root user, `linkx` as the entrypoint. |
| Recursive crawl | Feed links from `ok` records back to the producers as new jobs with `depth = parent + 1`, stopped by `--max-depth`. Without a depth limit a crawl does not end. |
| URL store | A store of every URL seen with the time it was last parsed. It replaces the in-memory duplicate check, prevents refetching across runs, and lets a scheduler refetch pages older than a chosen age. |
| DNS pinning | Under DNS rebinding the connected-address check refuses the response, but the GET has already reached the internal address. A custom httpx transport that connects to the exact address that was checked (keeping the host name for TLS and the `Host` header) would stop the request itself. |
| Proxy support | Environment proxies are ignored because a proxy resolves names on its own. An explicit `--proxy` would need the address checks to move to the proxy side, or to trust the proxy's own egress rules. |
| Logged URLs | URLs are logged and written as given, including any credentials or tokens in them, and control characters reach plain-text logs. Redact userinfo and escape control characters in the plain log format. |
| Byte-bounded queue | The queue is bounded by count. Worst case memory is (workers + queue + consumers) bodies of up to `--max-bytes` each, about 250 MB typical and under 1 GB worst case at defaults. A byte budget with a condition variable would bound it directly. |
| `Retry-After` | Honour it on 429 and 503, capped, instead of the fixed backoff. |
| Per-host concurrency cap | Today the limit is a time gap per host; a per-host semaphore would also cap simultaneous connections to one site. |
| Metrics | A long-running service would export counters (fetches by outcome, drops, retries), a gauge (queue depth) and a histogram (fetch latency) to Prometheus or OpenTelemetry, from the same points that feed the summary today. |

## Running this in production

This tool is one process with an in-memory queue. A production acquisition pipeline keeps the same shape but puts a durable broker between the stages so each scales, fails and restarts on its own:

```text
URL scheduler --> [urls topic, keyed by host] --> fetchers --> [raw-html topic, keyed by host] --> parsers --> [links topic] --> URL store
                                                                      |
                                                                      +--> replay: re-parse old pages without refetching
```

- **Kafka between the stages, partitioned by host.** All URLs for one host land on one partition, so one fetcher owns each host and per-host politeness needs no shared state. Partition count caps parallelism; add fetcher pods up to it.
- **Concurrency inside each process, more processes for scale.** Each fetcher pod still runs many concurrent fetches on one event loop: waiting is cheap in-process, and one request per pod would waste a pod per open connection. More pods are for throughput beyond one event loop, CPU for parsing, and failure isolation. Kubernetes restarts a crashed pod; that is where restart logic belongs, not in a supervisor inside the process.
- **A raw-HTML topic.** Keeping fetched pages lets parsers be fixed and re-run over history without hitting sites again.
- **Idempotent consumers.** Kafka delivers at least once, so a parser may see a page twice. Writing results keyed by URL and fetch time (an upsert) makes a repeat harmless.
- **Backpressure becomes consumer lag.** The queue-full decision turns into a retention and lag policy: for live data, consumers skip to the newest offsets (the drop-oldest equivalent); for a complete crawl, they work through the backlog (block).
- **Shutdown** follows the same rule as here: stop taking new work, finish what is in hand, commit offsets, exit.

## Development

```bash
uv sync
uv run pytest --cov            # 229 tests, about 6 s
uv run ruff check && uv run ruff format --check
uv run mypy                    # strict
uv run python scripts/demo_backpressure.py
```

CI runs all of the above on Ubuntu with Python 3.13, 3.14 and 3.15, and on Windows with 3.15, and writes the coverage table to the job summary.

The fetcher's clock, sleep, randomness and DNS, and the pipeline's parser, are injectable, so tests cover timeouts, retries, backoff, redirects into private networks, gzip bombs, interrupts and output failures without a network or real waits. The end-to-end test runs the real CLI against a local HTTP server with a dead port, a 404, a JSON file and an invalid line among good pages.

### How this was built

I built this with an AI coding agent (Claude) in a fixed process:

1. A concept review from seven angles, then a design document reviewed in five stages by fresh-context reviewer agents. [`docs/designs/linkx.md`](docs/designs/linkx.md) is the approved design; [`.review/LEDGER.md`](.review/LEDGER.md) lists every review, what it changed, the reviewer advice I overruled and why, and the asyncio and httpx behaviour I confirmed by experiment rather than taking from memory.
2. Tests written from the design before each module, and shown failing first.
3. Small commits, each with its tests.
4. An independent review for bugs, edge cases and security before merging.
