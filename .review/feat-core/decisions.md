# Decisions

## Triage of review findings (round 1)

| Finding | Source | Bucket | Action |
|---|---|---|---|
| Non-UTF-8 input file exits 1 with a traceback | correctness MAJOR | fix now | catch decode errors as unreadable input, exit 2 |
| No test proves the first Ctrl-C drains queued results | correctness MAJOR | fix now | test with results queued behind a slow parser |
| Ctrl-C during `Sink.close` loses the summary | correctness MINOR | fix now | close the sink while the handler is still installed |
| Records written after an output failure | correctness MINOR | fix now | the sink refuses every emit after a failed write |
| Non-text charset makes decoding fail | correctness MINOR | fix now | decode once at the end, fall back to UTF-8 on LookupError |
| `nan` / `inf` pass option validation | correctness MINOR | fix now | reject non-finite numbers |
| README says every input URL gets a record | correctness MINOR | fix now | say every job; point to the duplicates count |
| Test gaps: timeout path retried, allow-private through a redirect, CLI flag wiring, CUJ 1/3 summary | correctness notes | fix now | tests added |
| `uv run linkx --help` not in CI | correctness plan drift | fix now | CI step |
| Bad `Location` header retried as `connect` | correctness MINOR, design finding | decide and record | see below |
| Design wording drift (6 rows) | correctness drift table | ask the user | the design is Approved; proposed wording goes to the user |
| DNS rebinding (check and connect resolve separately) | security HIGH, design finding | ask the user | the design accepts the gap explicitly |
| Gzip bomb peaks at about 736 MB before the cap | security MEDIUM | fix now | bounded decompression; the design's memory bound already promises at most `max-bytes` decoded per body |
| U+2028, U+2029, U+0085 raw in JSON lines | security LOW | fix now (one line) | escape them |
| `is_public` misses NAT64, `::a.b.c.d`, `192.0.0.0/24`, `192.88.99.0/24` | security LOW | fix now (small, core guarantee) | unwrap embedded IPv4, block the two ranges |
| Environment proxies trusted | security LOW | ask the user | bundled with the DNS rebinding question: a peer check would block a private proxy |
| Control characters in plain-text logs | security LOW | note and skip | ledger follow-up |
| Userinfo and tokens echoed to output and logs | security LOW | note and skip | ledger follow-up |
| Slow DNS ties up the shared thread pool | security LOW | note and skip | ledger follow-up |
| `--out` / input files follow symlinks | security LOW | note and skip | operator-controlled; no fix |
| CI actions tag-pinned; checkout persists the token | security LOW | fix part now | `persist-credentials: false`; SHA pinning is a ledger follow-up |
| `DEFAULT_PORTS` defined twice | simplicity SHOULD | fix now | import from `fetch` |
| CLI defaults repeat `Settings` defaults | simplicity SHOULD | fix now | defaults read from `Settings()`; test that they agree |
| `Summary` completed in three places | simplicity SHOULD | fix now | docstring says which stage fills which field |
| `html is None` hides a broken invariant | simplicity SHOULD | fix now | `FetchResult.__post_init__` enforces exactly one of html and error |
| Tautological policy-table test | simplicity SHOULD | fix now | compare with the CLI's choices instead |
| `queue_policy` free string; `main()` does five jobs; comments; clock defaults; TaskGroup names; README wording | simplicity COULD | fix now | all small, all in code being walked through |
| `Settings.max_redirects` / `progress_interval` library-only | simplicity follow-up | note and skip | README note |

## Decisions made

| Decision | Alternative | Why this one | To switch |
|---|---|---|---|
| A bad `Location` header stays `connect` and is retried | Map `httpx.RemoteProtocolError` to a non-retried kind | The same exception also covers a server that drops the connection mid-response, which is transient; a malformed redirect is rare and costs only R extra attempts | `fetch.py`, the `except httpx.TransportError` branch in `_attempt` |
| Unsupported `Content-Encoding` (for example `br`) is a `fetch_error` of kind `unexpected` | A new error kind such as `unsupported_encoding` | The record's error kinds are a published contract; `linkx` asks only for gzip and deflate, so a compliant server never sends anything else | `fetch.py`, `_read_page` |
| Raw (headerless) deflate is not decoded | Retry with a raw-deflate decompressor | Rare in practice; a wrong guess is reported, not hidden | same place |
| `Accept-Encoding` pinned to `gzip, deflate` | httpx's default, which adds `br`/`zstd` when those packages happen to be installed | The bounded decoder handles exactly what is asked for | `make_client` |
| U+2028, U+2029 and U+0085 escaped in output | `ensure_ascii=True` everywhere | Keeps other non-ASCII text readable | `Record.to_json` |

## Decided by the user (2026-11-19)

| Question | Answer | Done in |
|---|---|---|
| DNS rebinding and environment proxies (security HIGH, LOW) | Check the connected IP in a response hook; `trust_env=False` | 32c069c |
| Design wording to match the code (correctness drift table, 8 rows) | Update the design | 5733556, plus the redirect-following sentence in 87700d9 and fix-set-3 wording in 1591d2c |
| The connected-IP check fails open when the peer address cannot be read (fix review round 2, MAJOR, at the two-round cap) | Fail closed | ca5e3a3 |
| Four minors from fix review round 2 | Fix all four | ca5e3a3, 1591d2c |

## Fix review round 1 findings (fixed in round 2)

| Finding | Action |
|---|---|
| MAJOR: redirect bodies bypass the size cap (httpx reads 3xx bodies) | linkx follows redirects itself and closes each 3xx unread (87700d9) |
| MAJOR: the sink's failure flag hides the real output error | `OutputFailed` chained to the first failure; CLI reports the first non-refusal (b8ff835) |
| MINOR: huge integer option crashes `math.isfinite` | finite check on floats only (b8ff835); upper bounds later (1591d2c) |
| MINOR: `idna` / `undefined` charsets still fail | fallback also catches ValueError; test over every codec name (87700d9) |
| MINOR: untested load-bearing lines | tests for `_inflate` +1 and for Ctrl-C during `Sink.close` (87700d9, b8ff835) |
| MINOR: Ctrl-C during the HTTP client's close loses the summary | decide and record: left open; records are complete by then; ledger follow-up |
| MINOR: input error message for a non-UTF-8 file does not name the file | note and skip: ledger follow-up |
| MINOR: a late interrupt log line after the run ends | note and skip: ledger follow-up |
| `5f00::1` accepted by `is_public` | note and skip: ledger follow-up |

## Fix review round 2 findings (fixed in fix set 3, after the user's decision)

| Finding | Action |
|---|---|
| MAJOR: peer check fails open when the address cannot be read | fail closed (ca5e3a3); reproduced over real sockets: 40 of 40 leaked before, 40 of 40 refused after |
| MINOR: `trust_env=False` also ignores `SSL_CERT_FILE` / `SSL_CERT_DIR`, undocumented | README and design say so (1591d2c) |
| MINOR: `--max-bytes` ≥ 2^63 breaks zlib; huge `--concurrency` exhausts memory | upper bounds on every numeric option (1591d2c) |
| MINOR: design row 1 was wrong: a `javascript:` redirect gives `InvalidURL` | mapped to `invalid_url`, design corrected (ca5e3a3, 1591d2c) |
| MINOR: no test fails if 3xx responses stop being closed | end-to-end test with one worker (ca5e3a3) |

## Decisions made (fix sets 2 and 3)

| Decision | Alternative | Why this one | To switch |
|---|---|---|---|
| Upper bounds: 1,000 workers, 64 consumers, queue 1,000,000, 1 GB body, 10 retries, 3,600 s timeout and per-host delay | Other limits, or none | Far above real use; each stops a typo from hanging or exhausting memory | `build_parser` in `cli.py` |
| A transport with no `network_stream` skips the connected-address check | Refuse it too | httpx's own transport always has one, so the CLI is always checked; in-memory test transports and custom library transports that drop response extensions do not, and refusing would break every MockTransport test and the demo. Documented in the `extract` and `make_client` docstrings | `_check_response` in `fetch.py` |

## Fix review round 3 findings (fixed in fix set 4)

| Finding | Action |
|---|---|
| MINOR: an unreadable address was reported as "non-public" with an `--allow-private` hint | its own "could not confirm" message, no hint (26f47a7) |
| MINOR: README options table split by the bounds paragraph | paragraph moved below the table (26f47a7) |
| Nits: upper-bound tests ran `main()` and would hang on a regression; docstring, design sentence, test count; a mutant targeted the wrong line | tests parse only; wording fixed; mutant retargeted and re-run (26f47a7) |

## Fix review round 4

PASS, no findings. Housekeeping it noted (this table, the transport note in public docstrings) done in the review-record commit.

## Waiting for the user

Nothing.
