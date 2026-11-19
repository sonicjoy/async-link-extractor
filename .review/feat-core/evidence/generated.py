"""Generated inputs for the pure functions that take outside input (fixed seed, replayable).

Properties:
  P1 extract_links never raises on arbitrary markup.
  P2 every link it returns is absolute http(s), has a host, has no fragment, and appears once.
  P3 dedupe_key is idempotent: key(key(u)) == key(u).
  P4 is_public is False for every address inside the reserved ranges sampled below.
Pass --break to run P2 against a deliberately wrong extractor; it must report failures.

Run:  uv run python .review/feat-core/evidence/generated.py [--break]
"""

import ipaddress
import random
import sys
from urllib.parse import urlsplit

from linkx.cli import dedupe_key
from linkx.fetch import is_public
from linkx.parse import extract_links

SEED = 20261119
CASES = 20_000
rng = random.Random(SEED)

HREFS = [
    "", " ", "#", "#x", "/a", "a/b", "../c", "./d?x=1#f", "//cdn.test/e", "http://h.test/f#g",
    "https://H.TEST:443/G", "javascript:alert(1)", "mailto:a@b", "tel:1", "data:,x", "ftp://f/",
    "http://[::1]/v6", "http://[broken", "http://exa mple.test/", "été", "%zz", "?q=1",
    "http://a.test:99999/", "HTTP://UPPER.TEST/", "  /trim  ", "http:/one-slash", "http://",
    "\x00", "\t/tab\n", "http://user:pw@h.test/", "https://xn--bcher-kva.example/",
]
TAGS = ["a", "A", "base", "link", "p", "div", "img", "area", "script", "!--", "/a", "a/"]


def fragment() -> str:
    tag = rng.choice(TAGS)
    href = rng.choice(HREFS)
    quote = rng.choice(['"', "'", ""])
    roll = rng.random()
    if roll < 0.1:
        return rng.choice(["<", ">", "&amp;", "<!DOCTYPE", "<![CDATA[", "-->", "</", "<a href="])
    if roll < 0.2:
        return "".join(chr(rng.randint(0, 0x2FF)) for _ in range(rng.randint(1, 8)))
    return f"<{tag} href={quote}{href}{quote}>text</{tag}>"


def page() -> str:
    return "".join(fragment() for _ in range(rng.randint(0, 25)))


def wrong_extractor(html: str, base: str) -> list[str]:
    links = extract_links(html, base)
    return [*links, *(link + "#frag" for link in links[:1]), *links[:1]]


def main() -> int:
    extractor = wrong_extractor if "--break" in sys.argv else extract_links
    failures: list[str] = []
    bases = ["http://site.test/dir/page.html", "https://site.test", "http://[::1]:8080/x/"]

    for case in range(CASES):
        html, base = page(), rng.choice(bases)
        try:
            links = extractor(html, base)
        except Exception as exc:  # P1
            failures.append(f"P1 case {case}: {type(exc).__name__}: {exc}: {html[:80]!r}")
            continue
        if len(links) != len(set(links)):
            failures.append(f"P2 case {case}: duplicate in {links}")
        for link in links:
            parts = urlsplit(link)
            if parts.scheme not in ("http", "https") or not parts.netloc or parts.fragment or "#" in link:
                failures.append(f"P2 case {case}: bad link {link!r}")

    for case in range(CASES):
        scheme = rng.choice(["http", "https", "HTTP", "ftp", ""])
        host = rng.choice(["a.test", "A.Test", "[::1]", "127.0.0.1", "b.test:80", "b.test:443", "c.test:8080", ""])
        path = rng.choice(["", "/", "/x", "/x/", "/X"])
        url = f"{scheme}://{host}{path}{rng.choice(['', '?b=1&a=2'])}{rng.choice(['', '#f'])}"
        once = dedupe_key(url)
        if dedupe_key(once) != once:  # P3
            failures.append(f"P3: {url!r} -> {once!r} -> {dedupe_key(once)!r}")

    reserved = [
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4", "::1/128",
        "fc00::/7", "fe80::/10", "ff00::/8",
    ]
    for net in map(ipaddress.ip_network, reserved):
        for _ in range(500):
            address = net[rng.randrange(net.num_addresses)]
            if is_public(str(address)):  # P4
                failures.append(f"P4: {address} in {net} reported public")
            if address.version == 4 and is_public(f"::ffff:{address}"):
                failures.append(f"P4: ::ffff:{address} reported public")

    print(f"seed {SEED}: {CASES} pages, {CASES} URLs, {len(reserved) * 500} reserved addresses")
    for line in failures[:15]:
        print("FAIL", line)
    print(f"{len(failures)} property violations{' (wrong extractor, --break)' if '--break' in sys.argv else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
