#!/bin/sh
# Read-only searches and proofs behind the blast-radius entries in human-review.md.
# Run from the repository root. Each block names the packet item it supports.

# --- Every item: who depends on this code? -------------------------------------------
# The package is new; nothing outside it imports it. Expect hits only in src/, tests/, scripts/.
git grep -n "from linkx\|import linkx" -- ':!notes'
git ls-tree -r main --name-only            # base has no code: docs and review reports only

# --- Address check (SSRF) --------------------------------------------------------------
# Every request goes through the hook; nothing else sends requests.
git grep -n "_check_request\|event_hooks\|client.stream\|httpx.AsyncClient(" -- src
# The one place resolution happens before the connection resolves again (the rebinding gap):
git grep -n "_resolve(\|getaddrinfo" -- src
# Proof that literals and resolved names are refused, including embedded IPv4 forms:
uv run pytest -q tests/test_fetch.py -k "public or private or blocked"
uv run python .review/feat-core/evidence/generated.py      # P4: 6,500 reserved addresses

# --- Size cap and decompression --------------------------------------------------------
git grep -n "aiter_raw\|aiter_bytes\|decompressobj\|max_bytes" -- src
uv run python .review/feat-core/evidence/gzip_bomb.py       # peak memory at HEAD
uv run python .review/feat-core/evidence/boundary.py        # caps at and past the bound

# --- One record per job, interrupts ----------------------------------------------------
git grep -n "def emit\|def close\|def interrupt\|shutdown()" -- src
uv run python .review/feat-core/evidence/stress.py          # 300 randomised schedules
uv run python .review/feat-core/evidence/mutant_first_interrupt.py   # must FAIL (exit 1)
# Real SIGINT on Linux (needs Docker):
# docker run --rm -v "$PWD:/src:ro" python:3.15-slim sh /src/.review/feat-core/evidence/linux_ctrl_c.sh

# --- CI workflow ------------------------------------------------------------------------
git grep -n "permissions\|persist-credentials\|uses:" -- .github/workflows/ci.yml

# --- Dependencies -----------------------------------------------------------------------
git grep -n "dependencies\|httpx" -- pyproject.toml
grep -c "hash = " uv.lock                  # every locked artifact is hash-pinned
