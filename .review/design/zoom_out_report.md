# Goldfish Review Report

**Target:** `C:\Users\sonic\source\repos\async-link-extractor\docs\designs\linkx.md` (`1-263`)

**Stage:** 5, Initial Zoom Out Review. **Background given to reviewers:** `docs\designs\linkx-brief.md`.

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-zoom-out` | completed |
| `egm-inventive-catalyst` | completed |

One instance of each reviewer ran. Neither needed a retry.

## Severity Breakdown

| Severity | Count |
| --- | --- |
| Blocker | 0 |
| Major | 7 |
| Nit | 8 |

The two reviewers raised 20 findings between them; 15 remain after merging duplicates. Where the reviewers gave different severities for the same finding, the higher one is used and the difference is noted in the finding.

**Length budget.** The document is about 3,000 words against a budget of about 2,500. Findings 1, 2, 4, 6 and 7 together remove roughly 500 to 600 words (reviewer estimates, not measured), which reaches the budget. Finding 3 option B removes about 60 more. Findings 5 and 9 add or change text at close to zero net.

**Not verified.** Finding 3 rests on how `asyncio.run` handles a second Ctrl-C. The reviewer cited the documentation from memory and nothing was run. It should be confirmed with a short experiment before the design is changed on that basis.

## Prioritized Findings (Blockers -> Major -> Nits)

### 1. Whole-iteration catch-all contradicts the sink's invariant check and the brief's writer-failure rule - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`, `egm-inventive-catalyst` (both Major)
* **Affected Scope:** line 103 (Producers, "Workers cannot die from an input"), line 157 (Record and outcomes), line 41 (Non-Goals), lines 212-216 (Alternatives, supervisor), lines 170-174 (exit codes)
* **Discrepancy Description**: Line 103 says "Each worker and consumer wraps its whole iteration, including the queue put and the emit, in a catch of `Exception`. Anything caught there becomes a record with error kind `internal` ... and the loop continues." Line 157 says "Emitting an index twice, or finishing with any index outstanding, is a programming error and raises." Both cannot hold: an exception raised by `emit` is caught by the wrapper, which then emits a second record for the same index. Three consequences follow.
  * A writer failure (full disk, closed stdout pipe) is caught once per input, so the run keeps fetching the whole list while writing nothing. The brief resolved the opposite (brief line 16: "Keep the consumer in the same group so a writer failure cancels the producers").
  * If emitting a `dropped` record for an evicted entry fails, the worker records `internal` against its current job, which is already on the queue and is later emitted again by the consumer.
  * The wrapper is a second layer over catches the design already has: line 87 says `fetch` "Never raises `Exception`" and line 118 runs the parse "inside a catch of `Exception`". Those two cover every failure caused by an input.
* **Rationale & Recommendation**: Delete the loop-level wrapper. Keep the two boundary catches and let queue and sink errors propagate through the TaskGroup and end the run with a non-zero exit. Then cut the supervisor material, which exists only to justify the wrapper and appears three times: line 41, the last sentence of line 103, and lines 212-216. Saving is about 150 words. Two follow-ons: a bug in the pipeline code now ends the run instead of producing `internal` records, and the exit code table at lines 170-174 needs a code for an internal or output failure.

### 2. Per-host rate limit is in the core although the brief defers it, and it conflicts with the fixed worker pool - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`, `egm-inventive-catalyst` (both Major)
* **Affected Scope:** line 26 (Goals), line 40 (Non-Goals), line 88 (Components), line 101 (Per-host delay), line 165 (`--per-host-delay`), line 246 (step 7)
* **Discrepancy Description**: The brief puts this out of scope: "per-host rate limit, robots.txt and Dockerfile (design notes unless time remains; six lenses advise deferring them now)" (brief line 40). The design makes it a goal with its own module, flag, injected clock and tests, and gives no reason for the change. Non-Goals line 42 defers only the Dockerfile and robots.txt. The feature is "default 0, off", so the core behaves the same without it. It also conflicts with the pool: a worker in `await limiter.wait(host)` holds one of N slots while it sleeps, so on a list grouped by host all N workers queue behind one host and concurrency falls to 1. The document does not say whether the wait counts toward `elapsed_ms` (line 178 says "including retries and backoff"), toward p95, or toward the per-attempt `asyncio.timeout()`.
* **Rationale & Recommendation**: Move it to step 12 with the Dockerfile and robots.txt and add it to line 42. Cut line 26, the line 88 row, line 101, the flag at line 165 and the limiter half of line 246. Line 40 ("Rate limiting consumers") exists only as a contrast to this feature and goes too (see also finding 4). Saving is about 130 to 170 words and one module. The README must then say that `--concurrency 10` can send 10 parallel requests to one host. If the author has a requirement for this feature that is not in the brief, keep it and add two sentences instead: what the wait counts toward, and that same-host lists run serially.

### 3. A second Ctrl-C does not behave as described and breaks the one-record-per-input goal - Severity: Major
* **Reviewer Lens:** `egm-inventive-catalyst`
* **Affected Scope:** lines 122-138 (Completion and interrupt), lines 62-66 (CUJ 3), line 30 (Goals)
* **Discrepancy Description**: Line 138 says "A second Ctrl-C during the drain cancels the consumers as well." Per the `asyncio.run` documentation, the first Ctrl-C cancels the main task and a later one raises `KeyboardInterrupt` immediately without cancelling it. This was cited from memory and not run. If it holds, on a second Ctrl-C the line "emit a `cancelled` record for every job with no record yet" is never reached, the summary is not printed, and exit status 130 is not guaranteed. The drain can take up to `queue-max` (100) parses at the consumer's pace, so a second Ctrl-C is most likely exactly when the consumer is slow. Separately, `except CancelledError: interrupted = True` also fires when a consumer failure cancels the parent task; once finding 1 is applied, an internal failure would be labelled as an interrupt.
* **Rationale & Recommendation**: Confirm the behaviour with an experiment first. Then choose one:
  * **Option A (smaller change):** the sink is synchronous, so the final `cancelled` records and the summary can be emitted from a plain `finally` in `cli.py` that needs no event loop. Replace the second-Ctrl-C sentence with that, and set `interrupted` from the signal, not from any `CancelledError`. Cost: a second Ctrl-C can land mid-`emit` and leave a partial last line, which the README must mention.
  * **Option B (removes a mechanism):** drop the drain. Use one TaskGroup; on cancel everything stops and the same `finally` marks every outstanding index `cancelled`. This removes the nested group, `uncancel()`, the second-Ctrl-C case and the experiment sentence, about 60 words. Cost: up to `queue-max` fetched pages are reported `cancelled` instead of parsed, and the goal at line 30 ("results already queued are still written") must be reworded.

### 4. `--consumers M` has no stated need - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (Major), `egm-inventive-catalyst` (Nit)
* **Affected Scope:** line 40 (Non-Goals), line 76 (diagram), line 118 (Consumers), line 162 (CLI), line 228 (Memory), line 230, line 247 ("several consumers")
* **Discrepancy Description**: The design gives no reason to run more than one consumer, and its own text says the setting adds no throughput: "It does not add CPU throughput on the standard build" (line 230) and "fetching dominates the run time" (line 222). The brief says "parsing outpaces fetching" (brief line 19) and describes a single consumer. The setting adds a flag, a term in the memory bound, a test case and a non-goal nobody asked about.
* **Rationale & Recommendation**: Fix one consumer. Delete line 40 and the flag, and replace the M mentions with one sentence saying `Queue.shutdown()` would stop several consumers if that were ever needed. Leaving it out closes nothing off. Saving is about 40 words, one flag and one test case. Cost: no benefit on free-threaded builds until the flag is added back. Neither reviewer could see the original problem statement; if it requires multiple consumers, cite that in one clause and keep the flag.

### 5. The Problem section motivates the bounded queue with a case the brief says does not occur - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 17 (The Problem), line 27 (Goals), line 198 (Alternatives, `block` default)
* **Discrepancy Description**: Line 17 says "A tool that buffers every page in memory can exhaust it when parsing falls behind fetching." The brief says "parsing outpaces fetching, so drops will not occur at the defaults. The README must say this plainly" (brief line 19). The design never says this. A reader learns only from line 198 ("the requirement asks for trimming the oldest entries") that the default policy comes from an external requirement, and the document never says what that requirement is or where it comes from. CUJ 2 only happens with an injected slow consumer (line 247).
* **Rationale & Recommendation**: A reader cannot tell whether drop-oldest solves an observed problem or satisfies an assignment. Replace the last sentence of line 17 with one of about the same length saying that a bounded queue with drop-oldest is required by the problem statement, and that with real parsing at the defaults the queue is not expected to fill. Net zero words.

### 6. Alternatives Considered repeats the brief and the plan - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (Major, for the length budget), `egm-inventive-catalyst` (Nit, largest single cut)
* **Affected Scope:** lines 180-222 (about 420 words)
* **Discrepancy Description**: Line 13 already links the brief, which lists five of the seven alternatives with the same reasons (brief lines 44-53): semaphore, `block` default, sentinel, second queue or marker, process pool. Only "Threads" is new. The supervisor entry (212-216) goes with finding 1; the process pool entry (218-222) is covered by line 230; the second queue entry (206-210) is in brief line 50 nearly word for word. "Rejected by author:" appears seven times with no other rejecter in the document, and is wrong for the second queue, which the brief says was "Rejected by reviewers".
* **Rationale & Recommendation**: Keep "Threads" and the `block`-default entry in full, since the latter is the decision a reader is most likely to question. Delete the supervisor, process pool and second queue entries, reduce the others to one line each with a pointer to the brief, and remove the "Rejected by author:" prefix. Saving is about 110 to 200 words beyond what finding 1 counts. One reviewer noted it had not read the feature-spec standard: if that template requires Pros, Cons and Reason per entry, keep the format and cut the entry count instead.

### 7. Detailed Implementation Plan repeats the Components table and the Verification Plan - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (altitude and length); `egm-inventive-catalyst` raised the Components table overlap as a Nit
* **Affected Scope:** lines 240-251 (about 520 words), lines 84-93 (Components)
* **Discrepancy Description**: Step 1 (line 240) lists every scaffold file and dev dependency. Step 2 (line 241) repeats the commands in line 257 and the versions in line 31. Step 10 (line 249) lists README headings that lines 38, 39, 198, 222 and 230 already commit to. The Components table restates module responsibilities that steps 3-9 (lines 242-248) give again.
* **Rationale & Recommendation**: Reduce step 1 to "`pyproject.toml` (runtime dependency `httpx`), lockfile, package skeleton". Reduce step 2 to "runs the Verification Plan commands on 3.13, 3.14 and 3.15". Cut the README list from step 10. Keep the per-step test cases, which map to the failure-path goal. Saving is about 90 words. Optionally trim the Components table to module and signature for about 50 more.

### 8. Policy lookup table is built for policies that do not exist - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`, `egm-inventive-catalyst`
* **Affected Scope:** line 89 (Components), line 109 (Queue and overflow policy), line 244 (step 5)
* **Discrepancy Description**: "Policies are held in a table keyed by the `--queue-policy` value, so adding one is a function plus a table entry." There are two policies and no third is planned.
* **Rationale & Recommendation**: Cut the sentence and the "name-to-policy table" wording. The argument parser's choices list already rejects unknown names, which also removes the "unknown policy name rejected" test. Saving is about 30 words.

### 9. "Open Questions: None" overstates what the plan answers - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`, `egm-inventive-catalyst`
* **Affected Scope:** lines 44-46, line 229, line 99, lines 149-155, line 11
* **Discrepancy Description**: Line 46 says "None. The questions raised in the brief are answered in the Technical Plan." Line 229 contradicts it by deferring a decision: "the default policy and its test are set in the security review before release". Five brief items are also unanswered: where a decode failure lands (brief line 70; not in the table at 149-155); the total per-URL deadline, backoff values and the redirect limit number (brief line 74; line 99 says only "a redirect limit"); and whether httpx and pytest-asyncio support 3.15 (brief line 21: "was not verified"), although line 11 says the tool is "built and shipped on 3.15".
* **Rationale & Recommendation**: Do not add plan text. Replace line 46 with one line listing these as settled during implementation, or keep the 3.15 dependency check and the line 229 decision as the open items. About 15 words added.

### 10. Coverage gate is stricter than the goal and inconsistent with CI - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 31 (Goals), line 241 (step 2), line 258 (Verification Plan)
* **Discrepancy Description**: The goal is "with a coverage report". Line 258 requires "95% line and branch coverage, enforced by `--cov-fail-under=95`". The CI step at line 241 runs `pytest --cov` and "prints the coverage report" with no threshold.
* **Rationale & Recommendation**: The brief asks for "a test per failure path" (brief line 39), not a hard branch-coverage gate. Choose one; the simpler choice is to report coverage without gating it. Saving is about 10 words.

### 11. Summary reports more than the goal asks for - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 29 (Goals), line 178 (Summary)
* **Discrepancy Description**: The goal is "counts per outcome, drops, and p95 fetch latency". Line 178 adds "total retries, and p50, p95 and maximum".
* **Rationale & Recommendation**: Keep the goal's set, or update the goal so the two lines agree. Saving is about 8 words.

### 12. The Ctrl-C goal omits the `cancelled` outcome that CUJ 3 depends on - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** line 30 (Goals), line 66 (CUJ 3), line 135 (pseudocode)
* **Discrepancy Description**: Line 30 says "Clean stop on Ctrl-C through the same path as normal completion: results already queued are still written." It omits the `cancelled` outcome that CUJ 3 and line 135 depend on. Line 28's "Every input produces exactly one record" holds on interrupt only because of that outcome.
* **Rationale & Recommendation**: Reword line 30 to "queued results are written and the rest are recorded as `cancelled`". Length unchanged. If finding 3 option B is taken, this line is reworded there instead.

### 13. CUJ 2 overstates the first run's finish time - Severity: Nit
* **Reviewer Lens:** `egm-inventive-catalyst`
* **Affected Scope:** line 60 (CUJ 2)
* **Discrepancy Description**: "the first run finishes at fetch speed". It finishes at fetch time plus the drain of up to `queue-max` entries at the slow consumer's pace.
* **Rationale & Recommendation**: Say "fetching is not slowed".

### 14. The `block`-default rationale keeps a reason the brief records as weak - Severity: Nit
* **Reviewer Lens:** `egm-inventive-catalyst`
* **Affected Scope:** line 198 (Alternatives, `block` default)
* **Discrepancy Description**: "which suits a live feed where newer data replaces older". The brief records "Reviewers accept the decision but find the live-feed reason weak for a finite list."
* **Rationale & Recommendation**: Cut the clause and keep "the requirement asks for trimming the oldest entries". Saving is about 12 words.

### 15. Small repeated or code-level sentences that can go - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`, `egm-inventive-catalyst`
* **Affected Scope:** lines 23-24, 97, 114, 138
* **Discrepancy Description**: Each of these repeats text elsewhere or sits below design level.
  * Line 114, "`join()` and `task_done()` are not used.": brief line 52 covers it (8 words).
  * Line 97, "and no semaphore is needed": lines 188-192 say it (5 words).
  * Line 97, "`next()` on the iterator contains no `await`, so two workers never take the same job.": code-level detail (15 words).
  * Line 138, "An experiment on Python 3.13 and 3.15 confirmed that without `uncancel()` the queued results are lost.": evidence, not design; it disappears under finding 3 option B (17 words).
  * Lines 23-24: the goals "Concurrent stages" and "Concurrent fetching" can merge into one bullet (about 10 words).
* **Rationale & Recommendation**: Delete or merge as listed. Saving is about 55 words.

## Areas Checked With No Finding

* **Context sufficiency:** the objective and motivation are clear from lines 11-19, apart from finding 5.
* **Codebase reuse:** the repository holds only the two design documents outside the excluded `notes\` folder, so there is nothing to reuse.
* **Platform standards:** the design uses `asyncio.Queue.shutdown()`, `TaskGroup`, `asyncio.timeout()`, `html.parser` and `httpx.MockTransport`, and writes no custom queue class.
* **Premature optimisation:** none; the process pool and byte-based bound are deferred.
* **Drop-oldest atomicity:** there is no suspension between eviction, put and the `dropped` emit, and shutdown runs only after the workers exit.
* **Fixed pool against semaphore:** the pool removes the semaphore and gives `block`-mode backpressure with no extra mechanism.
* **Single synchronous sink:** resolved as the brief asked, with no lock or second queue.
* **Memory bound:** stated by construction at line 228.
* **Drop-oldest as default:** an author decision recorded in the brief; neither reviewer offered a cheaper alternative than the existing `block` flag.
* **Architectural dead-ends:** none; the single sink and plain queue block neither a broker nor more consumers later.
