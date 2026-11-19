"""Break down a live run: outcomes, error kinds, hosts behind the 429s, latency, links.

Usage:  uv run python docs/live-run/analyse.py c50
"""

import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).parent
tag = sys.argv[1]
path = HERE / f"results-{tag}.jsonl"
text = (
    path.read_text("utf-8")
    if path.exists()
    else gzip.decompress(path.with_suffix(".jsonl.gz").read_bytes()).decode()
)
records = [json.loads(line) for line in text.splitlines()]
log = (HERE / f"log-{tag}.txt").read_text("utf-8")


def site(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for family in (
        "wikipedia.org",
        "wikimedia.org",
        "wikidata.org",
        "wiktionary.org",
        "wikiquote.org",
    ):
        if host.endswith(family):
            return f"*.{family}"
    return host


outcomes = Counter(r["outcome"] for r in records)
kinds = Counter(r["error"]["kind"] for r in records if r["error"])
unique = len({r["index"] for r in records}) == len(records)
print(f"records: {len(records)} (indexes unique: {unique})")
print("outcomes:", dict(outcomes))
print("error kinds:", dict(kinds.most_common()))

hosts = Counter(site(r["url"]) for r in records)
print(f"distinct hosts: {len({(urlsplit(r['url']).hostname or '') for r in records})}")
print("biggest site families in the list:", hosts.most_common(5))

status = Counter(r["http_status"] for r in records if r["outcome"] != "ok")
print("final HTTP status of non-ok records:", dict(status.most_common(8)))
too_many = Counter(site(r["url"]) for r in records if r["http_status"] == 429)
print("who answered 429 (final):", too_many.most_common(5))

retry_429 = re.findall(r"\[#(\d+)\] attempt \d failed \(http_status: HTTP 429\)", log)
by_index = {r["index"]: r for r in records}
retry_sites = Counter(site(by_index[int(i)]["url"]) for i in retry_429)
print(f"429 retries logged: {len(retry_429)}, by site:", retry_sites.most_common(5))

ok = [r for r in records if r["outcome"] == "ok"]
links = [len(r["links"]) for r in ok]
print(f"ok pages: {len(ok)}, links extracted: {sum(links)}, max on one page: {max(links)}")
redirected = sum(1 for r in ok if r["final_url"].rstrip("/") != r["url"].rstrip("/"))
print(f"ok pages reached through a redirect: {redirected}")

lat = sorted(r["elapsed_ms"] for r in records if r["elapsed_ms"] is not None)


def pick(p: int) -> int:
    """Nearest-rank percentile, as linkx's summary uses."""
    return int(lat[max(1, -(-p * len(lat) // 100)) - 1])


quantiles = ", ".join(f"p{p} {pick(p)}" for p in (50, 90, 95, 99))
print(f"fetch latency ms: {quantiles}, max {lat[-1]}")
attempts = Counter(r["attempts"] for r in records)
print("attempts per URL:", dict(sorted(attempts.items())))
for line in log.splitlines():
    if (
        line.startswith(("MEASURED", "  outcomes", "  retries", "  fetch latency"))
        or "summary:" in line
    ):
        print("log:", line.strip())
