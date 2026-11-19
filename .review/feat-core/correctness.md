# Correctness review

**Verdict:** PASS WITH ISSUES

Spec: `docs/designs/linkx.md`, **Status: Approved** (2026-11-19). Branch `feat/core` at f51d6b3 against `main`.

The core guarantees hold under load. A randomized stress run of 1,000 pipelines exercised random job counts (0–60), both policies, 1–8 workers, 1–4 consumers, queue sizes 1–5, retries, timeouts, too-large bodies, redirect loops, parser exceptions, invalid URLs, and no interrupt, an interrupt before `run`, one interrupt or two interrupts at random times. Every run produced exactly one record per job, no cancelled records on uninterrupted runs, and summary counts that summed to the job count (`done, failures: 0`). The findings below are about edges around that core, plus one test gap on the most important interrupt behaviour.

## Requirements coverage

| Requirement | Status | Implemented in | Proven by test |
|---|---|---|---|
| Consumers stop only when fetching has finished and the queue is empty | met | `pipeline.py:98-114` (`Queue.shutdown()` in `finally` after inner TaskGroup) | `test_consumers_wait_for_slow_producers`, `test_every_job_gets_one_ok_record_with_links`; stress run |
| Fixed pool of N workers on one shared iterator; each job fetched once | met | `pipeline.py:116-134` | `test_each_job_is_fetched_exactly_once` |
| URL validation first, `invalid_url` with no attempt and no network | met | `fetch.py:58-69,135` | `test_invalid_url_sends_no_request` (attempts 0, no requests) |
| Limiter outside the timeout; one `asyncio.timeout` over request, redirects and body; httpx timeout same value | met | `fetch.py:212-234`, `make_client` | `test_host_limiter_wait_does_not_count_against_the_timeout`, `test_attempt_timeout` |
| Request hook checks every hop; `blocked_address` unless all addresses public; `--allow-private` | met (stricter: also blocks multicast) | `fetch.py:273-298` | `test_redirect_to_a_private_address_is_blocked_at_the_hop`, `test_host_resolving_to_a_private_address_is_blocked`, `test_allow_private_skips_the_address_check` (literal only, see Test quality) |
| Non-2xx (incl. 3xx without Location) → `http_status`; media type before body; size cap while streaming; charset or UTF-8 with replacement | partially met | `fetch.py:236-271` | per-kind tests in `test_fetch.py`. Non-text charsets break decoding (finding 6) |
| Exception → kind mapping | partially met | `fetch.py:222-234,182-185,286-289` | `test_transport_errors_are_mapped_and_retried`, `test_unexpected_exception_becomes_an_error_record`. `httpx.InvalidURL` → `invalid_url` not implemented (drift table) |
| Retries: timeout, connect, 5xx, 429 only; up to R further; backoff `rand*min(5,0.5*2**k)` | met | `fetch.py:145-167,187-189,247` | `test_5xx_and_429_are_retried_then_succeed`, `test_retries_exhausted_reports_the_last_failure`, `test_backoff_doubles_and_is_capped`, `test_backoff_is_jittered_by_the_random_source`. `asyncio.timeout` path retry is untested (Test quality) |
| `CancelledError` passes through the fetcher and parse catches | met | `fetch.py:182`, `pipeline.py:150` (`except Exception`) | `test_cancellation_is_not_turned_into_an_error`; stress run |
| HostLimiter reserves slot without an await, then sleeps | met | `ratelimit.py:28-35` | `test_ratelimit.py` (5 tests) |
| `drop-oldest`: evict, put, emit `dropped` with no await between | met | `overflow.py:15-24`, `pipeline.py:124-134` | `test_drop_oldest_*`, `test_many_concurrent_putters_lose_nothing_unaccounted`, `test_drop_oldest_drops_when_the_consumer_falls_behind` |
| `block`: backpressure, drops nothing | met | `overflow.py:27-29` | `test_block_policy_drops_nothing`, `test_block_waits_for_space_and_loses_nothing` |
| Consumer: fetch errors emitted as is; parse in `to_thread`; `parse_error` on raise | met | `pipeline.py:136-158` | `test_fetch_errors_are_written_without_parsing`, `test_a_parser_exception_becomes_a_parse_error_and_the_run_continues` |
| `extract_links` rules | met | `parse.py` | `test_parse.py` (17 tests, incl. malformed and empty page) |
| Interrupt: first cancels workers only; later cancels consumers too; before `run` starts nothing | met in code, **first-stage drain not proven** | `pipeline.py:62-79,105` | `test_interrupt_*`, `test_second_interrupt_stops_the_drain`. A mutant where the first interrupt also cancels consumers passes all 8 (finding 2) |
| SIGINT handler via `call_soon_threadsafe`, restored before return | met (with a window, finding 3) | `pipeline.py:192-213` | `test_ctrl_c_signal_triggers_the_interrupt_and_the_handler_is_restored`, `test_runs_outside_the_main_thread_without_a_signal_handler` |
| Sink: repeated index raises; mark, write, flush; `close` fills `cancelled` or raises | met | `sink.py:40-75` | `test_sink.py` |
| Output failure / bug → not caught, exit 1, no further records | partially met | `pipeline.py:160`, `cli.py:289-294` | `test_an_output_failure_ends_the_run`, `test_output_failure_exits_1`. Further writes do happen after the first failure (finding 4) |
| Record format and null rules | met (one drift on blocked hops) | `models.py:60-133` | `test_models.py` |
| Input: URL vs file, comments/blank lines, dedupe key rules, `--keep-duplicates` | met | `cli.py:116-161` | `test_urls_come_from_arguments_and_files`, `test_equal_urls_share_a_key`, `test_different_urls_have_different_keys`, `test_keep_duplicates_keeps_every_line` (function only, flag not driven) |
| Option bounds | partially met | `cli.py:34-49` | `test_out_of_range_options_are_usage_errors`. `nan`/`inf` accepted (finding 7) |
| Exit codes 0 / 1 / 2 / 130 | partially met | `cli.py:243-301` | `test_cli.py`. A non-UTF-8 input file exits 1 with a traceback, not 2 (finding 1) |
| Summary fields; nearest-rank percentiles | met | `sink.py:15-21,67-75`, `cli.py:199-213` | `test_nearest_rank_percentile`, `test_summary_is_logged_to_stderr`; my exact-fraction check for n = 1..2000, p ∈ {50, 95}: 0 mismatches |
| Logging levels, index on per-URL lines, `--log-json` | met | `cli.py:166-196`, `pipeline.py`, `fetch.py` | `test_json_logs_are_one_object_per_line`, `test_verbose_logs_each_attempt_and_record`, `test_each_drop_is_logged`, `test_retry_is_logged_as_a_warning`, `test_progress_is_logged_while_running` |
| CUJ 1: extract links from a list | partially proven | whole CLI | `test_e2e.py::test_one_bad_url_does_not_affect_the_others` (6 URLs, real HTTP; does not check summary counts sum to N) |
| CUJ 2: consumer falls behind (demo) | met | `scripts/demo_backpressure.py` | run in CI. Exits non-zero unless drop-oldest drops and block drops none; "finishes at fetch speed" is printed, not asserted |
| CUJ 3: interrupted run | partially proven | `pipeline.py`, `cli.py` | `test_ctrl_c_during_the_run_exits_130_with_every_job_recorded` (exit 130, every job recorded). Does not prove queued results get parsed or that the summary is printed |
| Memory NFR (N + queue-max + M bodies) | met by structure | worker holds one result, queue bounded, consumer holds one | not tested (not expected) |

## Findings

### [MAJOR] A non-UTF-8 input file crashes with a traceback and exit 1 instead of exit 2
- **Where:** `src/linkx/cli.py:123` (`open(source, encoding="utf-8-sig")`), `cli.py:260-266` (catches only `OSError`, `KeyboardInterrupt`)
- **Problem:** The design's exit table maps "unreadable input" to 2. A file that is not UTF-8 raises `UnicodeDecodeError`, which is a `ValueError`, not an `OSError`. It escapes `main()` as a raw traceback with exit 1, which is the code for an output failure or internal error. This is likely on the platform the project ships CI for: Windows PowerShell 5.1's `>` redirection writes UTF-16LE with a BOM, so `Get-Content list | ... > urls.txt` produces exactly this file. The same happens for a Latin-1 file.
- **Evidence:**
  ```
  $ uv run linkx u16.txt   # UTF-16LE with BOM
  UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
  u16.txt exit=1
  $ uv run linkx latin1.txt
  UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 15: invalid continuation byte
  latin1.txt exit=1
  ```
- **Confidence:** high
- **Suggested fix:** Catch `UnicodeDecodeError` (or `ValueError`) next to `OSError` at `cli.py:262`, log "cannot read input: not UTF-8" and return `EXIT_USAGE`. Optionally detect a UTF-16 BOM. Add a test with a UTF-16 file.

### [MAJOR] No test proves that the first Ctrl-C still parses and writes queued results
- **Where:** `tests/test_pipeline.py:213-256`, `tests/test_cli.py:257-273`
- **Problem:** CUJ 3's central promise, "queued results are parsed and written", is the difference between `interrupt()` and the `asyncio.run` default the design rejected. No test fails if the first interrupt cancels the consumers too. In `test_interrupt_stops_fetching_and_drains_the_queue`, the 4 good jobs are parsed (4 × 0.01 s) before the interrupt fires at 0.05 s, so the queue is already empty when it lands. The other interrupt tests assert only `len(cancelled) > 1` or `> 0`.
- **Evidence:** I patched `Pipeline.interrupt` through a pytest plugin under the scratch folder, with no repo edit, so that it cancels workers and consumers on the first call. I confirmed the plugin loads (a raising variant fails the tests). Then:
  ```
  $ PYTHONPATH=<scratch> uv run pytest -p conftest_mut tests/test_pipeline.py tests/test_cli.py -k "interrupt or ctrl_c"
  8 passed, 53 deselected   (3 runs out of 3)
  ```
- **Confidence:** high
- **Suggested fix:** Add a test where several results are already queued behind a slow parser when the first interrupt fires. For example, use a `block` queue of 5 filled instantly, a parser that takes 0.05 s each, and an interrupt at 0.01 s. Assert that all queued jobs end `ok` and only the unfetched ones are `cancelled`.

### [MINOR] Ctrl-C after the handler is restored loses the summary, and a repeated press can land mid-write
- **Where:** `src/linkx/pipeline.py:83-96` (`sink.close` runs after the `with self._ctrl_c_calls_interrupt()` block), `cli.py:285-288`
- **Problem:** The handler is restored to `asyncio.Runner`'s before `Sink.close` writes the `cancelled` records, and before `extract` closes the client. A Ctrl-C in that span cancels the main task. `asyncio.run` then raises `KeyboardInterrupt`, and `cli.py` exits 130 without the summary that CUJ 3 requires. This happens even when the run had completed normally. A further press inside the same span makes the Runner's handler raise `KeyboardInterrupt` synchronously. That can happen inside `Sink.emit`, which contradicts the README's "never half-way through writing a record". The span grows with the number of `cancelled` records written to a slow stdout, and an operator pressing "Ctrl-C again to stop now" is likely to press more than twice.
- **Evidence:** A throwaway script ran the first Ctrl-C mid-run and a second one while the first `cancelled` record was written:
  ```
  WARNING interrupted: stopping fetches, finishing queued pages (Ctrl-C again to stop now)
  WARNING interrupted            <- cli.py's KeyboardInterrupt path; no summary line
  exit 130
  ['ok', 'cancelled', 'cancelled']
  ```
- **Confidence:** high (the summary loss is demonstrated); medium (mid-write needs a third or fourth press, reasoned from `asyncio.runners._on_sigint`)
- **Suggested fix:** Call `self._sink.close(...)` inside the `with` block, so further presses only bump `_interrupts`. Consider keeping a no-op SIGINT handler until `extract` returns.

### [MINOR] Records are still written after an output failure
- **Where:** `src/linkx/sink.py:40-53`, `pipeline.py:134,142`
- **Problem:** The design says that on an output failure "the groups cancel the rest, and `cli.py` ... exits 1 without writing further records." The TaskGroup abort is delivered through a callback, so tasks that are already ready in the same loop iteration still run and call `emit`. With a stream whose write fails once (for example `BlockingIOError` on a non-blocking pipe, or a full disk that then frees), later records are written after the failed one, and the failed record's index is marked written but never output. The exit code is still 1, so the impact is small.
- **Evidence:** 200 seeded runs (50 jobs, 8 workers, queue 1, drop-oldest, 1–4 consumers), with a stream that raises `OSError` on write k (1–20) and then works: `max write attempts after the first failure: 7`.
- **Confidence:** high
- **Suggested fix:** In `Sink.emit`, set a `failed` flag when write or flush raises, and refuse (raise) every later `emit`.

### [MINOR] A bad `Location` header is classed `connect` and retried (design finding)
- **Where:** `src/linkx/fetch.py:233-234`; design "Exceptions map to kinds", "Retries"
- **Problem:** httpx turns an unparseable `Location` into `RemoteProtocolError`, which is a `TransportError`. The design maps that to `connect`, and `connect` is retried. A failure caused by the data, which is the same on every attempt, is retried R times with backoff and reported as a connection problem.
- **Evidence:** `Location: http://[::1/broken` → `FetchError(kind=CONNECT, "RemoteProtocolError: Invalid URL in location header: Invalid port: ':1'.") attempts 3 requests 3`.
- **Confidence:** high
- **Suggested fix:** Change the design to map `httpx.RemoteProtocolError` (or "protocol errors" in general) to a non-retried kind, or at least not retry it.

### [MINOR] A server-chosen charset can make decoding fail as `unexpected`
- **Where:** `src/linkx/fetch.py:100-104,262`
- **Problem:** The design says the body is "decoded with the response charset, or UTF-8, replacing undecodable bytes." `_codec_name` accepts any registered codec. Bytes-to-bytes and str-to-str codecs (`base64`, `zlib`, `hex`, `rot13`) and a BOM-less `utf-16` (whose incremental decoder raises even with `errors="replace"`) make the attempt fail as `unexpected` instead of falling back to UTF-8. The failure is isolated to that URL and not retried.
- **Evidence:** `charset=base64` → `UNEXPECTED 'AssertionError: '`; `rot13` → `UNEXPECTED TypeError ...`; `utf-16` (no BOM) → `UNEXPECTED UnicodeDecodeError ... Stream does not start with BOM`. The same happens on 3.13 and 3.15.
- **Confidence:** high
- **Suggested fix:** Accept only text encodings, using `codecs.lookup(name)._is_text_encoding`, or try-decode and fall back to UTF-8 on error.

### [MINOR] `--timeout nan`, `--timeout inf` and `--per-host-delay inf` pass validation
- **Where:** `src/linkx/cli.py:34-49`
- **Problem:** The design requires `--timeout` above 0. `nan` passes, because every comparison with nan is false. A `nan` deadline crashes the event loop itself as soon as a timer has to wait. `inf` disables the per-attempt bound, and `--per-host-delay inf` hangs the second request to any host.
- **Evidence:** `main([... "--timeout","nan"])` → exit 0 with an instant mock. With any real wait, `asyncio.timeout(float('nan'))` around `sleep(2)` raises `ValueError: cannot convert float NaN to integer` from `windows_events._poll`.
- **Confidence:** high (accepted); medium (CLI-level crash shown with asyncio directly, not via a slow server)
- **Suggested fix:** Reject values where `not math.isfinite(value)` in `_bounded`.

### [MINOR] The README says every input URL gets a record, but skipped duplicates get none
- **Where:** `README.md:46`
- **Problem:** "Every input URL gets exactly one record, so the output can be checked against the input". With the default duplicate skipping, repeated URLs have no record. The line also ends in a colon that introduces an unrelated sentence.
- **Evidence:** `test_records_go_to_stdout_by_default` gives 3 inputs, 2 records.
- **Confidence:** high
- **Suggested fix:** Say "every job" and point to the skipped-duplicates count in the summary.

## Limits checked

| Bound (where) | Largest realistic input | Just past it | Loud or silent |
|---|---|---|---|
| `--timeout` per attempt (`fetch.py:219`) covers DNS in hook, all hops, body; httpx per-phase same value | slow-drip body under 10 s per read | `timeout` error, retried | loud (record + WARNING per retry) |
| Per-job worst case | (R+1)·timeout + backoff ≤ 1+2 s (defaults) + limiter waits = ~33 s | n/a | `elapsed_ms` in record |
| `--retries` (`fetch.py:148`) | R=2: 3 attempts; checked `attempts > retries` → exactly R+1 | stops, last failure recorded | loud |
| Backoff cap 5 s (`fetch.py:189`) | k≥4 capped | test `[1,2,4,5,5]` | n/a |
| `max_redirects=9` | 9 followed, 10th 3xx → error (`len(requests) == 10` in test) | `too_many_redirects` | loud |
| `--max-bytes` (`fetch.py:254-268`) decompressed bytes | exactly 5 of 5 accepted; 6 rejected (ran it) | `too_large`, not retried | loud. The Content-Length pre-check compares compressed length with a decompressed cap (drift table) |
| `--queue-max` count | drop-oldest at 1 with 10 workers | eviction → `dropped` record + WARNING | loud |
| Host limiter slots | many jobs on one host: workers queue N·delay deep, by design | n/a | progress line shows in-flight |
| httpx pool `max_connections=concurrency`, pool timeout = `--timeout` | N workers hold ≤ N connections | not reachable | n/a |
| Default thread pool for `to_thread` (min(32, cpu+4)) | M consumers > pool size queue in executor | slower, not lossy | silent but harmless |
| In-memory state: jobs list, dedupe set, `Sink._written`, `_latencies` | O(jobs); millions of URLs = hundreds of MB | memory | silent (no cap; acceptable for the design's scope) |
| Thread join at exit after a second Ctrl-C | a long parse keeps the process alive up to asyncio's 300 s executor-join timeout | n/a | silent wait |

## Not verified

- **Real terminal Ctrl-C** while the loop is blocked in `select`/IOCP on Windows and Linux. Tests use `signal.raise_signal`, which runs the handler synchronously. Wake-up relies on PEP 475 EINTR handling on Unix and the proactor's `set_wakeup_fd` on Windows. Pressing Ctrl-C during a slow real fetch on both OSes would settle it.
- **Cancellation inside httpcore/anyio shielded cleanup** (`AsyncShieldCancellation`) never swallowing a worker cancel. The stress run used `MockTransport`, which bypasses httpcore. A stress run against a local slow server would show it.
- **A main-task cancel landing inside `Pipeline.run`**, from a Ctrl-C during `make_client` before the handler is installed, would skip `Sink.close`: partial records, no `cancelled`, exit 130. The window is microseconds; this is reasoned, not run.
- **CI's `setup-uv` `python-version` overriding `.python-version` (3.15)**, so the 3.13 and 3.14 jobs really test those versions. The author's `checks.txt` shows 3.13 and 3.14 passing locally via `--isolated`.
- **`httpx.InvalidURL` being unreachable during send.** httpx turns a bad `Location` into `RemoteProtocolError`, and the start URL is pre-validated. I did not find another path.

## Known classes checked

`classes.md` records none (first code review in this repository).

## Open follow-ups touched

`followups.md` records none.

## Test quality notes

- The interrupt tests do not prove the first-stage drain (finding 2). `test_second_interrupt_stops_the_drain` does fail if the second interrupt leaves consumers running: 5 × 0.1 s parses would leave only job 5 cancelled.
- The `asyncio.timeout` → `timeout` path is reached only by `test_attempt_timeout` with `retries=0`, so nothing proves that this path is retried (`fetch.py:222-225`, `retryable=True`). The `ReadTimeout` parametrization covers only the httpx path.
- "A redirect to a private address ... allowed with the flag" (plan step 7) is tested only with a direct `127.0.0.1` URL, not through a redirect.
- CLI flag wiring to `Settings` (`cli.py:249-259`) is not driven for `--keep-duplicates`, `--consumers`, `--queue-max`, `--queue-policy`, `--max-bytes` or `--per-host-delay`. Swapping two of those assignments would pass the suite. 100% line coverage hides this, because the lines run regardless.
- CUJ 1's "summary counts sum to N" and CUJ 3's "the summary is printed" are not asserted.
- Stand-ins: `ScriptedFetcher` returns instantly or hangs; `MockTransport` bypasses httpcore, so connection-pool and cancellation behaviour of the real transport is covered only by the two `test_e2e.py` tests (which do use a real `http.server`, good).
- Good: the fetcher tests inject clock, sleep, rng and resolve, and assert attempts and sleeps exactly. The sink audit and the duplicate-emit check are tested directly.

## Plan drift

- All planned files exist. Unplanned: `.gitignore` (trivial), `src/linkx/__main__.py` (part of the "package skeleton"), and the two-line design edit for the `Sink(stream, jobs)` and `Fetcher(..., clock)` signatures, which is recorded in `spec.md`.
- Verification Plan step "`uv sync`, then `uv run linkx --help`" is not in CI. `test_module_entry_point` runs `python -m linkx`, not the console script. I ran `uv run linkx --help` locally: exit 0.
- `[tool.coverage.run] source = ["linkx"]` instead of `src/linkx`, and `uv run mypy` (configured files) instead of `mypy src`: equivalent or wider.

## Design drift

| Design says | Code does | Where | Which should change |
|---|---|---|---|
| `httpx.InvalidURL` → `invalid_url` | not caught in `_attempt`; would become `unexpected` (appears unreachable) | `fetch.py:222-234` | code (one `except`), or drop from the design |
| Size counted as the body streams | also rejects early on `Content-Length > max-bytes`. Under gzip that compares compressed length with a decompressed cap (can reject a body whose decoded size is ≤ cap only for incompressible data) | `fetch.py:255-257` | design (document the pre-check) |
| `blocked_address` when an address is not `is_global` | also blocks multicast, and unwraps IPv4-mapped IPv6 | `fetch.py:72-77` | design (record it) |
| `final_url`/`http_status` null "when no response arrived" | a hop blocked by the hook reports both null although a 3xx arrived; likewise a timeout during the body | `fetch.py:273-298,222-225` | design (say "null for failures raised before or between responses") |
| Second Ctrl-C stops parsing "once any running parse ends" | the running parse's job is recorded `cancelled` at once; its thread runs on and the process waits for it at loop shutdown | `pipeline.py:149` | design (state which record the running parse gets) |
| SIGINT handler restored "before it returns" | restored before `Sink.close` runs, opening the window in finding 3 | `pipeline.py:83-93` | code |
| Unknown-index `emit` not mentioned | raises `RuntimeError` | `sink.py:42-43` | none (a sensible addition) |
| Dedupe: "nothing else is normalised" | `?` with an empty query and surrounding whitespace are also normalised away | `cli.py:131-147` | trivial; either |

Housekeeping note: one of my probes ran `uv run --python 3.13` without `--isolated`. That recreated the repository's git-ignored `.venv` on 3.13, and the next 3.15 command recreated it on 3.15. The full suite then passed again (`187 passed`) and `git status` is clean. No tracked file was touched.
