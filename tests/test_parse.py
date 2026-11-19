"""Link extraction: one test per rule in the design, plus malformed markup and an empty page."""

import pytest

from linkx.parse import extract_links

PAGE = "http://site.test/dir/page.html"


def links(body: str, base_url: str = PAGE) -> list[str]:
    return extract_links(body, base_url)


def test_relative_links_resolve_against_the_page_url() -> None:
    body = '<a href="other.html">o</a><a href="/root">r</a><a href="../up">u</a>'
    assert links(body) == [
        "http://site.test/dir/other.html",
        "http://site.test/root",
        "http://site.test/up",
    ]


def test_absolute_and_protocol_relative_links() -> None:
    body = '<a href="https://x.test/a">a</a><a href="//cdn.test/b">b</a>'
    assert links(body) == ["https://x.test/a", "http://cdn.test/b"]


def test_base_href_changes_the_resolution_base() -> None:
    body = '<head><base href="https://other.test/base/"></head><a href="x">x</a>'
    assert links(body) == ["https://other.test/base/x"]


def test_relative_base_href_resolves_against_the_page_url() -> None:
    body = '<base href="/b/"><a href="x">x</a>'
    assert links(body) == ["http://site.test/b/x"]


def test_only_the_first_base_counts() -> None:
    body = '<base href="https://one.test/"><base href="https://two.test/"><a href="x">x</a>'
    assert links(body) == ["https://one.test/x"]


def test_base_with_a_non_http_scheme_is_ignored() -> None:
    body = '<base href="javascript:void(0)/"><a href="x">x</a>'
    assert links(body) == ["http://site.test/dir/x"]


def test_duplicates_are_removed_keeping_first_seen_order() -> None:
    body = (
        '<a href="/b">1</a><a href="/a">2</a><a href="/b">3</a><a href="http://site.test/a">4</a>'
    )
    assert links(body) == ["http://site.test/b", "http://site.test/a"]


@pytest.mark.parametrize(
    "href",
    ["javascript:alert(1)", "mailto:me@site.test", "tel:+441234", "ftp://files.test/x", "data:,x"],
)
def test_non_http_schemes_are_skipped(href: str) -> None:
    assert links(f'<a href="{href}">x</a>') == []


@pytest.mark.parametrize("href", ["#top", "#", "", "   "])
def test_fragment_only_and_empty_hrefs_are_skipped(href: str) -> None:
    assert links(f'<a href="{href}">x</a>') == []


def test_fragments_are_removed_from_kept_links() -> None:
    assert links('<a href="/a#part">x</a><a href="/a">y</a>') == ["http://site.test/a"]


def test_anchor_without_href_is_ignored() -> None:
    assert links('<a name="x">x</a><a href="/y">y</a>') == ["http://site.test/y"]


def test_surrounding_whitespace_in_href_is_ignored() -> None:
    assert links('<a href="  /a  ">x</a>') == ["http://site.test/a"]


def test_unresolvable_href_is_skipped_without_failing_the_page() -> None:
    assert links('<a href="http://[broken/x">x</a><a href="/ok">ok</a>') == ["http://site.test/ok"]


def test_case_and_entities_are_handled() -> None:
    assert links('<A HREF="/a?x=1&amp;y=2">x</A>') == ["http://site.test/a?x=1&y=2"]


def test_malformed_html_still_yields_links() -> None:
    body = '<html><body><div><a href="/a">unclosed <p><a href=/b>no quotes</div><a href="/c"'
    assert links(body) == ["http://site.test/a", "http://site.test/b"]


def test_empty_page_has_no_links() -> None:
    assert links("") == []


def test_only_anchor_tags_count() -> None:
    body = '<link href="/style.css"><img src="/i.png"><area href="/map"><a href="/a">a</a>'
    assert links(body) == ["http://site.test/a"]
