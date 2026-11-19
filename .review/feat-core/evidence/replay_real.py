"""Replay real public pages through the real CLI, and cross-check the parser.

Fetches a handful of public pages with linkx itself, then for each ok page re-fetches the
HTML and counts anchors with an independent method (a regex over <a ... href=...>) to check
linkx is not silently missing links. The regex over-counts (it keeps javascript:, mailto:,
fragments and duplicates), so the check is: linkx count <= distinct regex hrefs, and every
linkx link is absolute http(s) with no fragment.

Run:  uv run python .review/feat-core/evidence/replay_real.py
"""

import io
import json
import re
import sys
from contextlib import redirect_stdout
from html import unescape
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from linkx.cli import main

PAGES = [
    "https://www.python.org/",
    "https://docs.python.org/3/library/asyncio-queue.html",
    "https://en.wikipedia.org/wiki/Web_crawler",
    "https://www.rfc-editor.org/rfc/rfc9110.html",
    "https://news.ycombinator.com/",
    "https://github.com/encode/httpx",
]
ANCHOR = re.compile(r"<a\b[^>]*?\bhref\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


def main_check() -> int:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main([*PAGES, "--timeout", "20", "--max-bytes", "10000000"])
    records = sorted(map(json.loads, buffer.getvalue().splitlines()), key=lambda r: r["index"])
    print(f"linkx exit code {code}, {len(records)} records for {len(PAGES)} pages")
    failures = 0
    for record in records:
        if record["outcome"] != "ok":
            print(f"  {record['url']}: {record['outcome']} {record['error']}")
            continue
        links = record["links"]
        bad = [u for u in links if urlsplit(u).scheme not in ("http", "https") or "#" in u]
        response = httpx.get(
            record["url"], follow_redirects=True, timeout=20, headers={"user-agent": "linkx/0.1.0"}
        )
        html = response.text
        base_tag = re.search(r"<base\b[^>]*\bhref\s*=\s*[\"']([^\"']+)", html, re.IGNORECASE)
        base = urljoin(str(response.url), base_tag.group(1)) if base_tag else str(response.url)
        independent = set()
        for match in ANCHOR.finditer(html):
            href = unescape(match.group(1).strip("'\"")).strip()
            if not href or href.startswith("#"):
                continue
            absolute = urldefrag(urljoin(base, href))[0]
            if urlsplit(absolute).scheme in ("http", "https"):
                independent.add(absolute)
        only_linkx = set(links) - independent
        only_regex = independent - set(links)
        ok = not bad and len(links) == len(set(links)) and not only_linkx and not only_regex
        failures += not ok
        print(
            f"  {'PASS' if ok else 'DIFF'} {record['url']}: linkx {len(links)}, regex {len(independent)}, "
            f"only linkx {len(only_linkx)}, only regex {len(only_regex)}, malformed {len(bad)}"
        )
        for extra in sorted(only_linkx | only_regex)[:3]:
            print(f"      e.g. {'linkx' if extra in only_linkx else 'regex'} only: {extra}")
    return 1 if failures or code != 0 else 0


if __name__ == "__main__":
    sys.exit(main_check())
