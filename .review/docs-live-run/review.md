# Review of docs/live-run (recomputed from logs and gzipped records)

TRUE (computed value):
- Records 1,000 per run, indexes 0..999 each once (c50, c10, v1): true
- Outcomes c50 650/350/0/0/0, c10 653/347/0/0/0: true
- Links extracted 58,937 / 58,908: true
- Wall 71.3 s / 221.0 s; CPU 18.3 / 46.2 s; peak memory 102 / 73 MB (MEASURED lines): true
- Latency nearest-rank c50 p50 603, p95 4621, p99 46802 ms; c10 438, 3357, 46696 ms: true
- Retries 230 / 232 (log lines, sum of attempts-1, summary agree): true
- Highest queue depth 4 / 3 (summary lines): true
- Failure breakdown c50: 403=90, 404=79, connect=55 (dns 36, cert 11, refused 6, dropped 2), not_html=50 (44 PDF), timeout=24, 429=29, too_large=2, other HTTP=21 (401x4, 402x4, 406x3, 410, 451, 307 with no Location, 500x2, 502x3, 503, 522) -> 90+79+55+50+24+29+2+21 = 350 = fetch_error total: true
- urls.txt: 1,000 unique URLs, 712 hostnames, 641 sites, max 4 per site, 15 seeds in build_urls.py: true
- v1 list: 454 Wikipedia URLs, 595 failures, 436 final 429, 873 retry log lines on 429, 429 backoff 0.0 to 2.0 s: true
- 650 vs 653 ok ("three differences"): true
- Time to 900 done c50 26 s, c10 161 s; to 990 c50 66 s, c10 186 s (first progress line at or past the count): matches, but see imprecision below
- README anchor #not-built-and-how-i-would-build-it exists (heading line 205); docs/live-run/README.md and all linked files exist: true
- Ledger row closed with 6dfa385: commit exists, is on main, merges the SHA-pinning fix; ci.yml uses SHA pins: true
- analyse.py and measure.py compute what is described (nearest-rank; wall/CPU/peak working set): true

FALSE or IMPRECISE:
1. "The last 1% of URLs took 40 of the 71 seconds" (docs/live-run/README.md): FALSE. In c50 the 990th result arrives between 61 s (989 done) and 66 s (993 done), so the last 1% took about 5 to 10 s. What took about 40 s is the last 2.6%: 974 done at 31 s, 1,000 at 71 s. Correct: "The last 2.6% of URLs (26 of 1,000) took 40 of the 71 seconds." (Or for c10: 990 done at 186 s, last 1% took 35 of 221 s.)
2. "Time to 500 done: 16 s / 76 s" and "about 4.75 times the throughput" (both READMEs): IMPRECISE. Progress lines come every 5 s, so these are the first progress line at or past 500, not the time to 500. Real time to 500 is in (11, 16] s for c50 (441 done at 11 s, 625 at 16 s) and in (70, 76] s for c10 (496 at 70 s, 519 at 76 s). Ratio 76/16 = 4.75 is only one point in a range of roughly 4.4 to 6.9. Suggest: "500 done by the 16 s and 76 s progress lines, about 4.5 to 5 times as fast" or compare 900 done (26 s vs 161 s = 6.2x) or wall-time-to-990 etc. The column label "Time to 500 / 900 / 990 done" should say "(progress lines, every 5 s)". Same for the main README table "(first 500 done)".
3. scripts/run_live.ps1 line 1 comment says "605 hosts": stale. 605 is the hostname count of the first list (urls-v1-hostname-cap.txt); urls.txt has 712 hostnames, 641 sites. Fix the comment.
4. "p99 is about 47 s: three 15-second attempts": plausible (115 URLs used 3 attempts; max 47.9 s) but the 99th percentile is of elapsed_ms per record; fine. Not wrong.

UNVERIFIABLE from the evidence files:
- "Python 3.15.2", "Windows 11", "home connection", "2026-11-19" (the log timestamps show only time of day; the date is consistent with the merge commit date).
- "Wikimedia limits requests per client address, not per hostname" (external fact; the logs only show 436 of 454 Wikipedia URLs ending in 429).
- "five times the workers" is the setting (50 vs 10): true by arguments; not a measurement.
