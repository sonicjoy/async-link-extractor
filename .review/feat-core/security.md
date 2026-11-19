# Security review

**Verdict:** FAIL (one design-level SSRF bypass that the design accepts but that defeats its own "safe by default" rule; one confirmed memory-amplification issue). Everything else is LOW.

Scope: `src/linkx/*`, `.github/workflows/ci.yml`, `pyproject.toml`/`uv.lock`. Throwaway scripts wrote only under the system temp folder; localhost only.

## Findings

### [HIGH] DESIGN FINDING: DNS rebinding (check-then-connect gap) defeats the private-address block
- **Where:** `src/linkx/fetch.py:274-297` (`_check_request` / `_check_public`); `docs/designs/linkx.md:113` ("An address that changes between this check and the connection is not detected").
- **Issue:** SSRF, time-of-check/time-of-use. The hook resolves the host with `getaddrinfo` and validates the answers; httpx/httpcore then resolves the same name again when it opens the socket. The two lookups are independent. The design records this as accepted, but the same design lists "Safe by default. Non-public addresses are refused, including on redirects" as a goal, and the tool's stated threat is an untrusted URL list.
- **Attack path:** The attacker puts `http://rebind.attacker.example/` in the URL list and runs the authoritative DNS for that name. First answer (the check) is a public IP with TTL 0; second answer (the connect) is `169.254.169.254` or `127.0.0.1`. The response body is parsed and its links are written to the output, so internal HTML is read back through `links`, and `error.message`, `http_status` and timing leak more. The same works at every redirect hop. No privilege is needed beyond controlling one DNS name. Not run live (no non-localhost network); the mechanism is two separate resolutions in the code path.
- **Evidence:** `_check_public` calls `self._resolve(host, port)`; the request is then sent with `self._client.stream("GET", url)` using the host name, so httpcore resolves again. There is no transport, resolver pinning or post-connect address check.
- **Confidence:** high on mechanism; medium on exploitability per deployment (a local caching resolver with a minimum TTL narrows the window but does not close it).
- **Fix:** Connect to the address that was validated: a custom httpcore network backend or `httpx.AsyncHTTPTransport` that resolves once, validates, and dials the IP (keeping Host and TLS SNI/verification on the name). Or check the connected peer address (`response.extensions["network_stream"]`) before reading the body, accepting that the request was already sent. Update the design text either way.

### [MEDIUM] Decompression amplification: about 70 MB transient memory per worker despite `--max-bytes`
- **Where:** `src/linkx/fetch.py:226-234` (`aiter_bytes` loop); `make_client` default `Accept-Encoding` (gzip, deflate).
- **Issue:** The cap is checked after each chunk is decompressed, and httpx decompresses a whole raw chunk (up to 64 KiB, about 1000:1 for zlib) before yielding it. The Content-Length pre-check compares the compressed length only. A server that omits Content-Length defeats the early rejection.
- **Attack path:** Attacker-controlled URLs, a 1 MB gzip of 1 GiB zeros, 10 workers (default). Measured with `--max-bytes 1000`, concurrency 10: peak 735.8 MB traced memory; with a Content-Length header the same bomb is rejected at 10.9 MB. Peak scales with `--concurrency` (100 workers is about 7 GB) and with retries. The cap still applies afterwards (record is `too_large`), so this is a peak-memory DoS, not a cap bypass.
- **Evidence:** in-process `extract` against a localhost gzip bomb: `735.80854 MB peak`, outcome `too_large`, 10 jobs.
- **Confidence:** high (reproduced).
- **Fix:** Stream with `aiter_raw()` and a `zlib.decompressobj` using `max_length` per call, counting decoded bytes; or send `Accept-Encoding: identity`. An over-cap body stays a `too_large` error record (no correctness change).

### [LOW] Line-splitting characters in output JSON lines (U+2028, U+0085, U+2029)
- **Where:** `src/linkx/models.py:121` (`json.dumps(..., ensure_ascii=False)`); links come from `src/linkx/parse.py`.
- **Issue:** Output injection. `json.dumps` escapes characters below 0x20 but leaves U+0085, U+2028 and U+2029 raw. Consumers that split with `str.splitlines()` see one record as several lines.
- **Attack path:** A hostile page holds `<a href="http://x.com/a&#x2028;{...fake record...}">`. Confirmed: `extract_links` keeps the character and one `Record.to_json()` output gives `len(s.splitlines()) == 3`. A consumer using `splitlines()` then reads attacker-shaped records (fake `index`, `outcome`). Consumers splitting on `\n` are unaffected. Same for `url` and `error.message`.
- **Confidence:** high on behaviour; low-medium on impact (depends on consumer).
- **Fix:** `ensure_ascii=True`, or escape those three code points after dumping; state it in the README output contract.

### [LOW] IP classification gaps in `is_public`
- **Where:** `src/linkx/fetch.py:73-78`.
- **Issue:** `ipaddress.is_global` accepts (checked): `64:ff9b::7f00:1` and `64:ff9b::a9fe:a9fe` (NAT64 prefix embedding 127.0.0.1 and 169.254.169.254), `::127.0.0.1`, `192.0.0.9`, `192.88.99.1`, `5f00::1`. IPv4-mapped (`::ffff:127.0.0.1`, `::ffff:7f00:1`) and 6to4 (`2002:7f00:1::`, `2002:a9fe:a9fe::`) are correctly refused.
- **Attack path:** A host on a NAT64/DNS64 network (common on IPv6-only cloud hosts) and an attacker DNS answer of `64:ff9b::a9fe:a9fe`; the gateway translates to the metadata address. `::127.0.0.1` is not routable on mainstream stacks.
- **Confidence:** medium (NAT64 path not run live); low for the others.
- **Fix:** Unwrap `64:ff9b::/96`, `64:ff9b:1::/48` and `::/96` and test the embedded IPv4; refuse `192.0.0.0/24` and `192.88.99.0/24`.

### [LOW] Environment proxies and netrc are trusted (`trust_env` default)
- **Where:** `src/linkx/fetch.py:81-99` (`make_client`, no `trust_env=False`).
- **Issue:** With `HTTP(S)_PROXY`/`ALL_PROXY` set, requests go to the proxy. The hook still resolves and checks the target locally, but the proxy resolves independently (a wider rebinding gap; a name public locally may be internal to the proxy). The design's Security bullet does not mention it. The attacker does not control the environment, so this is hardening.
- **Confidence:** medium.
- **Fix:** `trust_env=False`, or add an explicit `--proxy` and document that the address check does not bind the proxy's resolution.

### [LOW] Control characters from the URL list reach plain-text logs
- **Where:** `src/linkx/pipeline.py` (drop warning, always on, and the verbose record log); retry/attempt logs in `fetch.py`; plain formatter in `cli.py` `configure_logging`.
- **Issue:** Log/terminal injection. Input-file lines are only `strip()`ped, so a line can hold `\x1b[...`. The drop-oldest warning logs `evicted.job.url` raw at default verbosity, including URLs that were never valid. `--log-json` is safe.
- **Attack path:** Whoever controls the list can rewrite the operator's terminal output (hide drop warnings, fake summary lines). Needs an untrusted list and a terminal. Found by code reading; not run.
- **Confidence:** medium.
- **Fix:** log the URL with `%r`, or reject control characters in `read_urls`.

### [LOW] Userinfo and tokens in URLs are echoed to logs and output
- **Where:** `src/linkx/models.py` (`url`, `final_url`), retry/drop logs, `cli.dedupe_key` (keeps userinfo).
- **Issue:** `http://user:pass@host/` and token query strings are written verbatim to stdout/`--out` records and stderr logs, which CI often collects. A redirect `Location` carrying credentials lands in `final_url`.
- **Attack path:** Disclosure to whoever reads logs or output files; no remote attacker.
- **Confidence:** medium.
- **Fix:** Redact userinfo in logs; document that records echo URLs verbatim.

### [LOW] Slow DNS can tie up the shared thread pool
- **Where:** `src/linkx/fetch.py:35-38` (`loop.getaddrinfo`, default executor), shared with `asyncio.to_thread` parsing in `pipeline.py`.
- **Issue:** `asyncio.timeout` cancels the await but the `getaddrinfo` thread keeps running. An attacker-run authoritative server that stalls can park up to min(32, cpu+4) threads and delay parsing of other pages. Bounded by timeouts and retries: a slowdown, not a hang.
- **Confidence:** medium-low.
- **Fix:** A dedicated executor for DNS, or a resolver with its own timeout.

### [LOW] `--out` and input file handling
- **Where:** `src/linkx/cli.py` `_open_output` (`open(path, "w")`) and `read_urls`.
- **Issue:** `--out` truncates and follows symlinks before any fetch; any non-`http(s)://` argument is opened as a file (a device such as `/dev/zero` would be read without bound). Both values come from the operator's own command line, so not remotely exploitable. `--out` equal to an input file is safe because inputs are read first.
- **Confidence:** high; impact negligible under the operator-trusted CLI model.
- **Fix:** none required.

### [LOW] CI workflow: unpinned actions, token persisted
- **Where:** `.github/workflows/ci.yml` (checkout, setup-uv, upload-artifact steps).
- **Issue:** `actions/checkout@v7`, `astral-sh/setup-uv@v10` and `actions/upload-artifact@v7` are tag-pinned, not SHA-pinned; checkout leaves `GITHUB_TOKEN` in `.git/config`. Mitigations present: `permissions: contents: read`, `pull_request` (not `pull_request_target`), no secrets, `uv sync --locked`, only first-party and Astral actions. I could not verify the tags exist (no network).
- **Confidence:** low risk.
- **Fix:** pin by SHA; `persist-credentials: false`. `enable-cache: true` lets a PR write the cache (poisoning risk for later runs on the same key).

## Controls relied on
| Control (where) | What this change sends it | Inputs tried | What got through |
|---|---|---|---|
| `is_public` (`fetch.py:73`) | Every resolved address and literal host | `::ffff:127.0.0.1`, `::ffff:7f00:1`, `2002:7f00:1::`, `2002:a9fe:a9fe::`, `fe80::1%eth0`, `100.64.0.1`, `169.254.169.254`, `fd00:ec2::254`, `::`, `0.0.0.0`, `224.0.0.1`, `198.18.0.1`, `2001::1`: all refused | `64:ff9b::7f00:1`, `64:ff9b::a9fe:a9fe`, `::127.0.0.1`, `192.0.0.9`, `192.88.99.1`, `5f00::1` accepted |
| `validate_url` + request hook (`fetch.py:60,274`) | First URL and every redirect hop | `127.1`, `0x7f.1`, `2130706433`, `[::1]`, `a@127.0.0.1`, `127.0.0.1.`, fullwidth-dot `127。0。0。1` (httpx normalises to `127.0.0.1`), `localhost.`; non-IDNA lookalikes rejected as invalid | Shorthand, decimal and octal forms are not IP literals to `ipaddress`, so they go to the resolver, resolve to 127.0.0.1 and are blocked. A second lookup at connect time is not covered: see HIGH |
| `max_bytes` cap (`fetch.py:216-234`) | Streamed decoded bytes | 1 GiB gzip bomb, with and without Content-Length | Cap enforced, but peak memory 736 MB at 10 workers: see MEDIUM |
| `asyncio.timeout` (`fetch.py:162`) | Whole attempt incl. redirects and body | by inspection | Covers connect, headers and body; the DNS thread outlives it (LOW) |
| HTMLParser link extraction (`parse.py`) | Up to 2 MB hostile HTML | `<a ` x600k, `<!--` x500k, unclosed attrs, `<` x2M, `&` x2M, CDATA, unclosed href | worst case 0.8 s per page in a worker thread; no quadratic blow-up seen |
| `json.dumps` record output (`models.py:121`) | Hostile `links`, `url`, error text | ` `, `\x85`, quotes, newlines | `\n`, quotes and control characters escaped; ` `, `\u0085` pass raw: see LOW |
| `JsonFormatter` (`cli.py`) | URLs and messages | by inspection | JSON-escaped, safe |

## Security-relevant changes with no issue found
- Scheme restriction to http/https on the first request and every redirect hop; TLS verification left on (no `verify=False`).
- Redirect cap (`max_redirects=9`) and `too_many_redirects` mapping.
- Per-attempt total timeout including redirects and body; retries bounded by `--retries`.
- Content-type gate before the body is read; Content-Length pre-check.
- Extracted links limited to http/https with a netloc (no `javascript:` or `file:` output); `<base href>` accepted only if a web URL.
- Dependencies: only `httpx>=0.28,<1` (anyio, httpcore, h11, idna, certifi are the genuine transitive set); `uv.lock` has hashes; dev tools are well-known names; no typosquats. No secrets in code, tests, CI or logs. No shell, eval, pickle or subprocess use in `src/`.
- Pending-result memory bounded by `queue_max` x `max_bytes` (operator-settable).
- `classes.md` and `followups.md` are empty: nothing to compare against.
