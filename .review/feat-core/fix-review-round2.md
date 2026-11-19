# Fix review, round 2

**Verdict:** FAIL

Scope: `fixes-round2.patch` = `49bf5b3..b8ff835` (byte-identical to `git diff 49bf5b3 HEAD`). Baseline on HEAD: **243 passed** in 6.3 s; `ruff check`, `ruff format --check` and `mypy` are clean. httpx 0.28.1, httpcore 1.0.9, Python 3.15.2 on Windows. Every experiment ran against localhost servers from a scratch folder; mutants ran on copies of `src/` and `tests/` in that folder, never in the repository.

The FAIL rests on one MAJOR. The connected-peer check is **fail-open**: when the peer address cannot be read, the check is skipped and the body is read. An internal service that answers and then resets the connection (RST) has its page parsed and its links written out. Measured: 40 of 40 records `ok` through `extract`, links included. Everything else claimed in this round is resolved. The manual redirect loop behaves like httpx's own following in every case I tried. Each new test fails when its fix is reverted, with one exception: nothing tests that a 3xx is closed.

## Findings claimed fixed

| Finding | Property (one sentence) | Status | Evidence |
|---|---|---|---|
| DNS rebinding (security HIGH; the author chose the response-hook option) | No byte returned by a non-public peer is read or written out, unless `--allow-private` is set. | **instance only: a peer whose address cannot be read is let through** (new finding 1) | `fetch.py:325-327`: `peer = ... if stream is not None else None` / `if peer and not is_public(...)`. anyio drops `remote_address` when `getpeername()` raises (`anyio/abc/_sockets.py:154-161`), and it raises once the peer has reset the connection. The check itself is right whenever the address is known: IPv4 `('127.0.0.1', p)` and IPv6 `('::1', p)` peers were both refused (exp2), a FIN close is refused 50/50, and HTTPS passes `server_addr` through `TLSStream.extra_attributes` (`anyio/streams/tls.py:295`). Mutants `no-response-hook` (3 tests fail) and `response-hook-ignores-allow-private` (2 fail) are killed. |
| Environment proxies (security LOW) | The connected address is the real server, never a proxy picked up from the environment. | resolved | `fetch.py:105` `trust_env=False`. The `trust-env` mutant fails `test_client_ignores_proxy_settings_from_the_environment`. Side effect listed under new behaviour. |
| Redirect bodies bypass the cap (round-1 MAJOR) | The only body linkx ever reads is the final one, through `_read_body`. | resolved | `fetch.py:108` `follow_redirects=False`; `_follow` `fetch.py:270-277` closes every response in `finally`. A 302 with a 20 MB body sent in full ×5, then a 302 with an endless chunked body: 0.06 s total, **0.7 MB peak**, both `ok`. The `httpx-follows` mutant fails `test_redirect_bodies_are_never_read` and `test_redirect_loop_is_too_many_redirects`. |
| Sink failure flag hides the real error (round-1 MAJOR) | The CLI reports the first output error, and silences stdout when that error is a broken pipe. | resolved | `sink.py:52` `raise OutputFailed(...) from self._failure`; `cli._root_cause` walks every leaf and prefers one that is not `OutputFailed`, falling back to `__cause__`. Every `emit` after a failure goes through the check, so the only non-`OutputFailed` leaf from a write is the original one. The `root-cause-first-leaf` mutant fails `test_the_first_output_error_is_the_one_reported` (the log shows `run failed: output failed earlier`) and `test_root_cause_prefers_the_first_real_failure`. The `no-cause-chain` mutant fails `test_nothing_is_written_after_a_failed_write`. |
| Huge integer option crashes (round-1 MINOR) | An option value never escapes the usage-error path with a traceback. | resolved for parsing; a sibling crash at use time is minor finding 3 | `cli.py:49` checks `isfinite` only for floats. The `isfinite-all` mutant fails `test_a_huge_integer_option_is_accepted_not_a_crash`. |
| `charset=idna` / `undefined` (round-1 MINOR) | A charset chosen by the server never fails the fetch. | resolved | `fetch.py:129` catches `(LookupError, ValueError)`. `test_every_known_codec_name_decodes_or_falls_back` covers every alias. The `decode-lookup-only` mutant fails 3 tests. |
| NUL in `--out` or in an input path (unclaimed in round 1, now fixed) | Any unusable path is a usage error, exit 2. | resolved | `cli.py:322,332`. The `nul-input` and `nul-output` mutants each fail their parametrized case. `read_urls` raises `ValueError` only from `open` or decoding, so the wider catch hides nothing else. |
| Test gap: `+ 1` in `_inflate` | Each zlib step stops at most one byte past the room. | resolved | The new `test_inflate_produces_at_most_one_byte_past_the_room` kills `room - len(out)` (a `max_length` of 0 means unlimited; 4 tests fail). The round-1 mutant `max(room - len(out), 1)` still survives, but 3,000 randomized bodies, limits and chunkings found **0** inputs where its result differs (exp5): the gzip/zlib trailer always arrives after the last output, so the next call drains anything still inside zlib. I treat that mutant as equivalent. |
| Test gap: sink closed inside the SIGINT span | A late Ctrl-C never reaches the handler that was installed before the run. | resolved | The `close-outside-span` mutant fails `test_ctrl_c_while_the_sink_closes_is_still_handled_by_the_pipeline`. |
| Design wording (8 rows in `design-wording.md`) plus the redirect sentence | The design describes the code as built. | resolved except row 1 (minor finding 4) | Rows 2-8 match `fetch.py`, `pipeline.py`, `sink.py` and `models.py`. Row 1 says `InvalidURL` cannot be reached during a send; it can. |

### The redirect loop, checked case by case (exp1, real localhost server, `allow_private=True`)

| Case | Result | Same as httpx following? |
|---|---|---|
| 8 and 9 redirects; 10 redirects | `ok` with 9 and 10 requests; `too_many_redirects` with 10 requests | yes (httpx raises on the 10th redirect) |
| `Location: page`, `/page`, `//127.0.0.1:p/page`, `?a=1` | resolved against the request URL; `final_url` is the last hop | yes (`_redirect_url`) |
| 303, 307, 308 | followed with GET (all requests are GET) | yes |
| 302 without `Location`; 300 and 304 with `Location` | `http_status` with that status, 1 request | yes (`has_redirect_location` covers 301/302/303/307/308 only) |
| `Location: ""` | the same URL again until `too_many_redirects` | yes |
| `Location: http://[::1` | `connect`, `RemoteProtocolError: Invalid URL in location header`, retried | yes, as decided |
| `Location: javascript:alert(1)`, `mailto:a@b.c` | **`unexpected`, `InvalidURL: For absolute URLs, path must be ...`**, not retried | yes (pre-existing), but contradicts the new design text (minor finding 4) |
| `Location: ftp://...` | `invalid_url` from the request hook | yes |
| Redirect to another host | `Host` header updated; no other header added or carried over | yes (`_redirect_headers`) |
| Request and response hooks on each hop | both run on every `send` (`_client.py:1691-1697`) | tests `..._blocked_at_the_hop` and `..._redirect_hop_that_connected_to_a_private_address_is_refused` |
| Timeout | the whole loop is inside `asyncio.timeout`; httpx's per-phase timeout reaches each hop through `extensions=request.extensions` | yes |
| An error while building the next request | raised inside httpx's `try`, which closes the response (`_client.py:1713-1715`) | yes |

## New behaviour introduced by the fixes

| Hunk | What now happens | Who is affected, and are they told |
|---|---|---|
| `make_client` `trust_env=False` | Besides proxies, httpx now also ignores `SSL_CERT_FILE`, `SSL_CERT_DIR` (`httpx/_config.py:34-37`) and `.netrc` | Operators who trust a private or corporate CA through `SSL_CERT_FILE`: their HTTPS sites now fail as `connect` and are retried R times. The README says only "Proxy settings from the environment are ignored". Minor finding 2. |
| `_check_response` | A response from a non-public peer becomes `blocked_address`, with no `final_url` or `http_status`, even on a 3xx hop | Users who fetch internal hosts by public name without `--allow-private`. Told in the record; the design states the missing fields. |
| `_follow` | Each 3xx is closed unread, so the connection is dropped instead of reused | A redirect costs a new connection. Not visible; fine. |
| `_follow` relies on the client's `follow_redirects=False` | A library user who hands `Fetcher` their own client with `follow_redirects=True` gets httpx reading 3xx bodies again and a `next_request` of `None` | Library users only; the CLI and `extract` always use `make_client`. Passing `follow_redirects=False` to `send` would remove the dependency (a note, not a finding). |
| `_bounded` int path | Ints of any size are accepted again | `--max-bytes` of 2^63 or more breaks every gzip page; minor finding 3. |
| `_root_cause` | Logs the first real leaf | Operators now see the real cause. |
| `main` catches `ValueError` | A NUL path gives `cannot read input: embedded null character`, exit 2 | Told. |

## New findings

### [MAJOR] The connected-peer check is skipped when the peer address cannot be read, so a rebinding target that resets the connection is read and written out
- **Where:** `src/linkx/fetch.py:325-327`
  ```python
  stream = response.extensions.get("network_stream")
  peer = stream.get_extra_info("server_addr") if stream is not None else None
  if peer and not is_public(str(peer[0])):
  ```
  together with `anyio/abc/_sockets.py:154-161` (`try: peername = getpeername() except OSError: peername = None`; `remote_address` is present only if that succeeded) and `httpcore/_backends/anyio.py:87-88`.
- **Problem:** `server_addr` is `None` whenever `getpeername()` fails when the hook runs, which happens once the peer has sent a TCP RST. A `None` peer passes the check. By then httpcore has usually read the whole small response into its buffer, so `_read_body` returns the internal page. The README's claim, "The second check stops anything the internal service returns from being read or written out", fails exactly when the internal service aborts its connection after answering. Load balancers, proxies and some application servers do this. The attacker does not choose this behaviour; any internal target that has it is open to the rebinding attack the fix was meant to stop. A check that cannot see the address should refuse, not pass.
- **Evidence:** a localhost server that sends a 200 HTML page and then closes with `SO_LINGER 0` (RST); the resolver tells the request hook the name is `93.184.216.34`.
  ```
  Fetcher, no delay, 50 fetches:   {'blocked_address': 24, 'ok (body read)': 26}  peers seen: ('127.0.0.1', p), None
  Fetcher, 50 ms earlier hook, 20: {'ok (body read)': 20}  peers seen: None     (IPv4 and IPv6 alike)
  extract(), 40 jobs, concurrency 4: Counter({'ok': 40})
    {"url": "http://localhost:60264/rst?2", "outcome": "ok", "links": ["http://localhost:60264/secret"]}
  ```
  The same server closing with FIN instead: 50/50 `blocked_address`. Scripts: `exp2.py`, `exp3.py`, `exp8.py` in the review's scratch folder. Observed on Windows; anyio's code path is the same on Linux, where `getpeername` gives `ENOTCONN` after a reset (reasoned, not run).
- **Confidence:** high on the mechanism and the reproduction; medium on how often real internal targets reset.
- **Suggested fix:** fail closed. If `network_stream` is present but `server_addr` is `None`, raise `blocked_address` ("connected address unknown"). This keeps `MockTransport` tests working, since they have no stream. A sturdier option is to take the peer at connect time through httpcore's `trace` extension (`connection.connect_tcp.complete` returns the stream while it is still connected). Add a test with a `FakeNetworkStream` whose `server_addr` is `None`, and ideally the RST server as an e2e test.

### [MINOR] `trust_env=False` also drops `SSL_CERT_FILE` / `SSL_CERT_DIR`, and nothing says so
- **Where:** `src/linkx/fetch.py:105`; `httpx/_config.py:34-37`; README "Non-public addresses" paragraph.
- **Problem:** The change was made for proxies, but it also stops httpx from reading the CA bundle named in the environment. On a network that relies on a private CA (TLS inspection, internal PKI), every HTTPS fetch now fails with a certificate error, recorded as `connect` and retried.
- **Evidence:** code above: `if trust_env and os.environ.get("SSL_CERT_FILE")`. Not run (it would need TLS to a non-localhost host).
- **Confidence:** high (code).
- **Suggested fix:** document it in the README, or build the SSL context yourself from `SSL_CERT_FILE` / `SSL_CERT_DIR` and pass it as `verify=`.

### [MINOR] An accepted huge `--max-bytes` makes every gzip/deflate page fail; a huge `--concurrency` exhausts memory
- **Where:** `src/linkx/cli.py:49` (ints are no longer bounded by the float conversion) and `fetch.py:398` (`inflate.decompress(data, room - len(out) + 1)`); `pipeline.py:117` (`range(self._settings.concurrency)`).
- **Problem:** The round-1 fix moved the failure from parse time to use time. zlib's `max_length` must fit in a C `ssize_t`. Values from 2^63 up were already accepted in round 1; this round removes the ceiling of about 10^308.
- **Evidence:** `main(["http://a.test/", "--max-bytes", "9"*400, ...])` with a gzip page gives exit 0 and the record `{'kind': 'unexpected', 'message': 'OverflowError: Python int too large to convert to C ssize_t'}` (exp4). `--retries` and `--queue-max` with the same value work.
- **Confidence:** high.
- **Suggested fix:** give `_bounded` a `maximum`: for example `max-bytes` ≤ 2^31, `concurrency` and `consumers` ≤ a few thousand.

### [MINOR] Approved design row 1 is wrong: `InvalidURL` is reachable during a send
- **Where:** `docs/designs/linkx.md:74` ("URLs are validated before any request, and an unparseable redirect `Location` reaches `linkx` as `httpx.RemoteProtocolError`, so it is `connect` and retried"); the same claim in `_follow`'s docstring (`fetch.py:268`).
- **Problem:** A `Location` that has a scheme but no host (`javascript:alert(1)`, `mailto:a@b.c`) fails inside `copy_with` with `httpx.InvalidURL`, which is not converted. The record is `unexpected` and not retried. The behaviour predates this round, but this round's text now says it cannot happen.
- **Evidence:** exp1: `/js status=None err=('unexpected', "InvalidURL: For absolute URLs, path must be empty or begin with '/'")`.
- **Confidence:** high.
- **Suggested fix:** in `_attempt`, map `httpx.InvalidURL` to `invalid_url` (one `except` clause, which is what the original design said), or correct the sentence.

### [MINOR] No test covers closing a 3xx
- **Where:** `src/linkx/fetch.py:276-277`.
- **Problem:** If the `finally: await response.aclose()` is skipped for redirects, every redirected fetch holds its connection until garbage collection. With the pool sized to `--concurrency`, every later fetch then times out. All 243 tests still pass.
- **Evidence:** mutant `no-aclose-3xx` (close only when `next_request is None`): 243 passed. Against a real server with `concurrency=1`, the same mutant gives three `timeout ... within 2.0s` in a row; the real code gives three `ok` in 0.06 s (exp6).
- **Confidence:** high.
- **Suggested fix:** an e2e test with a real server, `concurrency=1` and a few redirect fetches in a row, asserting all `ok` well inside the timeout.

## Unclaimed changes
- `Fetcher.__init__` now builds `event_hooks` with only `request` and `response` instead of `{**hooks, ...}`. httpx has only those two keys, so this is safe.
- `test_client_ignores_proxy_settings_from_the_environment` and the `FakeNetworkStream` helpers. Safe.
- The README's follow-up table gains "Proxy support" and "Logged URLs" rows, which record security LOWs as follow-ups. Accurate.
- `design` status line: "Approved (wording updated 2026-11-19 ..., approved by the author)". Matches the approval described in the brief.

## Findings with no fix
These round-1 items, all MINOR, were neither fixed nor recorded in `decisions.md`. None blocks.
- A Ctrl-C during `extract`'s client close (`pipeline.py:231-233`) still gives exit 130 with no summary. The window is small.
- `cannot read input: not UTF-8` does not name the file.
- A late Ctrl-C during `Sink.close` logs "interrupted: stopping fetches…" after the run ends. Cosmetic.

## Waiting for the owner
- `decisions.md` has no round-2 section. Its "Waiting for the user" list still names DNS rebinding, environment proxies and the design wording, and all three have now been decided. Record the choice made: the response-hook check, knowing the request still reaches the internal address; `trust_env=False`; wording approved. Also record the trade-offs this round surfaced: minor findings 2 and 4, and the fail-closed question in finding 1.
- DNS pinning (stopping the request itself) is correctly listed as a README follow-up and is out of scope by the author's choice.

## The same class again
- **A security check that treats "could not tell" as "safe".** The peer check passes when `server_addr` is `None` (finding 1). It is the same shape as the original rebinding gap: the decision rests on information that may not describe the real connection. The single place to enforce it is `_check_response`: no known public peer means no body, except when there is no network stream at all (an in-memory transport).
- **A bound or option enforced at one layer while another layer reads the same input differently.** Huge ints pass the parser but overflow zlib (finding 3). This is the round-1 "unlisted exception type" class moved one step down. Enforce it once with upper bounds in `_bounded`.
