"""Extract hyperlinks from an HTML page. Pure and synchronous: it runs in a worker thread."""

from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

WEB_SCHEMES = frozenset({"http", "https"})


class _AnchorCollector(HTMLParser):
    """Collects every `<a href>` value and the first `<base href>`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ("a", "base"):
            return
        href = next((value for name, value in attrs if name == "href"), None)
        if href is None:
            return
        if tag == "a":
            self.hrefs.append(href)
        elif self.base_href is None:
            self.base_href = href


def _absolute_web_url(base: str, href: str) -> str | None:
    """Resolve `href` against `base`; return it without a fragment, or None if unusable."""
    try:
        absolute, _fragment = urldefrag(urljoin(base, href))
        parts = urlsplit(absolute)
    except ValueError:  # for example an unterminated IPv6 literal: http://[broken/x
        return None
    if parts.scheme not in WEB_SCHEMES or not parts.netloc:
        return None
    return absolute


def extract_links(html: str, base_url: str) -> list[str]:
    """Return the absolute http(s) links on the page, de-duplicated in first-seen order.

    Relative links resolve against the first `<base href>` if it is a web URL,
    otherwise against `base_url` (the page's final URL after redirects).
    Empty and fragment-only hrefs, other schemes and unparseable hrefs are skipped.
    """
    collector = _AnchorCollector()
    collector.feed(html)
    collector.close()

    base = base_url
    if collector.base_href is not None:
        base = _absolute_web_url(base_url, collector.base_href.strip()) or base_url

    links: dict[str, None] = {}  # a dict keeps insertion order: an ordered set
    for raw in collector.hrefs:
        href = raw.strip()
        if not href or href.startswith("#"):
            continue
        absolute = _absolute_web_url(base, href)
        if absolute is not None:
            links.setdefault(absolute)
    return list(links)
