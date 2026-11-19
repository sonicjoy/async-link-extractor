# Fix review, round 3

**Verdict:** PASS WITH ISSUES

Scope: `fixes-round3.patch` = `b8ff835..1591d2c` (byte-identical to `git diff b8ff835 HEAD`). All runs were on a `git archive HEAD` copy in my scratch folder, against localhost servers only. Baseline: **254 passed** in 7.1 s; `ruff check`, `ruff format --check` and `mypy` are clean. httpx 0.28.1, httpcore 1.0.9, Python 3.15.2, Windows.

The fail-closed check holds. I could not get a body past it on any real-socket path I tried: HTTP and HTTPS, IPv4 and IPv6, reused keep-alive connections, redirect hops, and resets. It also refuses no healthy connection: 900 fetches over healthy connections whose peer is treated as public all came back `ok`. The cost is that a public server that resets the connection straight after answering is now refused. The message says "non-public address unknown" and suggests `--allow-private` (minor finding 1). All four minors are fixed, and each new test fails when its fix is reverted. One author mutant hit the wrong line, but my own mutant of the right line is killed. The README options table is now broken in two (minor finding 2).

## Findings claimed fixed

| Finding | Property (one sentence) | Status | Evidence |
|---|---|---|---|
| MAJOR: peer check fails open when the address cannot be read | No byte from a connection whose peer is not known to be public is read or written out, unless `--allow-private` is set. | resolved (for the default transport; see the note under "Waiting for the owner") | `fetch.py:334-336`: `address = str(peer[0]) if peer else None` / `if address is None or not is_public(address): raise`. httpcore puts `network_stream` on every HTTP/1.1 and HTTP/2 response (`http11.py:129`, `http2.py:161`), and the upgrade wrapper delegates `get_extra_info` (`http11.py:378-379`), so with `make_client`'s default transport the `stream is None` skip cannot be reached. Real-socket runs (`exp_peer.py`, 100 fetches each): private peer over HTTP keep-alive, HTTP with a reset, and HTTPS were refused 100/100. A redirect from a public hop to a private hop was refused 100/100 when the private hop kept the connection, reset it, or used HTTPS. With the real `is_public` and a lying resolver, localhost was refused 100/100 with a FIN close and 100/100 with a reset. The author's `rebind_reset.py` gives `{'blocked_address': 40}`, 0 leaked, on HEAD, and `{'ok': 40}`, 40 leaked, after I flipped line 336 back to fail-open. The `fail-open on None` mutant fails `test_connection_whose_address_cannot_be_read_is_refused`. |
| MINOR: `javascript:` / `mailto:` redirect gives `unexpected` | A redirect to a URL httpx cannot build is `invalid_url` and not retried. | resolved | `fetch.py:260-263`. My mutant that deletes this clause fails both `test_redirect_to_a_non_web_url_is_invalid_and_not_retried` cases. The author's mutant of this fix hit the wrong line (see Housekeeping). |
| MINOR: huge `--max-bytes` overflows zlib; huge `--concurrency` exhausts memory | No accepted option value fails or exhausts memory at use time. | resolved for the CLI | `cli.py:55-56` with a maximum on all seven numeric options. The largest `--max-bytes` is 10^9 + 1 passed to zlib, below 2^31. The `>=` off-by-one mutant fails 7 parametrized cases. `Settings` built directly by a library user is still unbounded, as before (and has no lower bounds either). |
| MINOR: no test that a 3xx is closed | Every 3xx response is closed before the next hop. | resolved | `test_redirects_release_their_connection`. Two mutants are killed: close only when `next_request is None`, and `finally: pass`. The test only times out under the mutant, so it costs about 2 s then and nothing on the real code. |
| MINOR: `trust_env=False` also drops `SSL_CERT_FILE` / `SSL_CERT_DIR`, undocumented | The README says every effect of ignoring the environment. | resolved | README line 132 names the proxy variables, `SSL_CERT_FILE` / `SSL_CERT_DIR`, and certifi. The certifi claim matches httpx 0.28's `create_ssl_context` with `trust_env=False`. `.netrc` is not named, which does not matter for an unauthenticated crawler. |
| Design row 1 (`InvalidURL` unreachable) | The design describes the code as built. | resolved | `linkx.md:117` now maps `httpx.InvalidURL` to `invalid_url`. The sentence is a fragment ("`httpx.InvalidURL` (...) to `invalid_url`.") placed after "any other `Exception`" (nit). |

### Real-socket matrix (`exp_peer.py`: 127.0.0.1 and ::1 treated as public, 127.0.0.2 private, resolver says 1.2.3.4)

| Case (100 fetches each) | Result |
|---|---|
| HTTP v4 keep-alive, 4 workers / 1 worker (connections reused: 4 and 1 sockets) | ok 100 / ok 100 |
| HTTP v4 and v6 with `Connection: close` + FIN | ok 100 each |
| HTTP v6 keep-alive; HTTPS v4 keep-alive, close; HTTPS v6 keep-alive | ok 100 each |
| Redirect from a public hop to a public hop | ok 100 |
| **Public peer that resets after answering: HTTP v4, HTTP v6** | **`blocked_address` "non-public address unknown" 100 / 100** |
| Public peer that resets, HTTPS v4 | `connect` ReadError 100 (the TLS read fails first) |
| Private peer: keep-alive, reset, HTTPS; redirect from public to private (keep-alive, reset, HTTPS) | `blocked_address` 100 each |
| Real `is_public`, localhost: keep-alive / FIN / reset; `--allow-private` with a reset | blocked 100 / 100 / 100; ok 100 |

## New behaviour introduced by the fixes

| Hunk | What now happens | Who is affected, and are they told |
|---|---|---|
| `_check_response` fail closed | A response whose peer address cannot be read is refused with `blocked_address`, not retried, with no `final_url` or `http_status` | Anyone fetching a **public** server that aborts the connection (RST) right after answering: 100/100 refused here, on Windows, over both IPv4 and IPv6. Told by the README sentence, but the record says "connected to non-public address unknown (use --allow-private to permit)", which is false about the address and points at the switch that turns off all protection. Minor finding 1. |
| `_attempt` `except httpx.InvalidURL` | A redirect httpx cannot build (no host, non-printable characters, URL too long) is `invalid_url` after one attempt, not `unexpected` | Told in the record. `Location: http://[::1` is still `connect` and retried, as recorded in `decisions.md`. |
| `_bounded(maximum=...)` | A value above the bound exits 2: `must be at most 1000, got 1001` | Users passing more than 1,000 workers, 64 consumers, a queue of 10^6, 1 GB, 10 retries or 3,600 s. All are far above the defaults and above typical crawling values. Told. |
| `test_a_huge_integer_option...` renamed and inverted | A 400-digit `--retries` is now a usage error | Consistent with the bounds. |

## New findings

### [MINOR] An unreadable peer is reported as a "non-public address" and the user is told to use `--allow-private`
- **Where:** `src/linkx/fetch.py:336-341`
- **Problem:** When `server_addr` is `None`, the message is `<host> connected to non-public address unknown (use --allow-private to permit)`. The address is unknown, not non-public. A user who hits it on a legitimate public site that resets after answering (measured 100/100 locally) is advised to turn off both address checks for the whole run. The refusal itself is the decision the author recorded. The wording and the advice are not.
- **Evidence:** `exp_peer.py`: `http v4 RST after answer, public peer {'blocked_address:unknown': 100}`, same for v6. With `allow_private` the same server gives `ok 100`.
- **Confidence:** high on the message. Medium on how often real public servers reset quickly enough that `getpeername` fails before the response hook runs. Over a WAN the RST usually arrives after the headers are parsed, so this case may be rarer than it is on localhost.
- **Suggested fix:** a separate message, for example `<host>: connected address unknown (connection reset after answering); refused`, without the `--allow-private` hint. Sturdier, and a step toward the DNS-pinning follow-up: record the peer at connect time, through a wrapping `httpcore` network backend or the `trace` extension's `connection.connect_tcp.complete`. That removes both the false positive and the dependence on timing.

### [MINOR] The README options table is split by the new paragraph
- **Where:** `README.md:80-86`
- **Problem:** The "Upper bounds" paragraph sits between the `--per-host-delay` row and the `--allow-private` row. In GitHub Markdown the table ends at the blank line. The rows `--allow-private`, `--keep-duplicates`, `--verbose` and `--log-json` then become literal `| ... |` text joined to the paragraph.
- **Evidence:** `sed -n 66,90p README.md`, lines 80-86.
- **Confidence:** high.
- **Suggested fix:** move the paragraph below the table, before "Two more settings exist…".

## Unclaimed changes
- `tests/test_e2e.py` adds a `docs/index.html` fixture and imports `Outcome`. These serve the new test. Safe.
- `FakeNetworkStream` accepts `None`. Safe.
- README test count changed to "255 tests". The suite collects **254** (nit).

## Findings with no fix
None at blocker or major. The round-2 minors without a fix (Ctrl-C during the client's close, the non-UTF-8 message not naming the file, the late interrupt log line, `5f00::1`) are now recorded in `decisions.md` as ledger follow-ups or left open.

## Waiting for the owner
- `decisions.md` lists nothing waiting. Its round-3 rows match the code and the commits (`ca5e3a3`, `1591d2c`).
- Note on the recorded decision "An in-memory transport (no `network_stream`) skips the check". The reason it gives, "only test transports lack a network stream", is true of httpx's own transports. It is not true of any custom `AsyncBaseTransport` that a library user passes to `extract(transport=...)` or `make_client` and that builds its own `Response` without forwarding `extensions` (a caching or recording wrapper). The check is then silently skipped. This affects library use only, the CLI cannot reach it, and round 2 had the same behaviour. It is worth one sentence in the docstring of `extract`/`make_client`.

## The same class again
- **A message that states more than the check knows.** "non-public address unknown" (finding 1) belongs with round 2's "a check that treats 'could not tell' as 'safe'": "could not tell" should be its own outcome in both the decision and the wording. Both are enforced in one place, `_check_response`.

## Housekeeping (nits)
- `evidence/mutants_round3.py`, mutant "InvalidURL not mapped", uses `replace(old, new, 1)` on `except httpx.InvalidURL as exc:`. That string appears first at `fetch.py:62` (`validate_url`), not at the new clause on line 260. The kill it reports (`test_invalid_url_sends_no_request[http://[::1/broken]`) therefore says nothing about the fix. Deleting the clause at line 260 is killed by the two new tests (my run).
- `test_options_have_an_upper_bound` and `test_a_huge_integer_option_is_a_usage_error_not_a_crash` call `main()`. If a bound regresses, the test then runs a real fetch of `http://x.test/` through the system resolver, with up to 3,600 s timeouts, and hangs instead of failing. `pytest.raises(SystemExit)` around `build_parser().parse_args(...)` gives the same check with no I/O.
- The `_follow` docstring (`fetch.py:272`) still names only `RemoteProtocolError` for a bad `Location`.
- The design sentence on `InvalidURL` is a fragment that follows "any other `Exception`".
