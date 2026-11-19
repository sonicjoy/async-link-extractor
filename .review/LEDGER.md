# Review ledger

The short memory across reviews: the kinds of mistake found here, what is still open, and one row per review. Detail lives in each review's own folder.

## Classes seen

| Class (one general sentence) | Seen in | Guardrail |
|---|---|---|
| A fix that handles exactly the reported case or exception type, not the property it broke | feat-core (charset fallback, non-UTF-8 input, finite-number check) | none in code; the fix review asks for other routes to the same failure. Proposed: state the property in the fix commit and test it across its input space (as done for every codec name) |
| A limit enforced in our own reader while a library reads the same data elsewhere | feat-core (size cap skipped by httpx reading 3xx bodies; httpx decompressing whole chunks) | structural: one body-read path (`_read_body`); redirects are followed by linkx and closed unread; bomb tests on both paths |
| A test that still passes with its fix reverted | feat-core (first-Ctrl-C drain, `_inflate` +1, sink close inside the Ctrl-C span) | mutation checks for each fix (`.review/feat-core/evidence/mutants_round2.py`). Proposed: a mutation-testing job in CI |
| Reporting the first exception of a TaskGroup's group, which may be a consequence of the real failure | feat-core (sink refusals hid a broken pipe) | `_root_cause` prefers the first non-refusal; test with concurrent writers |
| A safety check that silently passes when the value it checks is missing (fails open) | feat-core (connected-address check skipped when the peer could not be read) | fail closed with its own message; test with an unknown peer; real-socket reproduction in evidence |

## Open follow-ups

| Item | Where | From | Status |
|---|---|---|---|
| Control characters from the URL list reach plain-text logs; userinfo and tokens in URLs are logged and written verbatim | src/linkx/pipeline.py, src/linkx/fetch.py (logging) | feat-core, security LOW | open |
| Under DNS rebinding the GET still reaches the internal address (response is refused); pin the checked IP in a custom transport | src/linkx/fetch.py | feat-core, security HIGH, author chose the response check | open |
| `5f00::1` (SRv6) accepted by `is_public` | src/linkx/fetch.py | feat-core, security LOW | open |
| Raw (headerless) deflate bodies are refused as `unexpected` | src/linkx/fetch.py | feat-core, decision | open |
| Slow DNS answers can tie up the default thread pool shared with parsing | src/linkx/fetch.py | feat-core, security LOW | open |
| Retries ignore `Retry-After`; a 429 is retried after 0 to 2 s (873 wasted retries in the live run) | src/linkx/fetch.py | docs/live-run, live run | open |
| Politeness is per hostname, not per provider; many hostnames can share one rate limit | src/linkx/ratelimit.py | docs/live-run, live run | open |
| Ctrl-C while the HTTP client closes (after the handler is restored) exits 130 without the summary; records are already complete | src/linkx/pipeline.py | feat-core, fix review round 1 MINOR | open |
| A late interrupt can log a line after the run has ended | src/linkx/pipeline.py | feat-core, fix review round 1 MINOR | open |
| "cannot read input: not UTF-8" does not name the file | src/linkx/cli.py | feat-core, fix review round 1 MINOR | open |
| `Settings` built in library code has no upper bounds (only the CLI checks them) | src/linkx/models.py | feat-core, fix review round 3 MINOR | open |
| A record lost during an interrupted run is indistinguishable from a cancelled job | src/linkx/sink.py | feat-core, author's stress check | open (design limit) |
| The `Accept-Encoding` pin has no test that fails without it unless brotli or zstandard is installed | tests/test_fetch.py | feat-core, fix review round 1 MINOR | open |
| CI actions are tag-pinned, not SHA-pinned | .github/workflows/ci.yml | feat-core, security LOW | closed: merged in b4880b0 |
| Dockerfile and opt-in robots.txt (designed, not built) | README "Not built" | design non-goals | open |

## Reviews

| Date | Branch or audit | HEAD | Outcome |
|---|---|---|---|
| 2026-11-19 | concept (7-lens brainstorm) | n/a | 6 contradictions; record shape, single sink, worker pool adopted. [brief](../docs/designs/linkx-brief.md) |
| 2026-11-19 | design stage 5, zoom out | n/a | 0 Blocker, 7 Major; catch-all guard and second Ctrl-C fixed. [report](design/zoom_out_report.md) |
| 2026-11-19 | design stage 6, comprehension | n/a | 0 Blocker, 4 Major; failure paths specified. [report](design/comprehension_report.md) |
| 2026-11-19 | design stage 7, critic | n/a | 0 Blocker, 6 Major; late-Ctrl-C drain, audit gap fixed. [report](design/critic_report.md) |
| 2026-11-19 | design stage 9, readiness | n/a | 1 Blocker (address policy, decided by the author), 13 Major. [report](design/implementation_readiness_report.md) |
| 2026-11-19 | design stage 8, intermediate zoom out | n/a | 0 Blocker, 4 Major; redirect checks moved to an httpx hook after an experiment. [report](design/intermediate_zoom_out_report.md) |
| 2026-11-19 | feat-core (code) | 26f47a7 | Full path. Correctness 0 blocker / 2 major, security 1 high / 1 medium, simplicity 5 should; 4 fix sets, 4 fix reviews (round 2 reached the cap: 1 major went to the author, who chose fail closed). 5 classes added, 13 follow-ups open. Needed the user: 3 (rebinding approach, design wording, fail-closed after the cap). First GitHub CI run failed (missing `setup-uv@v10` tag); actions SHA-pinned in 5edfad0, fix-reviewed, CI green on all 4 jobs. Not "clear" (no formal model check); pushed and merged with the user's go-ahead. [packet](feat-core/human-review.md) |
| 2026-11-20 | docs/live-run | (this commit) | Docs-only path: one fact-check reviewer recomputed every number from the logs and records. 1 false ("last 1%" was the last 2.6%), 2 imprecise (5-second progress granularity), 1 stale comment, 2 unverifiable claims reworded; all fixed. 2 follow-ups added (Retry-After, per-provider politeness). [review](docs-live-run/review.md) |

## Decisions kept against reviewer advice (design)

| Decision | Reviewer position | Why it stands |
|---|---|---|
| Per-host delay in the core | Defer to a design note | Politeness towards target hosts matters for any real fetcher; off by default, one small class |
| `--consumers M`, default 1 | Fix one consumer | Shows the queue works many-to-many; pays off on free-threaded builds; README gives measured numbers |
| `drop-oldest` as the default | Weak reason for a finite list | The requirement asks for trimming the oldest entries; `block` is one flag away |

## Behaviour confirmed by experiment rather than from memory

| Question | Result (Python 3.13.18 and 3.15.2) |
|---|---|
| Does `asyncio.run` drain queued results on Ctrl-C? | No: queued results are lost unless the cancel is handled |
| What does a second Ctrl-C do under `asyncio.run`? | Raises `KeyboardInterrupt` and cancels the consumers mid-drain |
| Does an explicit SIGINT handler give a two-stage stop? | Yes, on Windows and in a Linux container |
| Does an httpx request hook run on every redirect hop? | Yes (httpx 0.28.1), and an exception raised in it propagates unchanged |
| Does a response hook see the connected peer address? | Yes, `network_stream.get_extra_info("server_addr")` on every hop, before the body is read |
| Does httpx read 3xx bodies when following redirects? | Yes, in full: a 302 with a gzip bomb peaked at 400 MB |
