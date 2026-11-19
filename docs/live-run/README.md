# Live run: 1,000 real URLs

`linkx` run against 1,000 real web pages, twice, to see it work beyond tests and canned pages. Everything needed to repeat it is in this folder.

**Setup**, as run by the author: Windows 11, Python 3.15.2 (the repository's `.python-version`), a home connection, 2026-11-19. Options: `--per-host-delay 1 --timeout 15`, otherwise defaults (2 retries, queue 100, `drop-oldest`, 1 consumer). [`measure.py`](measure.py) runs the real CLI in-process and reports wall time, CPU time and peak memory (Windows only).

**The URL list** ([`urls.txt`](urls.txt)) was built by `linkx` itself ([`build_urls.py`](build_urls.py)): the links on 15 pages that link out widely (Wikipedia articles with long reference lists, Hacker News, python.org), shuffled, at most 4 per site. 1,000 URLs on 712 hostnames and 641 sites.

## Results

| | concurrency 50 | concurrency 10 |
|---|---|---|
| Records | 1,000 for 1,000 URLs, every index once | 1,000 for 1,000 URLs, every index once |
| ok / fetch_error / parse_error / dropped / cancelled | 650 / 350 / 0 / 0 / 0 | 653 / 347 / 0 / 0 / 0 |
| Links extracted | 58,937 | 58,908 |
| Wall time | 71 s | 221 s |
| First progress line (every 5 s) at or past 500 / 900 / 990 done | 16 s / 26 s / 66 s | 76 s / 161 s / 186 s |
| Peak memory | 102 MB | 73 MB |
| CPU time | 18 s | 46 s |
| Fetch latency p50 / p95 / p99 | 0.6 / 4.6 / 46.8 s | 0.4 / 3.4 / 46.7 s |
| Retries | 230 | 232 |
| Highest queue depth | 4 | 3 |

Logs exactly as printed: [`log-c50.txt`](log-c50.txt), [`log-c10.txt`](log-c10.txt). Records: `results-c50.jsonl.gz`, `results-c10.jsonl.gz`. Breakdown: `uv run python docs/live-run/analyse.py c50`.

**Why pages failed** (concurrency 50): 90 refused a non-browser client (HTTP 403), 79 were dead links (404), 55 could not be reached (DNS failure, refused connection, expired certificate, dropped connection), 50 were not HTML (mostly PDFs), 24 timed out, 29 were rate limited (429), 2 were over the 2 MB cap, and 21 more had other HTTP statuses (401, 402, 406, 410, 451, several 5xx, and one 307 without a usable `Location`). Each record carries its error kind and message.

## What it shows

- **One record per URL at scale.** Both runs wrote exactly one record for each of the 1,000 URLs, with no parse errors.
- **Nothing was dropped.** The parser kept up with the network: the queue never held more than 4 results, so `drop-oldest` never had to act.
- **More workers, roughly proportionally faster.** Five times the workers got through the first 90% about six times sooner (900 done by 26 s against 161 s). Progress is logged every 5 s, so these times are accurate to 5 s, and the speed-up for the first half lies between about 4.4 and 6.9 times.
- **The tail is the slowest servers.** p99 is about 47 s: three 15-second attempts at servers that never answer. The last 26 URLs (2.6%) took 40 of the 71 seconds (974 were done at 31 s). A lower `--timeout` or `--retries` trades that tail for more timeouts.
- **Results don't depend on concurrency.** 650 against 653 ok; the three differences are flaky servers.
- **Memory stays small.** 102 MB peak with 50 workers.

## The first attempt, and two limits it exposed

The first list ([`urls-v1-hostname-cap.txt`](urls-v1-hostname-cap.txt)) capped URLs per *hostname*. Every Wikipedia article links to its versions in other languages, so 454 of the 1,000 URLs were Wikipedia pages on hundreds of language hostnames (`de.wikipedia.org`, `fr.wikipedia.org`, ...). Their rate limit evidently spans all those hostnames: 436 of the 454 ended in HTTP 429, which was 436 of the 595 failures, and `linkx` spent 873 retries on them ([`log-v1-c50.txt`](log-v1-c50.txt), `results-v1-c50.jsonl.gz`). The list was then rebuilt with the cap per site, which gave the results above.

1. **Per-host politeness is not per-provider politeness.** Many hostnames can share one rate limit: a CDN, a hosting platform, a site with many subdomains. `--per-host-delay` spaces requests to each hostname, so it did not help. A production fetcher keys its limiter by site or provider (registrable domain, resolved address or network), not hostname.
2. **Retrying a 429 without reading `Retry-After` wastes requests.** The retries went out after 0 to 2 seconds of backoff, sooner than the server wanted, and most failed again. Honouring `Retry-After`, and backing off the whole provider after a 429, is the fix.

Both are listed under "Not built" in the main README.

## Repeating it

```powershell
uv run python docs/live-run/build_urls.py           # rebuilds urls.txt from today's seed pages
& .\docs\live-run\run_live.ps1 -Concurrency 50       # logs to the terminal and to log-c50.txt
uv run python docs/live-run/analyse.py c50
```

Pages change, so a new run gives a different list and slightly different numbers.
