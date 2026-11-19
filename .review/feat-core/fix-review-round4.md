# Fix review, round 4

**Verdict:** PASS

Scope: `fixes-round4.patch` = `1591d2c..26f47a7` (one commit). It is byte-identical to `git diff 1591d2c HEAD`. All runs used a `git archive HEAD` copy in my temp folder (`%TEMP%\r4copy`) and localhost servers only. Baseline: **254 passed** in 7.2 s (254 collected, matching the README's new count). `ruff check`, `ruff format --check` and `mypy` are clean.

Fail closed is unchanged. Over real localhost sockets:
- A public peer is accepted.
- A non-public peer is refused with the non-public message.
- A peer whose address cannot be read (the server resets the connection) is refused with the new "could not confirm" message.
- No links are written out in either refusal.

Every test touched by the patch fails without its change: 6 of 6 mutants were killed in under 0.3 s each. The round-3 versions of the two CLI tests really did hang under a regressed bound, which is the reason for the change. The README options table is now one table.

## Findings claimed fixed

| Finding | Property (one sentence) | Status | Evidence |
|---|---|---|---|
| MINOR: unreadable peer reported as "non-public address unknown" with an `--allow-private` hint | A refusal states only what the check knows. An unknown address is reported as unknown, not as non-public, and the message does not point at the switch that turns off all protection. | resolved | `fetch.py:337-352`: `if not peer:` raises `could not confirm the address {host} connected to (the connection may have been reset)`. Only the `is_public` branch keeps "non-public address … (use --allow-private to permit)". Real sockets (`peer_r4.py`, 100 fetches each, lying resolver, 127.0.0.1 treated as public): RST after answer gives `blocked_address: could not confirm …` 100/100, links written 0. The test `test_connection_whose_address_cannot_be_read_is_refused` asserts "could not confirm" is present and that "non-public" and "--allow-private" are absent. Three mutants are killed: restoring the round-3 message block, failing open on `not peer`, and putting the `--allow-private` hint back into the new message. |
| MINOR: README options table split by the upper-bounds paragraph | The options table renders as one table. | resolved | README lines 70-84 are one contiguous block: 15 rows, 3 cells each, a valid delimiter row, preceded by a blank line. The paragraph is now at line 86, after the blank line 85. My table scan of HEAD found 0 problems. The same scan of `1591d2c:README.md` flags lines 83-86 (4 pipe rows with no delimiter row, joined to a paragraph). No Markdown renderer is installed and I had no network to fetch one, so this is a structural check against GFM table rules, not a rendered view. |
| Nit: upper-bound tests call `main()` and would hang if a bound regressed | A regressed bound makes the test fail fast, with no I/O. | resolved | `test_cli.py:135-145` now calls `build_parser().parse_args(...)` inside `pytest.raises(SystemExit)`. Mutant `if value > maximum:` → `if False:`: the new tests give `7 failed in 0.30s`. The round-3 tests (`git show 1591d2c:tests/test_cli.py`) under the same mutant were still running at a 90 s timeout. No stray processes were left behind. |
| Nit: `_follow` docstring named only `RemoteProtocolError` | Docstrings describe the code as built. | resolved | `fetch.py:270-274` now names `RemoteProtocolError` (`connect`) and `InvalidURL` (`invalid_url`). This matches the `except httpx.InvalidURL` clause in `_attempt` and the two `test_redirect_to_a_non_web_url_is_invalid_and_not_retried` cases. |
| Nit: design `InvalidURL` sentence was a fragment after "any other `Exception`" | The design describes the code as built. | resolved | `linkx.md:117` now lists `httpx.InvalidURL` before "any other `Exception` to `unexpected`", in one sentence. The `linkx.md:113` addition ("refused with a message saying the address could not be confirmed") matches `fetch.py:343-344`. |
| Nit: README test count 255 | The README states the real count. | resolved | `pytest --collect-only`: 254 tests collected. |

### Real-socket matrix (`peer_r4.py`, 100 fetches each, concurrency 4, retries 0)

| Server behaviour | 127.0.0.1 treated as public | Real `is_public` |
|---|---|---|
| keep-alive | ok 100 | `blocked_address: localhost connected to non-public address 127.0.0.1 (use --allow-private to permit)` 100 |
| `Connection: close` + FIN | ok 100 | same non-public message 100 |
| RST straight after answering | `blocked_address: could not confirm the address localhost connected to (the connection may have been reset)` 100 | "could not confirm" 99, non-public 1 (the address was readable once before the reset landed) |
| RST, `--allow-private` | n/a | ok 100 |

Links were written out only in the rows that end `ok`.

### Mutants (`mutants_r4.py`)

| Mutant | Test(s) | Result |
|---|---|---|
| fetch hunk reverted to the round-3 message block | `test_connection_whose_address_cannot_be_read_is_refused` | 1 failed |
| `if not peer: return` (fail open) | same | 1 failed |
| unknown-peer message suggests `--allow-private` | same | 1 failed |
| non-public check removed | the private-address and private-redirect-hop tests | 2 failed |
| every readable peer refused | `test_connection_to_a_public_address_is_accepted` | 1 failed |
| CLI upper bound removed | `test_options_have_an_upper_bound` | 7 failed |

## New behaviour introduced by the fixes

| Hunk | What now happens | Who is affected, and are they told |
|---|---|---|
| `_check_response` split | Same decisions as round 3: unknown peer refused, non-public peer refused, public peer accepted, all skipped under `--allow-private`. Only the unknown-peer message changes, and it no longer suggests `--allow-private`. | Users who fetch a public server that resets right after answering. They get an accurate message. They are not told that `--allow-private` would let the page through. That omission is deliberate (round 3 suggested it) and is the safer advice. |
| test changes | Bound tests no longer touch the network or the event loop | Developers only |
| README, design, docstrings | Text only | None |

## New findings

None at blocker, major or minor.

## Unclaimed changes

- `fetch.py:332-336`: a comment that a custom transport which drops response extensions also skips the connected-address check. This answers the round-3 "Waiting for the owner" note, but as a private code comment. The note suggested a sentence in the public docstrings of `extract` / `make_client`, where a library user would see it. `decisions.md` still gives the reason "Only test transports lack a network stream". Nit: safe either way, and library-only.

## Findings with no fix

None. Round 3 had no blocker or major findings. Its two minors and four nits are all addressed above, except the `mutants_round3.py` wrong-line nit, which concerns an evidence script and does not need a code fix.

## Waiting for the owner

`decisions.md` lists nothing waiting. It has no rows yet for round 3 or this fix set (the message split, the table move). Add them for the record.

## The same class again

The round-3 class "a message that states more than the check knows" is now closed at its single enforcement point, `_check_response`. "Could not tell" has its own branch and its own wording. The pre-connect check (`fetch.py:371`, "resolves to non-public address") reports only addresses it has actually read, so it is not affected.

Not run: other Python versions. The change is version-independent and fetching interpreters needs network beyond localhost.
