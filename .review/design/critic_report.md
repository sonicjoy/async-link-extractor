# Goldfish Review Report

**Target:** `C:\Users\sonic\source\repos\async-link-extractor\docs\designs\linkx.md` (lines 1-226)

Stage 7, Critic Review. No reviewer could run code. Every statement about asyncio or httpx behaviour below is from memory and marked "unverified".

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-design` | completed |
| `egm-structural` | completed |
| `egm-style` | completed |

One instance of each reviewer ran. None needed a retry.

## Severity Breakdown

| Severity | Count |
| --- | --- |
| Blocker | 0 |
| Major | 6 |
| Nit | 17 |

The counts are after deduplication. Reviewers rated four items Major that are listed here as Nits (N1, N2, N3, N4); each entry says so and why.

## Focus areas checked and found sound

These were checked by `egm-design` and found correct as specified (all unverified):

- **Eviction rule (lines 104-109).** `get_nowait()` then `put_nowait()` has no suspension point, so no task runs between them and an evicted result cannot also reach a consumer.
- **Completion through `Queue.shutdown()` (lines 113, 122-136).** It is called once, after the worker group exits, on the normal and error paths. Consumers drain and then get `QueueShutDown`.
- **`uncancel()` on a first Ctrl-C while workers are running (lines 131-133, 144).** `run` absorbs the one cancellation and returns `True`.
- **Consumer failure through the same `except CancelledError`.** The outer group still raises, so the exit code is 1 and not 130.
- **Other cancellation points.** A worker cancelled mid-fetch, a worker cancelled in a `block` put, and a consumer cancelled between `to_thread` and `emit` each end with exactly one `cancelled` record.

The paths where "exactly one record per input" does not hold are M1 (two or zero records) and M2 (the exit-1 path).

## Prioritized Findings (Blockers -> Major -> Nits)

### M1. A second Ctrl-C can land inside `Sink.emit` and give an input two records or none - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`, `egm-style`
* **Affected Scope:** lines 28, 64, 144, 146
* **Discrepancy Description**: On the second SIGINT, `asyncio.run`'s handler raises `KeyboardInterrupt` in the main thread between any two bytecodes, not at an `await` (unverified). The "no `await`" argument does not cover it. Path to two records: a consumer is inside `emit` for index 7, the line is written, the interrupt arrives before the sink marks index 7, and `close()` then writes a `cancelled` line for index 7. The count check passes because it uses the sink's own bookkeeping. If the sink marks before writing, the same interrupt leaves index 7 with no line. Also, once `asyncio.run` has returned the default SIGINT handler is back (unverified), so a Ctrl-C during `close()` aborts the `cancelled` fill and the summary.
* **Rationale & Recommendation**: This is the one path found where an input gets zero or two records on an exit the document claims to cover. Either make the second interrupt cooperative (the tool's own handler cancels the consumers through `loop.call_soon_threadsafe`, and SIGINT is ignored during `close()`), or state the ordering inside `emit` and reduce the second-Ctrl-C guarantee on line 64 to best effort.

### M2. "On every exit path" is contradicted by the exit-1 path, and `close()` in the `finally` is unspecified there - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`, `egm-style`
* **Affected Scope:** lines 28, 120, 138-142, 146, 178-183
* **Discrepancy Description**: Line 28 promises one record per input on every exit path and line 146 says `close()` runs on every path. After an output `OSError` (line 120), `close()` tries to write `cancelled` records to the failed output and raises again inside the `finally`, replacing or chaining over the original error; no summary is returned. After a double emit, `close()` fills and may raise its own count error. The `cli.main` sketch has no handler that produces "reports it and exits 1".
* **Rationale & Recommendation**: The guarantee cannot hold when the output is what failed. Scope line 28 to exits 0 and 130. Add one clause at line 146: after a failed emit, `close()` writes no further records and the original error is the one reported.

### M3. A first Ctrl-C after fetching has finished does not drain the queue - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-structural`, `egm-style`
* **Affected Scope:** lines 30, 64, 124-136, 144
* **Discrepancy Description**: The `try/except CancelledError` wraps only the worker group. Once the workers finish and `shutdown()` runs, the main task waits in the outer group's exit. A first Ctrl-C there is not absorbed: the outer group cancels the consumers, `asyncio.run` raises `KeyboardInterrupt` (unverified), and every queued result becomes `cancelled`. One record per input still holds, but lines 30 and 64 say queued results are parsed and written. The window is widest with a slow consumer, which is CUJ 2. Separately, line 64's "the records, summary and status are the same" is wrong: after a second Ctrl-C the queued results are `cancelled` instead of `ok` or `parse_error`, so the records and counts differ; only the record count and the status match.
* **Rationale & Recommendation**: The cheapest fix is to state it. Replace the last sentence of line 64 with: "A second Ctrl-C, or a first one after fetching has finished, stops parsing: queued results get `cancelled` records. Every input still has one record and the status is 130." Add the same exception to line 30. The alternative is to restructure so the consumer wait also absorbs one cancellation.

### M4. The close-time audit cannot detect a lost result on a normal run - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-style`
* **Affected Scope:** lines 28, 88, 146, 163
* **Discrepancy Description**: `close()` first writes `cancelled` for every index with no record, then checks the count. After the fill the count can differ only through a double emit, which line 120 says already raises at `emit`. If a bug loses a result on an uninterrupted run, that input is labelled `cancelled`, the check passes and the exit code is 0. "The tool checks this" (line 28) checks almost nothing. Line 88's "closes the audit" is undefined.
* **Rationale & Recommendation**: Pass `interrupted` to `close()`. Fill with `cancelled` only when interrupted; otherwise treat a missing index as an internal error (exit 1). About one sentence changed on line 146.

### M5. The memory bound on line 221 is not supported by the fetch description - Severity: Major
* **Reviewer Lens:** `egm-design`, `egm-style`
* **Affected Scope:** lines 37, 96, 221
* **Discrepancy Description**: Line 96 does not say whether `--max-bytes` counts raw or decompressed bytes. If raw, a small gzip body can expand without limit; if decoded, one decompressed chunk can overshoot before the check (unverified). `extract_links(html, ...)` implies each queued result holds a decoded `str`, which CPython stores at 1, 2 or 4 bytes per character, so "each at most `max-bytes`" can be off by up to 4x. Parser state and link lists are not counted.
* **Rationale & Recommendation**: Say the cap counts decompressed bytes. Then either queue `bytes` and decode in the parse thread, or state the bound as body bytes received with decoded text and parser state extra.

### M6. The open question on non-public addresses changes the fetch design and is already assumed in the body - Severity: Major
* **Reviewer Lens:** `egm-design` (Major), `egm-structural` (Nit), `egm-style` (Nit)
* **Affected Scope:** lines 26, 44, 96, 100, 165, 222
* **Discrepancy Description**: Line 96 lets httpx follow redirects itself. The proposed default on line 44 needs a check on every hop before the request is sent, which means manual redirect handling or a request hook, plus host resolution with a gap before httpx's own resolution (unverified). `blocked_address` is already an error kind on line 165 while the question is open. The host limiter is keyed on the input host (line 100), so redirect hops bypass the "requests to the same host" goal on line 26; lines 85 and 100 say "attempts" where line 26 says "requests".
* **Rationale & Recommendation**: Decide the default now instead of in the security step, because it decides how redirects are followed. State in one sentence how hops are intercepted and that the limiter applies to the input URL's host only. Use "attempts" on line 26.

### N1. The `except CancelledError` branch is described as the Ctrl-C path only; `interrupted` is never initialised - Severity: Nit
* **Reviewer Lens:** `egm-style` (Major), `egm-structural` (Nit)
* **Affected Scope:** lines 89, 120, 125-136, 144
* **Discrepancy Description**: A consumer failure makes the outer group cancel the main task, so the same branch runs and sets `interrupted = True`. The outer group still raises, so the outcome is correct (unverified), but an implementer could log "interrupted" on an exit-1 path. The sketch never sets `interrupted` on the normal path. Line 144 credits `uncancel()` with what the `except` does, and "lose the queued results" should be "not parse".
* **Rationale & Recommendation**: Listed as a Nit because the behaviour is correct and only the explanation is incomplete. Add `interrupted = False` before the `try` and one sentence: the same branch runs when a consumer fails, and the outer group then re-raises that failure.

### N2. The eviction rule leaves three points to inference - Severity: Nit
* **Reviewer Lens:** `egm-style` (Major)
* **Affected Scope:** lines 106-108
* **Discrepancy Description**: The not-full case and its `None` return are unstated; it is unclear whether the fullness test is one of "these steps"; and nothing says the worker must emit the `dropped` record before its next `await`. Line 106 calls the policy "one async function" while the `drop-oldest` body must not suspend.
* **Rationale & Recommendation**: Listed as a Nit because `egm-design` found the rule sound and a cancellation between return and emit still yields one record (`cancelled` in place of `dropped`). Suggested wording: "`put_nowait()` the result. On `QueueFull`, `get_nowait()` the oldest, `put_nowait()` the new one and return the evicted one; otherwise return `None`. The function contains no `await`, and the worker emits the `dropped` record before its next `await`."

### N3. Three names for the queue element; "result" and "record" swapped - Severity: Nit
* **Reviewer Lens:** `egm-style` (Major)
* **Affected Scope:** lines 11, 19, 39, 64, 104-109, 113, 119, 178
* **Discrepancy Description**: The queue element is "result", "entry" and "item"; the removal is "trim", "drop" and "evict", with 24 words on line 19 declaring them synonyms. Line 119 says `fetch` converts an exception "into a record" although it returns a `FetchResult`. Line 113 "emitted as it is" has the same slip. "Exit status" (64) and "exit code" (89, 178) are both used.
* **Rationale & Recommendation**: Listed as a Nit because no reading changes the behaviour. Use "result" for the queue element, "evict" for the action and `dropped` for the outcome, then cut the last sentence of line 19 (saves 24 words). Line 113: "is emitted as a `fetch_error` record without parsing".

### N4. No Risks and Mitigations section; 3.15 dependency support is unconfirmed - Severity: Nit
* **Reviewer Lens:** `egm-design` (Major), `egm-structural` (Nit), `egm-style` (Nit)
* **Affected Scope:** lines 11, 31, 219-223
* **Discrepancy Description**: The standard lists Risks and Mitigations; the content is spread over Failure handling, Non-Functional Requirements and Open Questions. Lines 11 and 31 commit to building, shipping and CI on 3.15, while the brief records that httpx and pytest-asyncio support for 3.15 was not verified. "Shipped" has no referent, since the Dockerfile is a non-goal.
* **Rationale & Recommendation**: Listed as a Nit because the address risk is covered by M6 and the remaining fix is a rename plus one line. Rename the section to "Risks and Limits" and add a mitigation such as "fall back to 3.14 if a dependency lacks 3.15 wheels". Line 11: "requires Python 3.13 or newer and is developed on 3.15".

### N5. `elapsed_ms` is undefined and "fetch latency" does not describe it - Severity: Nit
* **Reviewer Lens:** `egm-style`, `egm-design`, `egm-structural`
* **Affected Scope:** lines 29, 100, 165, 187
* **Discrepancy Description**: Line 100 counts the limiter wait toward `elapsed_ms`; nothing says whether retry backoff counts or where the clock starts and stops. With `--per-host-delay` set, p95 mostly measures waiting behind the limiter. The summary's maximum is not a percentile.
* **Rationale & Recommendation**: Define it once on line 165 ("from the worker taking the job to the last attempt ending, including limiter waits and backoff"), delete the clause on line 100, and call it "per-URL elapsed time" on lines 29 and 187.

### N6. Only `--queue-max` has a stated lower bound - Severity: Nit
* **Reviewer Lens:** `egm-design`, `egm-style`
* **Affected Scope:** lines 104, 170-173
* **Discrepancy Description**: `--consumers 0` with `block` hangs; with `drop-oldest` it exits 0 with every input `dropped` or `cancelled`. `--concurrency 0` exits 0 with every input `cancelled`. `--timeout` and `--retries` have no stated rule. Line 104 states a property, not what the CLI does.
* **Rationale & Recommendation**: Change line 104 to say the CLI rejects values below 1 for all three (exit 2), and state the rule for `--timeout` and `--retries`.

### N7. Exit code for an audit mismatch is not stated - Severity: Nit
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** lines 138-142, 146, 178-183
* **Discrepancy Description**: Line 146 says `close()` raises on a mismatch; the exit table does not map it.
* **Rationale & Recommendation**: Add "or audit mismatch" to the meaning of code 1.

### N8. How the sink learns the inputs is not stated - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-style`
* **Affected Scope:** lines 88-89, 94, 146
* **Discrepancy Description**: `close()` writes a `cancelled` record, which includes `url`, for each missing index and checks the input count, but jobs reach the pipeline as one shared iterator.
* **Rationale & Recommendation**: Add a clause to the `sink.py` row: "constructed with the full job list".

### N9. `block` "loses nothing" has an unstated exception - Severity: Nit
* **Reviewer Lens:** `egm-design`, `egm-structural`, `egm-style`
* **Affected Scope:** lines 27, 64, 109
* **Discrepancy Description**: On interrupt, up to N workers waiting in `await queue.put()` are cancelled and their completed fetches become `cancelled`. "`drop-oldest` never slows fetching" is also broader than the mechanism.
* **Rationale & Recommendation**: Line 27: "`drop-oldest` (default) never makes a worker wait. `block` makes workers wait and drops nothing." Line 64: "in-flight fetches and results not yet queued are cancelled".

### N10. CUJ 2 "finishes at fetch speed" - Severity: Nit
* **Reviewer Lens:** `egm-design`, `egm-style`
* **Affected Scope:** line 58
* **Discrepancy Description**: Fetching finishes at fetch speed; the run still parses up to `queue-max` queued results with the slow parser.
* **Rationale & Recommendation**: Write "fetching finishes at fetch speed".

### N11. Worker-pool and sentinel rationales are imprecise - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-style`
* **Affected Scope:** lines 203, 214-215
* **Discrepancy Description**: "Gives backpressure with no extra mechanism" (203) is true only under `block`. The sentinel's "must wait for space" (214) applies only to `block`; under `drop-oldest` the defect recorded in the brief is that a sentinel evicts a real result. These concern wording only and do not reopen either decision.
* **Rationale & Recommendation**: Line 203: cut the backpressure clause or qualify it with "under `block`". Line 214: "one per consumer; under `drop-oldest` putting it into a full queue evicts a real result, and under `block` the put must wait".

### N12. "Rejected by author:" prefix repeated - Severity: Nit
* **Reviewer Lens:** `egm-design`, `egm-structural`
* **Affected Scope:** lines 197, 203, 209, 215
* **Discrepancy Description**: The standard asks for rejection reasons on technical grounds and discourages attribution to a person. The reasons given are concrete, so the prefix adds nothing.
* **Rationale & Recommendation**: Delete the four prefixes (saves 12 words).

### N13. Duplicate sentences that can be cut - Severity: Nit
* **Reviewer Lens:** `egm-style`, `egm-structural`
* **Affected Scope:** lines 13, 23-24, 39, 104, 113, 191, 215, 223
* **Discrepancy Description**: Line 191 repeats line 13. Line 39's "No input can end a worker; a bug ends the run loudly" repeats lines 119-120. Line 223 repeats line 68 and the last sentence of line 113. Lines 23-24 can be one bullet. The brief also disagrees with the design on per-host delay and the semaphore.
* **Rationale & Recommendation**: Cutting these saves about 85 words. Delete line 191 and add "this document supersedes it where they differ" to line 13.

### N14. Terms used before or without definition; retry index base - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-style`
* **Affected Scope:** lines 74, 98, 100, 113, 195, 223
* **Discrepancy Description**: N and M appear in the diagram before they are defined. "Free-threaded build", "standard build" and "GIL" are never defined. "Retry k" does not say whether k starts at 0 or 1, which changes the backoff ceiling. "Keeps its place in the pool" (100) is unclear.
* **Rationale & Recommendation**: Label the diagram with the flag names, write "retry k (k = 1..R)", define free-threaded once, and replace "keeps its place in the pool" with "does no other work".

### N15. Subjective or colloquial wording - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** lines 17, 26, 30, 39, 195, 201, 207, 209
* **Discrepancy Description**: "useless for a long list" (17), "Clean stop" (30), "loudly" (39), "familiar" (195), "the shortest code" (201), "the natural choice" (207), "one flag away" (209), "politeness" (26).
* **Rationale & Recommendation**: Replace with plain statements, for example "does not finish a long list", "Stop on Ctrl-C", "less code", "suits a finite list", "`block` remains available by flag", "Per-host delay".

### N16. Output encoding and newline claim when writing to stdout - Severity: Nit
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** lines 150, 176
* **Discrepancy Description**: UTF-8 and `\n` are stated for all output, but on Windows text-mode stdout translates `\n` to `\r\n` and may not be UTF-8 (unverified for 3.15 defaults).
* **Rationale & Recommendation**: Add a clause that stdout is reconfigured to UTF-8 with `newline="\n"`, or limit the claim to `--out`.

### N17. Small mismatches - Severity: Nit
* **Reviewer Lens:** `egm-structural`, `egm-style`
* **Affected Scope:** lines 25, 170, 176, 217, 225
* **Discrepancy Description**: Line 25 says a bad fetch "produces an error record", but an evicted fetch error is written as `dropped`. `[URL_OR_FILE ...]` makes the argument optional with no stated behaviour for none. The horizontal rules at 217 and 225 separate sibling H2 sections for no structural reason.
* **Rationale & Recommendation**: Line 25: "produces a record carrying the error". Line 170: write `URL_OR_FILE [...]` or add "with none, exit 2". Remove the rules.

## Unverified statements the design depends on

- Line 96: httpx timeouts are per phase and do not bound an attempt.
- Line 113: `get()` raises `QueueShutDown` only when the queue is shut down and empty.
- Line 144: on the first Ctrl-C `asyncio.run` cancels the main task, and a second makes it raise `KeyboardInterrupt`; M1 and M3 depend on where and when that second signal is delivered. The same behaviour on Windows is assumed.
