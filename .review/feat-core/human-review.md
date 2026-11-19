# Human review: feat/core

Needs you: 0 · Decisions made for you: 9 · Cleared with proof: 7
Spec/plan: `docs/designs/linkx.md` (Status: Approved; wording updated with the author's approval to match review fixes)
Reviewed at: 26f47a7. Commits since: none

| Evidence | Status | File |
|---|---|---|
| Format, lint, types, tests | done: ruff, ruff format, mypy strict clean; 254 tests pass on Python 3.13, 3.14, 3.15 (Windows); 100% line and branch coverage | checks.txt |
| New tests fail on base | done: on `main` every test module fails to import `linkx` (no code exists there). Each review fix also has a mutant that a test kills: 1 (round 1), 5 (round 2), 4 (round 3) | checks.txt, evidence/mutant_first_interrupt.txt, evidence/mutants_round2.txt, evidence/mutants_round3.txt |
| Behaviour: live drive (CLI on real sites, real redirects) | pass: 6 public pages match an independent extraction (one difference is a link inside an HTML comment, correctly ignored); 4 `http://` URLs follow real redirects to `https://` | evidence/replay_real.txt, evidence/live_redirects.txt |
| Behaviour: live drive (real Ctrl-C) | pass: Linux container, python:3.15-slim, real SIGINT: exit 130, queued pages written, second Ctrl-C stops the drain, one record per job | evidence/linux_ctrl_c.txt |
| Behaviour: boundary drive | pass: max-bytes (declared and streamed), redirects (9 / 10), timeout, retries, queue size, each at and past the bound, next request still succeeds; fails when bounds are shifted by one | evidence/boundary.txt |
| Behaviour: generated inputs | pass: 20,000 generated pages, 20,000 URLs, 6,500 reserved addresses (seed 20261119); fails against a deliberately wrong extractor | evidence/generated.txt |
| Behaviour: concurrency (model check) | not done: the repo has no model. Instead: 300 randomised schedules (workers, consumers, queue, policy, failures, stuck jobs, 0 to 2 interrupts) give one record per job every time; the correctness reviewer ran 1,000 more. Limit: a record lost during an interrupted run would be filled as `cancelled` (ledger follow-up) | evidence/stress.txt |
| Behaviour: security reproductions | pass: gzip bomb over real HTTP, peak 737 MB before, 10.9 MB after; DNS rebinding with a reset socket, 40 of 40 leaked before, 40 of 40 refused after; redirect carrying a bomb under 5 MB (test) | evidence/gzip_bomb.txt, evidence/rebind_reset.txt |
| Behaviour: measured comparison | pass: README numbers from 3 alternating runs per build: 4 consumers no faster with the GIL (2.20 to 2.27 s vs 2.20 to 2.32 s), 2.8 times faster free-threaded (0.79 to 0.88 s vs 2.33 to 2.40 s) | evidence/measured.txt |
| Behaviour: before/after on base, replay of saved inputs | not applicable: base has no code; real pages were fetched live instead | n/a |
| Correctness, security, simplicity reviews | done | correctness.md, security.md, simplicity.md |
| Fix review of the last fix set | done: round 4 PASS, no findings (rounds 1 and 2 FAIL and fixed; round 3 PASS WITH ISSUES and fixed) | fix-review-round1.md to fix-review-round4.md |
| Ledger: Reviews row, classes, follow-ups | done: 5 classes, 13 follow-ups, this review's row | ../LEDGER.md |

Outside the repo: the review read six public web pages and ran a throwaway Docker container (`python:3.15-slim`, removed with `--rm`). Temporary git worktrees under the session scratch folder were removed. Nothing else.

---

## Needs you

Nothing. Three questions came up during the review and the author answered each one (recorded in decisions.md): close DNS rebinding with a connected-address check; update the design's wording; fail closed when the connected address cannot be read, and fix the four minors from that review.

## Decisions made for you

| Decision | Alternative | Why this one | To switch |
|---|---|---|---|
| A bad redirect `Location` stays `connect` and is retried | Not retried | The same httpx error also covers a server dropping a connection, which is transient | `_attempt` in `fetch.py` |
| Unsupported `Content-Encoding` is `unexpected` | A new error kind | The error kinds are a published contract; linkx asks only for gzip and deflate | `_read_body` in `fetch.py` |
| Raw (headerless) deflate is not decoded | Retry with raw deflate | Rare; reported, not hidden | `_read_body` |
| `Accept-Encoding` pinned to `gzip, deflate` | httpx's default (adds br/zstd when installed) | The bounded decoder handles exactly what is asked for | `make_client` |
| U+2028, U+2029, U+0085 escaped in output | `ensure_ascii=True` | Keeps other text readable | `Record.to_json` |
| Upper bounds on every numeric option | Other limits | Far above real use; stop typos from hanging or exhausting memory | `build_parser` |
| A transport with no network stream skips the connected-address check | Refuse it | httpx's own transport always has one (the CLI is always checked); test transports and custom library transports may not; documented in `extract` and `make_client` | `_check_response` |
| Ctrl-C during the HTTP client's close exits 130 without a summary | Keep the handler until `extract` returns | Records are complete by then; window is the client close only | ledger follow-up |
| Review reports and evidence are published in the repo (patches excluded, local paths removed) | Keep them local | The decision record is part of the deliverable | `.git/info/exclude` |

## Cleared, with proof

| Item | If it were wrong | Safety fact | Proof |
|---|---|---|---|
| [HIGH] Non-public address checks (request hook, connected-address hook, fail closed) | An untrusted URL list reads internal services | Every request and redirect hop is checked before connecting and after, and an unknown peer is refused | tests in test_fetch.py and test_e2e.py; evidence/rebind_reset.txt (0 of 40 leaked); evidence/generated.txt (6,500 reserved addresses refused); fix-review-round3.md (900 healthy fetches accepted, 0 bodies through over HTTP/HTTPS, IPv4/IPv6, keep-alive, redirects) |
| [HIGH] Size cap and bounded decompression | A hostile server exhausts memory | Only the final body is ever read, through `_read_body`, which never holds more than `--max-bytes` + 1 decoded bytes | evidence/gzip_bomb.txt (10.9 MB vs 737 MB); bomb-on-redirect test under 5 MB; `_inflate` +1 test |
| [HIGH] One record per job on every exit path | Silent loss or duplicates in the output | `Sink.emit` refuses duplicates and writes after a failure; `close` fills only after an interrupt and raises otherwise | evidence/stress.txt (300 runs), correctness.md (1,000 runs), evidence/linux_ctrl_c.txt |
| [MEDIUM] Two-stage Ctrl-C | Queued results lost, or the summary missing | The handler only schedules `interrupt()`; first call cancels workers, later calls consumers; sink closes inside the handler's span | mutant tests (first-interrupt drain, close span); evidence/linux_ctrl_c.txt |
| [MEDIUM] Retries, timeouts, limits that decide what is dropped | Fetches retried forever, or good pages refused | Each bound holds at the bound and past it | evidence/boundary.txt; upper-bound tests |
| [LOW] CI workflow (new) | CI red or overprivileged | `permissions: contents: read`, `persist-credentials: false`, `uv sync --locked`, `pull_request` only | unproven on GitHub until the first run after push; the same commands pass locally (checks.txt) |
| [LOW] Dependencies: httpx (runtime); pytest, pytest-asyncio, pytest-cov, ruff, mypy (dev) | Supply-chain risk | One runtime dependency, all hash-locked in uv.lock | `grep -c "hash = " uv.lock`; security.md (no typosquats) |

### Notes on the cleared items

- The CI workflow has not run on GitHub yet; the PR's first run proves it.
- The design changed after approval in two ways, both approved by the author: the wording updates, and the connected-address check (a stricter answer to a gap the design had accepted). Redirects are now followed by linkx rather than httpx, which matches the design's original intent; the design text says so.
- Simplicity suggestions applied: one definition of default ports, CLI defaults read from `Settings`, `Summary` fields documented by stage, `FetchResult` invariant enforced, `main()` split, clearer names and comments.
