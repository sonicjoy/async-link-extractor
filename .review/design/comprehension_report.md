# Stage 6: Comprehension Test

**Target:** `docs/designs/linkx.md` (FeatureRange 1-233, DetailedRange 234-263, as reviewed before fixes). The reviewer read only the target. Result: both ranges could be paraphrased; 0 Blockers, 4 Major, 9 Nits.

## Paraphrase: FeatureRange (lines 1-233)

**What this proposes**

A command-line tool, `linkx`, that takes URLs (as arguments or in files) and writes one JSON line per input, listing the links on that page or saying why there are none. A fixed pool of N fetch workers pulls jobs from one shared iterator and puts each fetch result on a bounded queue. M consumers take results off, parse the HTML in a thread, and write records through one shared synchronous sink.

When the queue is full, one of two policies applies. `drop-oldest` (the default) evicts the oldest queued result, and the worker writes a `dropped` record for it. `block` makes the worker wait.

Every input ends with exactly one of five outcomes: `ok`, `fetch_error`, `parse_error`, `dropped` or `cancelled`. The sink enforces this by tracking which indexes have no record yet. Completion and Ctrl-C share one path: the workers end, the queue is shut down, consumers drain what is queued, and any input still without a record gets `cancelled`. A summary of counts and latency percentiles goes to stderr.

**How the existing system works, per this document**

There is no existing system. The document describes a new tool and its plan creates every file from an empty repository. The only prior material it mentions is a brief, `linkx-brief.md`. In place of a current system, it describes the designs it rejects: fetch and parse in one loop, stop on the first bad page, or buffer every page in memory.

## Paraphrase: DetailedRange (lines 234-263)

**What this proposes**

A twelve-step build order, tests first, each step merged through a reviewed pull request: scaffold; CI on Python 3.13, 3.14 and 3.15; models; parser; overflow policies; sink; rate limiter and fetch; pipeline; CLI and an end-to-end test against a local HTTP server; demo script and README; a security review step that sets the policy for redirects to private addresses; optionally a Dockerfile and robots.txt support. Verification is `uv sync`, pytest with 95% line and branch coverage, ruff, mypy, the end-to-end test, and the demo.

**How the existing system works, per this document**

Nothing exists yet; every step except step 11 is marked Create.

## Findings

### Major

1. **An external "requirement" drives the default policy but is never stated (lines 185, 198, 203; also 42, 251).** The default is justified by "the requirement asks for trimming the oldest entries", but no requirement appears in the document. "Trim" and "the trimming rule" are used as known terms while the main text says "drop-oldest" and "evict". Scope depends on "the core has passed review" and "if time remains" without saying which review or what limit.
2. **"Open Questions: None" conflicts with a deferred decision (lines 46, 229, 250).** The policy for redirects to private addresses is left to a later security review. That is an open design question, with no candidate policies named.
3. **Behaviour is not described when the guarantees fail (lines 103, 138, 157, 170-174).** Second Ctrl-C: whether remaining inputs still get `cancelled` records, whether the summary prints, and the exit code are not stated, and the pseudocode would skip the emit. Non-`Exception` errors "end the run" with no stated output. The sink "raises" with no stated handler or exit code. Nothing carries the `interrupted` flag to the CLI for exit 130.
4. **The worker and consumer guard does not connect with the sink's own failures (lines 103, 157).** A guard that includes the emit would catch the sink's double-emit error and emit again, or loop on an I/O failure such as a full disk. Which index gets the `internal` record after an eviction is not stated.

### Nit

5. Record fields for non-`ok` outcomes are unspecified; error kinds are not listed (142-155).
6. Retry rules differ between sections; `--retries`, `--timeout`, backoff and redirect limit are not defined (24, 99, 152, 164).
7. Summary contents differ between sections (29, 178).
8. Whether the per-host wait sits inside the attempt timeout or counts toward `elapsed_ms` is unstated (101, 178).
9. CUJ 2 needs a slow consumer, which only the demo script can provide (56-60, 247).
10. The `uncancel()` experiment is cited without a reason (138).
11. Paths differ between the Components table (`linkx/`) and the plan (`src/linkx/`); `Summary` is not listed in any module (86-93, 240-251).
12. The 95% coverage gate is not tied to CI (241, 258).
13. Several points are delegated to a README that does not exist yet (38, 39, 198, 222, 230).
