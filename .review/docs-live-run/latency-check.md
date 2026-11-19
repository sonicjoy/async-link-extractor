# Latency section: statements and the output that backs them

Docs-only change, checked by the author against the output of `docs/live-run/analyse.py` (below, unedited).

| Statement | Backed by |
|---|---|
| Table values (n, p50, p95, p99, max per group, both runs) | `elapsed_ms by group` lines below |
| ok median ~half a second, p95 ~2 s | ok: 585 / 2054 (c50), 406 / 1585 (c10) |
| Redirect costs ~300 ms at the median | ok through a redirect 894 vs ok 585 (c50); 695 vs 406 (c10) |
| 403/404 return in ~0.3 s | error, not retried p50 377 (c50), 298 (c10) |
| Retries recovered nothing; 115 retried URLs failed every attempt | `ok pages that needed a retry: 0`; attempts {1: 885, 3: 115}; error after 3 attempts n=115 |
| 230 retries | summary `retries 230` (c50) |
| Throughput rises about threefold | wall 71 s vs 221 s (README table) |
| 97% done in 31 s; count barely moved for 15 s with 23 in flight | progress lines 31s 974 (26 in flight), 36s-46s 977 (23 in flight) |
| Queue empty throughout | every progress line `queue 0` (log-c50.txt) |

## analyse.py c50
```text
outcomes: {'fetch_error': 350, 'ok': 650}
429 retries logged: 58, by site: [('*.wiktionary.org', 8), ('*.wikimedia.org', 6), ('search.worldcat.org', 6), ('en.wikibooks.org', 6), ('en.wikisource.org', 6)]
attempts per URL: {1: 885, 3: 115}
ok pages that needed a retry: 0
elapsed_ms by group (n, p50, p90, p95, p99, max):
  all                      n=1000: 603, 2594, 4621, 46802, max 47902
  ok                       n= 650: 585, 1548, 2054, 4073, max 12297
  ok through a redirect    n= 193: 894, 1973, 2293, 9077, max 11546
  error, not retried       n= 235: 377, 1252, 1751, 4122, max 10408
  error after 3 attempts   n= 115: 3275, 46785, 47035, 47425, max 47902
  timeout                  n=  24: 46755, 47423, 47425, 47902, max 47902
log: outcomes: ok 650, fetch_error 350, parse_error 0, dropped 0, cancelled 0
log: retries 230, max queue depth 4
```

## analyse.py c10
```text
outcomes: {'ok': 653, 'fetch_error': 347}
429 retries logged: 56, by site: [('*.wiktionary.org', 8), ('*.wikimedia.org', 6), ('search.worldcat.org', 6), ('en.wikibooks.org', 6), ('en.wikisource.org', 6)]
attempts per URL: {1: 884, 3: 116}
ok pages that needed a retry: 0
elapsed_ms by group (n, p50, p90, p95, p99, max):
  all                      n=1000: 438, 2252, 3357, 46696, max 47713
  ok                       n= 653: 406, 1190, 1585, 2913, max 13888
  ok through a redirect    n= 194: 695, 1616, 2149, 4236, max 8482
  error, not retried       n= 231: 298, 931, 1320, 2714, max 4344
  error after 3 attempts   n= 116: 2651, 46643, 47030, 47524, max 47713
  timeout                  n=  24: 46571, 47401, 47524, 47713, max 47713
log: outcomes: ok 653, fetch_error 347, parse_error 0, dropped 0, cancelled 0
log: retries 232, max queue depth 3
```
