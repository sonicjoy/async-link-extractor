# Goldfish Review Report

**Target:** `docs\designs\linkx.md` (`227-255`, Detailed Implementation Plan; lines 1-226 read as the specification)

**Stage:** 9, Implementation Readiness Test

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-implementation-readiness` | completed |
| `egm-codebase-details` | completed |
| `egm-style` | completed |

No reviewer needed a retry. One instance of each reviewer was run.

**Severity breakdown after deduplication:** 1 Blocker, 13 Major, 11 Nit.

**Verdict of both readiness reviewers:** not ready. An engineer would have to make behaviour, interface and dependency decisions while coding.

**How claims were checked.** `egm-codebase-details` read the CPython 3.13.18 `asyncio` source installed locally (`runners.py`, `taskgroups.py`, `queues.py`, `proactor_events.py`). Claims marked *verified* rest on that source or on a contradiction inside the document. It did not read the 3.14 or 3.15 sources and found no httpx, pytest-asyncio, pytest-cov or uv source, so every claim about those is *unverified (from memory)* and is marked so below.

**asyncio claims in lines 1-226 that hold (verified):** `QueueShutDown` is raised by `get()` only when the queue is shut down and empty; the first Ctrl-C cancels the main task and the second raises `KeyboardInterrupt`; the pseudocode at 125-136 drains the consumers and returns `True` when Ctrl-C arrives while workers run; `get_nowait()` then `put_nowait()` has no suspension point; the Windows proactor loop wakes on Ctrl-C.

**Severity changes made in synthesis.** The non-public address finding was rated Major by `egm-style` and Blocker by the other two; it is kept as Blocker. Two `egm-style` Majors (coverage wording, README contents) are listed as Nits because they do not change behaviour, an interface or a dependency. The "no positional arguments" case was rated Nit by one reviewer and Major by another; it is kept as Major because it is a behaviour decision.

**Word budget.** Most recommendations are one clause. Several belong in existing sentences at lines 89, 96, 98, 141 and 170-173 and not in the plan list. Resolving B1 allows step 11 to be deleted or cut to a README note, which pays for much of the rest.

## Prioritized Findings (Blockers -> Major -> Nits)

### B1. The non-public address policy is undecided and step 11 leaves its design to the implementer - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** `243` (with `44`, `165`, `170-173`, `222`, `241-242`)
* **Discrepancy Description**: Line 44 says the choice "is confirmed in the security step" and step 11 says it "confirms" the policy, so neither decides it. The implementer must choose:
  * the default (refuse or allow);
  * the interface: `--allow-private` is not in the synopsis at 170-173, although `blocked_address` is already an error kind at 165;
  * the mechanism: what counts as non-public (literal IPs only, or resolved hostnames), and how each redirect hop is checked when httpx follows redirects itself (line 96). Options are a `request` event hook, a wrapping transport, or a manual redirect loop. A separate DNS lookup does not bind the address httpx connects to, so DNS rebinding stays open unless accepted in writing;
  * the test seam: a resolving check would do real DNS for the fake hostnames in the step 7 `MockTransport` tests.

  Order problem: the step 9 end-to-end test fetches from a loopback `http.server`. If refusal is the default, that test fails after step 11, and so does the demo if it uses a local server. Step 11 lists only `fetch.py`, `test_fetch.py` and `README.md`; it omits `cli.py`, `tests/test_cli.py`, `tests/test_e2e.py` and possibly the demo script.
* **Rationale & Recommendation**: Close the Open Question in the document. For example: "Refused by default; `--allow-private` permits. Checked for every hop with an injectable resolver; all resolved addresses must be public; rebinding between check and connect is a documented limit." Add the flag to the synopsis, move the check and its tests into step 7 and the flag into step 9, state that the e2e test and demo pass `--allow-private`, and delete step 11 or cut it to the README note. Unverified (from memory): httpx 0.28 runs `event_hooks["request"]` once per redirect hop and has no public resolver hook.

### M1. `pipeline.run` cannot receive the parser or the HTTP client that step 8 needs - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** `240` against `89` (also `83-84`, `242`, `251`)
* **Discrepancy Description**: Line 240 says "The slow parser is passed as an argument", but line 89 gives `run(jobs, settings, sink)`. Step 8 also needs "a raising parser" and controlled fetches, and nothing says who creates the shared `httpx.AsyncClient`. One implementer adds a `run` parameter, another a `settings` field, another monkeypatches. `Settings` is used in two signatures but is not in the `models.py` list at line 83. Verified: internal contradiction.
* **Rationale & Recommendation**: Change line 89 to something like `run(jobs, settings, sink, *, client, parse=extract_links) -> bool`; state that the CLI creates the client and that tests and the demo pass a `MockTransport` client; add `Settings` to `models.py`.

### M2. CI fails from step 2 until step 9 - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** `229`, `233-234`, `247-251`
* **Discrepancy Description**: Step 2 runs the whole Verification Plan. Until step 9 the script target `linkx.cli:main` does not exist, so `uv run linkx --help` fails and `__main__.py` has nothing to import. `pytest` with no tests exits non-zero (code 5, from memory). The demo does not exist until step 10. An untested `__main__.py` can also push a small package below 95% in early steps. This conflicts with "merged to `main` by pull request" and with the standard's requirement that each phase is buildable and testable.
* **Rationale & Recommendation**: Have step 1 also create a minimal `cli.main` (handles `--help`) and one smoke test, making step 9 a Modify; state that the demo command joins CI at step 10 (see M10) and whether `__main__.py` is excluded from coverage.

### M3. The scaffold leaves dependency and tool configuration choices open - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** `233-234`, `248-250`
* **Discrepancy Description**: "dev tools" is not a list. Open choices that change results or dependencies:
  * the async test runner: pytest-asyncio is not named anywhere in the design, and its 1.x default is strict mode, so async tests need a marker or `asyncio_mode = "auto"` (unverified, from memory);
  * the build backend: uv installs `[project.scripts]` only for a packaged project, so without a `[build-system]` table `uv run linkx --help` may fail (unverified, from memory);
  * the coverage source: bare `--cov` has no target, so test files can count toward the 95%;
  * the httpx version bound;
  * the CI runner operating system, and how CI selects the interpreter: a `.python-version` file can override the matrix version unless uv is told otherwise (unverified, from memory);
  * the version in `.python-version` (3.13 minimum or 3.15 "built and shipped");
  * the licence in `LICENSE`.
* **Rationale & Recommendation**: One line in step 1 and one in step 2. For example: "`httpx>=0.28,<1`; dev group: pytest, pytest-asyncio>=1 (`asyncio_mode = "auto"`), pytest-cov (`--cov=linkx`), ruff, mypy; backend hatchling; MIT; `.python-version` 3.15" and "CI: ubuntu-latest (plus windows-latest if wanted), `setup-uv` with `python-version` from the matrix, `uv sync --locked`."

### M4. The fetch outcome mapping is incomplete - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** `239`, `243` against `96-98`, `113`, `159-160`, `165`
* **Discrepancy Description**: The step 7 tests cannot be written without deciding:
  * **Client timeout.** httpx applies a default 5-second timeout per phase, so with `--timeout 10` a slow connect fails at 5 seconds unless the client is built with `timeout=None` or a derived value (unverified, from memory).
  * **Exception mapping.** Where `ReadError`, `RemoteProtocolError`, `DecodingError`, `UnsupportedProtocol` (redirect to a non-http scheme) and `TooManyRedirects` land, and which are retried. No error kind fits the redirect limit. httpx's default `max_redirects` is 20, so 10 must be set explicitly (unverified, from memory).
  * **Content type.** Whether `application/xhtml+xml` or a missing header passes.
  * **Decoding.** A manually streamed body has no `response.text`; charset and error handling must be chosen. The brief lists decode failure as open.
  * **Size cap.** Whether `--max-bytes` counts wire bytes or decoded bytes. Counting wire bytes lets a compressed body exceed the memory bound at line 221. "re-checks the size cap" in step 11 names no test.
  * **Missing tests.** Step 7 has no `connect` case and no redirect-limit case, although line 31 promises a test per failure path. "429" and "redirect" state no expectation.
  * **Test note.** A `MockTransport` response built with `content=<bytes>` arrives as one chunk; the oversize test exercises mid-stream abandonment only if the handler supplies an async byte iterator (unverified, from memory).
* **Rationale & Recommendation**: Add about two lines near 96-98. For example: "The client is created with `timeout=None` and `max_redirects=10`. `httpx.TimeoutException` and `TimeoutError` -> `timeout` (retried); other `httpx.TransportError` -> `connect` (retried); `TooManyRedirects` -> `http_status` or a new kind (not retried); `InvalidURL`, `UnsupportedProtocol` -> `invalid_url`. HTML means `text/html` or `application/xhtml+xml`; a missing header is `not_html`. Decode with the header charset, else UTF-8, `errors="replace"`. The cap counts decoded bytes." In step 7 write "429 retried, redirect followed, redirect limit, connection error", and replace "re-checks the size cap" with a named test or drop it.

### M5. Interrupt and exit-code tests have no stated mechanism, and two interrupt paths have no test - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** `240-241` against `64`, `138-144`
* **Discrepancy Description**: "A simulated interrupt" is undefined in step 8. The exit 130 and exit 1 cases in step 9 could use a real signal to a subprocess or a patched `pipeline.run`; a subprocess SIGINT test is not portable to Windows (unverified, from memory). No test covers the second Ctrl-C branch at line 140, or a worker cancelled mid-fetch producing a `cancelled` record.
* **Rationale & Recommendation**: State "interrupt = `task.cancel()` on the task running `run` while workers are in flight; CLI tests for 1 and 130 inject a failing sink and a `pipeline.run` stub that returns true or raises `KeyboardInterrupt`; no real signals". Add the two missing cases to steps 8 and 9.

### M6. A Ctrl-C after the workers finish does not drain the queue, contrary to CUJ 3 - Severity: Major
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** `240` against `64`, `125-136`, `144`
* **Discrepancy Description**: The `except CancelledError` guards only the inner group. If the first Ctrl-C arrives while `run` waits in the outer group's exit (fetches done, consumers still working through queued results), the outer `TaskGroup` cancels the consumers and `CancelledError` leaves `run`. Queued results get `cancelled` records and are not "parsed and written". Exit status is still 130. Verified against `taskgroups.py` and `runners.py` in CPython 3.13.18.
* **Rationale & Recommendation**: Either state the exception at line 64 ("a Ctrl-C after fetching has finished stops the drain") or protect the drain as well. The step 8 test then covers whichever is chosen.

### M7. Behaviour on the failure path (exit 1) is not defined - Severity: Major
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** `241` against `120`, `138-141`, `146`
* **Discrepancy Description**: The `cli.main` sketch has no handler for the `ExceptionGroup` a `TaskGroup` raises, so "reports it and exits 1" has no stated shape. After an output `OSError`, the `finally` calls `sink.close()`, which writes `cancelled` records to the same broken output and raises again. Whether `close()` writes, skips, or still prints the summary there is a behaviour decision. It is also unstated that `main` returns the exit code. First two points verified from the document and `taskgroups.py`.
* **Rationale & Recommendation**: Add: "`main` returns the exit code. Any exception other than `KeyboardInterrupt` -> message on stderr, exit 1; after an output failure `close()` writes nothing and only reports."

### M8. A second Ctrl-C can land inside `Sink.emit` and break the one-record invariant - Severity: Major
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** `238` against `64`, `144-146`
* **Discrepancy Description**: On the second Ctrl-C the handler raises `KeyboardInterrupt` in whatever main-thread code is running, which can be between writing a line and recording its index. `close()` then writes a second record for that index or fails its count check, giving a duplicate line or exit 1 instead of 130. This is rare, but it contradicts "exactly one record, on every exit path". The synchronous raise is verified (`runners.py`); the consequence is by reasoning.
* **Rationale & Recommendation**: Specify the order in `emit` (record the index, then write and flush) and say what `close()` tolerates on the `KeyboardInterrupt` path, or scope the invariant to exclude a forced stop.

### M9. Retry backoff and limiter timing are not injectable in the stated interfaces - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`, `egm-style`
* **Affected Scope:** `239`, `241` against `84-85`, `98`, `100`
* **Discrepancy Description**: `fetch(client, job, settings, limiter)` has no sleep or random source, so the "5xx then success" and "retries exhausted" tests, and the e2e "dead URL" test, either sleep for real or each implementer invents a seam. The `HostLimiter` constructor that takes the "injected clock" is not given, and the limiter also needs a sleep. "Retry k" does not say whether k starts at 0 or 1, which halves or doubles the waits. Whether `Retry-After` on a 429 is ignored is unstated.
* **Rationale & Recommendation**: State `HostLimiter(delay, *, clock, sleep)`; say fetch takes the same `sleep` and an `rng` (or that tests patch them); say "k = 1 for the first retry; `Retry-After` is ignored."

### M10. Whether CI runs the demo, what the demo fetches, and whether it checks its own result - Severity: Major
* **Reviewer Lens:** `egm-style`, `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** `234`, `242`, `251` (with `32`)
* **Discrepancy Description**: "running the Verification Plan" includes the demo under one reading and only lint and tests under another. "showing drops" does not say whether the script asserts and exits non-zero or a person reads the output. The plan does not say what the demo fetches; line 32 requires it to be reproducible, which a run against public sites would not be.
* **Rationale & Recommendation**: Line 251: "...`scripts/demo_backpressure.py`, which uses an in-process `MockTransport` with a fixed delay and exits non-zero unless `drop-oldest` drops some and `block` none." If CI should skip it, change line 234 to "running the build, lint and test commands below".

### M11. Overflow test cases can be read two ways - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `237` against `106`, `108`
* **Discrepancy Description**: "unknown policy name rejected" does not say where: a lookup in `overflow.py` that raises, or an argparse choice in `cli.py` that exits 2. The case is absent from the CLI cases. "`drop-oldest` returns the oldest" drops the condition from line 106, where the policy returns `None` when the queue is not full. "putters" is not a term used in lines 1-226.
* **Rationale & Recommendation**: "`drop-oldest` returns the evicted entry when full, else `None`; after many concurrent `put` calls every entry is either queued or was returned as evicted; the policy lookup raises on an unknown name" (or name the CLI as the place, with exit 2).

### M12. Parser cases with no stated expectation - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `236` against `115`
* **Discrepancy Description**: Under line 115 (fragments removed, `http`/`https` kept), `href="#top"` resolves to the page's own URL and would be kept. "fragment-only links" could equally be read as dropped. "skipped schemes" reads as schemes the tests skip.
* **Rationale & Recommendation**: "non-http(s) schemes dropped; a fragment-only href yields the page URL" (or "is dropped", whichever is intended).

### M13. CLI input edge cases are unspecified - Severity: Major
* **Reviewer Lens:** `egm-codebase-details`, `egm-implementation-readiness`
* **Affected Scope:** `241` against `170-183`
* **Discrepancy Description**: `[URL_OR_FILE ...]` is optional; with no arguments the behaviour could be stdin, a usage error, or zero records with exit 0. Only `--queue-max` has a stated numeric bound. An unwritable `--out` path could be exit 1 or exit 2. The exit 1 test has no stated trigger.
* **Rationale & Recommendation**: One line: "no inputs -> exit 2; `--concurrency` and `--consumers` >= 1, `--retries` >= 0, `--timeout` > 0, else exit 2; unwritable `--out` -> exit 1 (this is the exit 1 test)."

### N1. Coverage claim is stronger than the command enforces - Severity: Nit
* **Reviewer Lens:** `egm-style`, `egm-codebase-details`
* **Affected Scope:** `248`, `250`
* **Discrepancy Description**: `--cov --cov-branch --cov-fail-under=95` checks one combined figure, not 95% each for line and branch (unverified, from memory). "the command above" is two bullets up.
* **Rationale & Recommendation**: "95% combined line and branch coverage, enforced by the Unit Tests command."

### N2. README contents not traceable to the specification - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `242`
* **Discrepancy Description**: "measured numbers" does not say which numbers. "the mapping to a production pipeline" refers to something lines 1-226 never define.
* **Rationale & Recommendation**: "the demo's run times and drop counts, what is not built, and how the queue would map to a message broker."

### N3. `--verbose` and the summary format are undefined - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** `241` against `173`, `176`, `187`
* **Discrepancy Description**: Nothing says what is logged at each level or whether the summary is text or JSON. Step 9 lists no test for either.
* **Rationale & Recommendation**: One line, such as "`--verbose` logs one line per retry and drop; the summary is `key=value` lines", and one CLI test.

### N4. `Sink` constructor, file ownership and output encoding are unstated - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** `238` against `88`, `146`, `150`, `176`
* **Discrepancy Description**: `close()` needs every index and URL, so `Sink` must receive the full job list. Nothing says who opens `--out`. On Windows, default text mode breaks the `\n` and UTF-8 rule for both file and stdout (stdout point unverified, from memory). `ensure_ascii` is unstated.
* **Rationale & Recommendation**: "`Sink(stream, jobs)`; the CLI opens `--out` with `encoding="utf-8", newline="\n"` and reconfigures stdout the same way", plus an LF/UTF-8 test.

### N5. Limiter key and `elapsed_ms` behaviour have no test - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-style`
* **Affected Scope:** `239` against `85`, `100`
* **Discrepancy Description**: The limiter key (hostname, or host and port) and whether redirect hops are limited are undefined. The wait counting toward `elapsed_ms` is untested. The step says "Rate limiter" and "Limiter" where the specification says `HostLimiter`; line 39 uses "rate limiting" for a non-goal.
* **Rationale & Recommendation**: "**Host limiter and fetch**"; add "keyed by hostname of the request URL, first hop only" and one test case.

### N6. Tool strictness is not stated - Severity: Nit
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope:** `249`
* **Discrepancy Description**: mypy strictness and the ruff rule set are not stated.
* **Rationale & Recommendation**: "mypy `strict = true`; ruff default rules."

### N7. Process wording that cannot be observed or measured - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `229`
* **Discrepancy Description**: "shown failing first" names no observer or record. "small steps" cannot be measured.
* **Rationale & Recommendation**: "Each test is committed failing before the code that passes it. Work is merged to `main` by pull request from a feature branch."

### N8. "the `null` rules" has no named referent - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `235`
* **Discrepancy Description**: The phrase points at nothing by name.
* **Rationale & Recommendation**: "including the `null` fields in Record and outcomes."

### N9. Sink and pipeline cases worded more loosely than the specification - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `238`, `240` against `120`, `146`
* **Discrepancy Description**: "counts" does not say of what; "double emit" omits "for one index"; "checks the total" omits that it raises; "several consumers" states no outcome.
* **Rationale & Recommendation**: "counts per outcome; a second emit for an index raises; `close()` emits `cancelled` for outstanding indexes and raises on a count mismatch"; "several consumers still give one record per input".

### N10. "dead URL" maps to two error kinds - Severity: Nit
* **Reviewer Lens:** `egm-style`, `egm-codebase-details`
* **Affected Scope:** `241` (with `50`)
* **Discrepancy Description**: A closed port gives `connect`; a 404 gives `http_status`. A refused loopback connect on Windows may also take about 2 seconds per attempt (unverified, from memory).
* **Rationale & Recommendation**: "one URL on a closed port among good ones" (or "one 404"), and pass `--retries 0` if a closed port is used.

### N11. "Unit Tests" label covers the whole suite - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** `248`, `251`
* **Discrepancy Description**: The command also runs `tests/test_e2e.py`, which line 251 runs again. A reader may think the 95% must come from unit tests alone.
* **Rationale & Recommendation**: Label the bullet "**Tests**" and reduce line 251 to the demo command.
