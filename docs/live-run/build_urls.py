"""Build a list of ~1,000 real URLs with linkx itself.

Fetch a handful of seed pages that link out to many sites (Wikipedia articles with long
reference lists, Hacker News, python.org), then pick links so that no host gets more than
a few URLs: the run measures many independent servers, not one site, and stays polite.

Run:  uv run python docs/live-run/build_urls.py
"""

import io
import json
import random
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit

from linkx.cli import main

HERE = Path(__file__).parent
SEEDS = [
    "https://en.wikipedia.org/wiki/Web_crawler",
    "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "https://en.wikipedia.org/wiki/Internet",
    "https://en.wikipedia.org/wiki/World_Wide_Web",
    "https://en.wikipedia.org/wiki/Linux",
    "https://en.wikipedia.org/wiki/Association_football",
    "https://en.wikipedia.org/wiki/Sports_betting",
    "https://en.wikipedia.org/wiki/Climate_change",
    "https://en.wikipedia.org/wiki/Open-source_software",
    "https://en.wikipedia.org/wiki/List_of_most-visited_websites",
    "https://en.wikipedia.org/wiki/Search_engine",
    "https://en.wikipedia.org/wiki/HTTP",
    "https://news.ycombinator.com/",
    "https://news.ycombinator.com/news?p=2",
    "https://www.python.org/",
]
TARGET = 1_000
PER_HOST = 4

buffer = io.StringIO()
with redirect_stdout(buffer):
    main([*SEEDS, "--timeout", "20", "--max-bytes", "20000000", "--log-json"])
records = [json.loads(line) for line in buffer.getvalue().splitlines()]

candidates: list[str] = []
for record in records:
    candidates.extend(record["links"] or [])
rng = random.Random(20261120)
rng.shuffle(candidates)

per_host: dict[str, int] = {}
chosen: list[str] = []
for url in candidates:
    host = (urlsplit(url).hostname or "").lower()
    if not host or url in chosen:
        continue
    # Cap per site, not per hostname: de.wikipedia.org and fr.wikipedia.org are one
    # provider that rate-limits by client IP (the first run hit 436 HTTP 429s that way).
    labels = host.split(".")
    site = ".".join(
        labels[-3:]
        if labels[-2] in {"co", "ac", "gov", "org", "com"} and len(labels[-1]) == 2
        else labels[-2:]
    )
    if per_host.get(site, 0) >= PER_HOST:
        continue
    per_host[site] = per_host.get(site, 0) + 1
    chosen.append(url)
    if len(chosen) == TARGET:
        break

(HERE / "urls.txt").write_text("\n".join(chosen) + "\n", encoding="utf-8")
print(f"seed pages ok: {sum(r['outcome'] == 'ok' for r in records)}/{len(SEEDS)}")
print(f"candidate links: {len(candidates)}, chosen: {len(chosen)}, distinct sites: {len(per_host)}")
