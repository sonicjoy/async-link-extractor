# Simplicity review

**Overall:** The code is small, well commented and consistent. 187 tests pass and ruff is clean. Nothing is dead and nothing is over-built beyond the approved design. The findings are duplicated definitions, one fact stated in three places, an invariant that fails silently, and a few points a line-by-line walkthrough will ask about.

## Suggestions

### [SHOULD] One definition of `DEFAULT_PORTS`
- **Where:** `src/linkx/cli.py:28`, `src/linkx/fetch.py:26`
- **Why:** The same dict is defined in both modules. In `fetch.py` it also serves as the scheme allow-list (`validate_url`), so a contributor who edits that copy will not touch the dedupe key in `cli.py`. Nothing ties the two.
- **Proposed:** Delete the copy in `cli.py` and import it: `from linkx.fetch import DEFAULT_PORTS`.
- **Behaviour change:** none

### [SHOULD] CLI defaults repeat the `Settings` defaults
- **Where:** `src/linkx/cli.py:68-101` vs `src/linkx/models.py:137-148`
- **Why:** `concurrency=10`, `consumers=1`, `queue_max=100`, `"drop-oldest"`, `timeout=10.0`, `retries=2`, `max_bytes=2_000_000` and `per_host_delay=0.0` are written twice, and the README options table is a third copy. Changing one makes the CLI and the library disagree, and no test fails.
- **Proposed:** In `cli.py`, `DEFAULTS = Settings()` and then `default=DEFAULTS.concurrency` and so on. Optionally add a test that `Settings(...)` built from `parse_args(["http://x"])` equals `Settings()` for the shared fields.
- **Behaviour change:** none

### [SHOULD] `Summary` is completed in three places
- **Where:** `src/linkx/sink.py:311-319`, `src/linkx/pipeline.py:94-96`, `src/linkx/cli.py:299`
- **Why:** The sink builds it, the pipeline fills `max_queue_depth` and `duration_s` with `replace`, and the CLI fills `duplicates_skipped` with `replace`. That is why three fields carry placeholder defaults (`0`, `0`, `0.0`) that a reader can mistake for real values.
- **Proposed:** Smallest fix: a comment on `Summary` saying which stage fills which field. Cleaner: `Sink.close` returns only the counting part, and `Pipeline.run` builds the whole `Summary`. `duplicates_skipped` stays a CLI-side `replace`, since the pipeline never sees duplicates.
- **Behaviour change:** none

### [SHOULD] The `html is None` half of the error check hides a broken invariant
- **Where:** `src/linkx/pipeline.py:145`, `src/linkx/models.py:49`
- **Why:** `FetchResult` says exactly one of `html` and `error` is set, but nothing enforces it. `if result.error is not None or result.html is None` quietly turns a result with neither into a `fetch_error` record whose `error` is null. The second condition exists to satisfy the type checker, not to handle a real case.
- **Proposed:** Test `result.html is None` only, with `assert result.error is not None` inside the branch, or enforce the invariant in `FetchResult.__post_init__`. Either way a broken invariant fails loudly.
- **Behaviour change:** yes, only for an impossible input: it raises instead of writing a record with a null error.

### [SHOULD] Tautological test
- **Where:** `tests/test_overflow.py:71-72`
- **Why:** `{"drop-oldest": drop_oldest, "block": block} == POLICIES` restates the table in the test. The name claims "the two CLI names" but never looks at the CLI. The parametrized test below it already exercises both entries.
- **Proposed:** Delete it, or replace it with the relation that matters: `--queue-policy` choices from `build_parser()` equal `set(POLICIES)`.
- **Behaviour change:** none (test only)

### [COULD] `queue_policy` is a free string in `Settings`
- **Where:** `src/linkx/models.py:141`, `src/linkx/pipeline.py:50`
- **Why:** argparse guards the CLI, but `Settings(queue_policy="drop_oldest")` from a library caller or a test fails only at `POLICIES[...]` with a bare `KeyError`.
- **Proposed:** Annotate as `Literal["drop-oldest", "block"]`, so the type checker catches the typo. No new type needed.
- **Behaviour change:** none

### [COULD] `main()` does five jobs
- **Where:** `src/linkx/cli.py:243-301`
- **Why:** Mapping args to `Settings`, reading input, opening output, running with its error mapping, and summarising live in one 60-line function with five exit paths. This is the function that will be read aloud.
- **Proposed:** Extract `_settings_from(args) -> Settings` and `_run(jobs, settings, stream, transport) -> int` (the try/except/finally block). `main` then reads as a list of steps.
- **Behaviour change:** none

### [COULD] Undocumented `KeyboardInterrupt` branch
- **Where:** `src/linkx/cli.py:265-266`
- **Why:** This catches Ctrl-C while an input file is read, before the pipeline installs its SIGINT handler. The branch at line 286 has a comment, this one does not, and the reason is not obvious.
- **Proposed:** One line: `# Ctrl-C while reading input: the pipeline's handler is not installed yet.`
- **Behaviour change:** none

### [COULD] Two clocks and two sleeps for one fetch
- **Where:** `src/linkx/fetch.py:337-354`, `src/linkx/ratelimit.py:225-231`
- **Why:** `Fetcher` times with `time.perf_counter` and sleeps with its own `sleep`; its `HostLimiter` times with `time.monotonic` and has its own `sleep`. A test that fakes one must remember to fake the other, and a reader will ask why they differ.
- **Proposed:** A comment on `Fetcher.__init__` saying the keyword-only arguments exist so tests run with no network or real waits, and that `limiter` carries its own clock and sleep. Use `time.monotonic` in both defaults if no test depends on the difference.
- **Behaviour change:** none

### [COULD] Hook failures carry no `final_url` or `http_status`
- **Where:** `src/linkx/fetch.py:502-527` vs `_read_page` at `fetch.py:465-476`
- **Why:** Failures from `_read_page` carry the response's URL and status. Failures from the request hook (a blocked redirect hop, a resolve failure on a hop) carry neither, so the record shows `final_url: null` for a redirect that was blocked. Both paths raise `FetchFailure`, and this difference is easy to miss reading either alone.
- **Proposed:** State it in the `_check_request` docstring. Do not change the record shape in this MR.
- **Behaviour change:** none

### [COULD] Names and wording
- `src/linkx/pipeline.py:99,106`: the TaskGroups are bound as `consumers` and `workers`, while `self._consumers` and `self._workers` hold the tasks. Rename the groups `consumer_group` and `worker_group`.
- `src/linkx/pipeline.py:30`: `PageFetcher` has one production implementation and exists for the `Pipeline` tests. Keep it, and add a one-line docstring saying so.
- `README.md:36`: the sample log line says "1 consumers". The `start` log line and `format_summary` also word the duplicate count differently ("(0 duplicates skipped)" vs "(N duplicate(s) skipped)"). Pick one wording.

## Out of scope (for follow-up)
- `Settings.max_redirects` and `progress_interval` are library-only settings. The README options table could say so.
- `Sink.emit` has two bug checks (unknown job, second record). Both are cheap and I would keep them.
