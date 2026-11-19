# Fix review, round 1

**Verdict:** FAIL

Scope: `fixes-round1.patch` = `f51d6b3..ffc8cec` (byte-identical to `git diff f51d6b3 ffc8cec`). While this review ran, two more commits landed on the branch (`a36f258` connected-peer check, `4416694` docs). They are not in this patch and were not reviewed. The two MAJOR findings below were re-run on `4416694` and still reproduce there. Note that `decisions.md`'s "Waiting for the user" list (DNS rebinding, proxies) is now out of date with `a36f258`.

Baseline: 223 passed on `ffc8cec`; ruff, ruff format and mypy are clean. All experiments wrote only under the scratchpad and used localhost only.

The FAIL rests on two MAJOR findings. (1) The decompression bound covers only the final response: a redirect's body is still inflated by httpx with no limit (400 MB peak for a 200 KB gzip). (2) The sink's new failed flag hides the real output error from the CLI, which also skips the broken-pipe handling.

## Findings claimed fixed

| Finding | Property (one sentence) | Status | Evidence |
|---|---|---|---|
| Non-UTF-8 input exits 1 (correctness MAJOR) | Unreadable input of any kind is a usage error: exit 2, no traceback. | resolved, with one sibling case — the new `isfinite` check raises `OverflowError` on a huge integer option (exit 1 with a traceback; new finding 3) | `cli.py:307-310`. The `no-unicode-catch` mutant fails `test_input_file_that_is_not_utf8_exits_2` (UTF-16 with BOM, and Latin-1). |
| First Ctrl-C drain untested (correctness MAJOR) | After the first interrupt, every result already queued is parsed and written. | resolved | `test_first_interrupt_still_parses_every_queued_result`. A mutant whose first interrupt cancels consumers too fails it (`['cancelled',...] == ['ok',...]`). |
| Ctrl-C during `Sink.close` loses the summary | A late Ctrl-C never removes the summary or cuts a closing record. | resolved for `Sink.close`; instance only — `extract`'s `async with make_client` exit (`pipeline.py:231-233`) still runs after the handler is restored. A press during client close still gives exit 130 with no summary. The window is small (reasoned, not run). | `pipeline.py:97` is now inside the `with`. **No test fails if it is moved back out**: the `close-outside-span` mutant passes all 223. |
| Records written after an output failure | No record is written after a failed write. | resolved, but the fix introduces new finding 2 | `sink.py:48-49,60-65`. The `sink-no-failed-check` mutant fails `test_an_output_failure_stops_all_further_writes` and `test_nothing_is_written_after_a_failed_write`. |
| Non-text charset breaks decoding | A server-chosen charset never fails the fetch; unusable ones fall back to UTF-8. | instance only — `charset=idna` and `charset=undefined` raise `UnicodeError`, not `LookupError` | Scan of 507 codec names and aliases through `_decode`: exactly these two raise. Through `Fetcher.fetch`: `idna` gives `UNEXPECTED 'UnicodeError: Unsupported error handling: replace'` and `undefined` gives `UNEXPECTED 'UnicodeError: undefined encoding'`. New finding 4. |
| `nan`/`inf` pass validation | Numeric options are finite and within bounds. | resolved (floats) | `cli.py:46-47`. The `no-isfinite` mutant fails `test_non_finite_numbers_are_usage_errors` (`deadline must not be NaN`). |
| README "every input URL" | The README states the output contract accurately. | resolved | README now says "every job" and explains duplicates. |
| Gzip bomb about 736 MB before the cap (security MEDIUM) | Memory held per fetch stays near `max-bytes` decoded, whatever the server sends (design line 234). | **instance only — redirect responses**: httpx `aread()`s every 3xx body with its own unbounded decoder before following it | New finding 1: 302 with a 200 KB gzip body gives **400.1 MB peak**, outcome `ok`; the same body on a 200 gives 10.9 MB and `too_large`. For the 200 path: 300 randomized sizes, encodings and chunkings gave 0 mismatches; limit 1000 accepts 1000 and refuses 1001. |
| U+2028/2029/0085 raw | One record is exactly one line under any line-splitting rule. | resolved | `models.py` `translate(_LINE_BREAKS)`. The mutant fails `test_unicode_line_separators_are_escaped`. |
| `is_public` NAT64, `::a.b.c.d`, two ranges | An address is accepted only if the IPv4 or IPv6 host it reaches is public unicast. | resolved for the claimed forms | All three branches are mutation-killed (`no-nat64`, `no-v4compat`, `no-extra-ranges`). On both 3.13.18 and 3.15.2, `64:ff9b::808:808`, `::ffff:8.8.8.8`, `1.1.1.1` and `2606:4700::1111` stay public; `64:ff9b:1::/48`, `64:ff9b::c000:9` and `::ffff:192.88.99.1` are refused. No public address is newly refused. Still accepted, outside the claim: `5f00::1` (listed by security, not asked for in its fix) and operator-specific NAT64 prefixes (undetectable). |
| CI token persisted | Checkout does not leave the token in `.git/config`. | resolved | `persist-credentials: false`. |
| `uv run linkx --help` not in CI | The console script is exercised in CI. | resolved | ci.yml Install step. |
| Test gaps (timeout retried, allow-private via redirect, flag wiring, CUJ summaries) | — | resolved | `timeout-not-retried` mutant fails `test_attempt_timeout_is_retried`; `test_every_option_reaches_the_settings` drives every flag; the CUJ 1 and CUJ 3 summary asserts are present. |
| Simplicity SHOULD/COULD items | — | resolved | `DEFAULT_PORTS` imported; `DEFAULTS = Settings()` plus an agreement test; `Summary` docstring; `__post_init__` invariant (the `postinit-off` mutant fails); policy test compares against the CLI; `Literal` type; `main` split; comments and names. |

## New behaviour introduced by the fixes

| Hunk | What now happens | Who is affected, and are they told |
|---|---|---|
| `fetch.py` `_read_body`: unsupported `Content-Encoding` | `br`, `zstd`, stacked encodings: `fetch_error`/`unexpected`, not retried | Servers that ignore `Accept-Encoding`. Told in the record; decided in `decisions.md`. |
| `_read_body`: raw (headerless) deflate | Was decoded by httpx's fallback; now `unexpected "corrupt deflate body: incorrect header check"` | Old IIS-style servers. Told in the record; decided. |
| `_read_body`: multi-member gzip, trailing junk, truncated stream | First member only; junk ignored; truncated gives what was decoded | Same as httpx (checked side by side). No regression. |
| `_read_body`: `is_stream_consumed` path | Pre-read responses (custom transports only) are capped after the fact | Tests and library users with a custom transport. Fine. |
| `make_client`: `Accept-Encoding: gzip, deflate` pinned | `br`/`zstd` never requested | No one, in practice. The test asserting it passes without the pin here (httpx's default is identical when brotli and zstandard are absent): the `no-accept-encoding` mutant passes. |
| `Sink.emit` failed flag | Every emit after a failed write raises `RuntimeError` | Operators: the real error message can be replaced (new finding 2). |
| `pipeline.run` close inside the span | A Ctrl-C during close schedules `interrupt()`, which runs after `run` returns: it logs "interrupted: stopping fetches…" after the run is over, with summary `interrupted=False` and exit 0 | Cosmetic (reasoned). |
| `cli._bounded` `isfinite` | Integer options of 309 or more digits crash | New finding 3. |
| `cli.main` decode error | Message is `cannot read input: not UTF-8` with **no file name** | Users who pass several input files cannot tell which one failed. Minor; add `exc`'s file or the source name. |
| `FetchResult.__post_init__` | Raises `ValueError` for neither or both of html and error | Only an impossible state; `Fetcher._result` always sets one. |
| Log wording (`start`, `summary`) | Text changed (`duplicates skipped: N`, `N jobs, 1.74s`) | Plain-text log scrapers; JSON fields are unchanged. |

## New findings

### [MAJOR] Redirect bodies bypass the bounded decompression and `max-bytes`
- **Where:** `src/linkx/fetch.py:97-116` (`follow_redirects=True`) together with httpx `_client.py:1700-1708` (`if follow_redirects: await response.aread()`); claim at `README.md` ("A 50 MB gzip bomb is refused while holding under 5 MB") and design line 234 (at most N + queue-max + M bodies of up to `max-bytes` each).
- **Problem:** `_read_body` bounds only the final response. httpx fully reads every 3xx body, decoded by its own `GZipDecoder`/`DeflateDecoder` with no limit and no cap, and keeps it in `response.history` until the fetch ends (up to 9 hops). The security finding's property, memory per fetch near `max-bytes`, still fails through this route: a hostile URL list that 302s with a gzip bomb gets the old amplification back, about 10× per worker at default concurrency. An uncompressed large 3xx body is likewise unbounded except by the timeout.
- **Evidence:** localhost `ThreadingHTTPServer`, one job, `max_bytes=2_000_000`, a 200 MB-of-zeros gzip body (~200 KB), traced with `tracemalloc`. Same result on `ffc8cec` and `4416694`:
  ```
  /bomb200 peak MB 10.9   outcome fetch_error too_large
  /bomb302 peak MB 400.1  outcome ok (followed to /small)
  ```
- **Confidence:** high
- **Suggested fix:** follow redirects in `_attempt` yourself: `follow_redirects=False`, `client.send(..., stream=True)`, then `aclose()` each 3xx without reading it and send `response.next_request`. The request hook and the new response hook still run on every hop. Add a test with a 302 carrying a gzip bomb and a peak-memory assertion.

### [MAJOR] The sink's failed flag hides the real output error, and the CLI no longer silences a broken stdout
- **Where:** `src/linkx/sink.py:48-49` together with `src/linkx/cli.py:246-250` (`_first_cause` takes `exceptions[0]`) and `cli.py:288-290`.
- **Problem:** Tasks that run in the same loop iteration as the failing write now raise `RuntimeError("output failed earlier…")`. TaskGroup collects errors in completion order, and a nested worker group's exception is appended after the outer group's own task errors, so the first leaf is often that `RuntimeError` rather than the `OSError`. The CLI then logs `run failed: output failed earlier; no further records are written`, and the cause ("disk full", "Broken pipe") never reaches the operator. `isinstance(cause, BrokenPipeError)` is also false, so `_silence_stdout()` is skipped. Its own docstring explains what then happens: the interpreter's final flush of stdout fails again, which on POSIX turns `linkx … | head -1` into an "Exception ignored … BrokenPipeError" message and exit 120 instead of 1. That last step is reasoned, not run; Windows has no `SIGPIPE`.
- **Evidence:**
  - `Pipeline` with a stream that raises `BrokenPipeError` from write k on (k 1–20, 50 jobs, random workers, consumers and queue): `177 BrokenPipeError` and `123 RuntimeError: output failed earlier` as the first leaf, out of 300.
  - `cli.main` with 60 URLs (MockTransport), queue 1, a broken `sys.stdout`, 87 runs: on `ffc8cec`, **87/87** logged `run failed: output failed earlier…` and `_silence_stdout` was called 0 times. On `f51d6b3` the same script gave 87/87 `run failed: [Errno 32] Broken pipe`, with silencing called every time.
  - `test_output_failure_exits_1` does not catch this because it fetches one URL, so there is only one write.
- **Confidence:** high (message and skipped silencing); medium (the exit-120 consequence).
- **Suggested fix:** keep the original exception in the sink (`self._failure = exc`) and raise a dedicated `OutputFailed(...) from self._failure`. In `_first_cause`, walk all leaves and prefer one that is not `OutputFailed` (or unwrap its `__cause__`). Add a CLI test with several URLs and a `BrokenPipeError` stdout that asserts the message and that `_silence_stdout` is called.

### [MINOR] A very large integer option crashes with a traceback (exit 1) instead of exit 2
- **Where:** `src/linkx/cli.py:46` (`math.isfinite(value)` on an `int`).
- **Problem:** `math.isfinite` converts to float; an int of 309 or more digits raises `OverflowError`. argparse catches only `TypeError`, `ValueError` and `ArgumentTypeError`, so the error escapes. Before the fix this value was accepted. This is the same class as the non-UTF-8 MAJOR: an input error of an unexpected exception type escapes the usage-error path.
- **Evidence:** `python -m linkx http://a.test/ --retries 1000…0` (400 zeros) gives `OverflowError: int too large to convert to float`, exit 1.
- **Confidence:** high
- **Suggested fix:** apply `isfinite` only to floats (`isinstance(value, float)`), or catch `OverflowError` there as well.

### [MINOR] `charset=idna` and `charset=undefined` still fail the fetch
- **Where:** `src/linkx/fetch.py:119-125` (`except LookupError`).
- **Problem:** Both codecs raise `UnicodeError` even with `errors="replace"`, so the page becomes `unexpected` instead of falling back to UTF-8. This is the original finding's property, reached through a sibling exception type.
- **Evidence:** given in the table above. `UnicodeError: Unsupported error handling: replace` and `UnicodeError: undefined encoding`, attempts 1.
- **Confidence:** high
- **Suggested fix:** `except (LookupError, UnicodeError)`; add `idna` and `undefined` to the parametrized test.

### [MINOR] Test gaps on two load-bearing lines
- The `+ 1` in `_inflate`'s `max_length` (`fetch.py:357`) is what guarantees no output is left inside zlib when a body is exactly at the cap. The mutant `max(room - len(out), 1)` passes all 223 tests. Suggested test: a body of exactly `limit` decoded bytes whose final step fills `max_length`, checking the full content is returned.
- Closing the sink inside the SIGINT span: the `close-outside-span` mutant passes all 223 tests. A test could replace `Sink.close` with one that calls `signal.raise_signal(SIGINT)` and assert that `run` still returns a summary.

## Unclaimed changes

- `fail()` in `_read_page` lost its keyword-only `*`, so it matches `Callable[[ErrorKind, str], FetchFailure]`. Safe.
- `test_attempt_timeout` became `test_attempt_timeout_is_retried` with `retries=1`; the case `retries=0` is no longer tested on its own. Harmless.
- `harness` now re-wraps every bytes response as a raw stream (`as_network_response`), so all fetch tests exercise the streaming path. This is an improvement; it is the reason the bomb test is meaningful.

All are safe.

## Findings with no fix

None. Every blocker or major finding in the three reports is fixed, decided, or on the waiting list. The correctness MINOR "bad Location retried as connect" is decided with a recorded reason. The security LOWs marked "note and skip" are intentional.

## Waiting for the owner

- DNS rebinding (security HIGH): the list says the design accepts the gap explicitly. That is true of `docs/designs/linkx.md:113` at `ffc8cec`. Commit `a36f258`, which landed after this patch, adds a connected-peer response hook for it. The waiting list is now stale and that commit needs its own review.
- Environment proxies (security LOW): accurate at `ffc8cec` (`trust_env` left at its default). `a36f258` sets `trust_env=False`, so this item is also stale.
- Design wording drift (correctness table): `4416694` edits the design. Not reviewed here.

## The same class again

- **An input or response error escapes through an exception type nobody listed.** This covers non-UTF-8 input (`UnicodeDecodeError`, original), a huge integer option (`OverflowError`, new), and the `idna`/`undefined` charsets (`UnicodeError`, new). Each fix caught exactly the reported type. For the CLI, the single enforcement point is the `_bounded` parser plus `read_urls`: convert any `Exception` raised while interpreting user input into a usage error. For decoding, `_decode` can catch `(LookupError, UnicodeError)`.
- **A bound enforced in our reader while httpx reads bodies elsewhere.** Redirect bodies (new finding 1) are the same class as the original gzip finding. The single place to enforce it is to make `_read_body` the only code that ever reads a body, which means following redirects in `_attempt`.
