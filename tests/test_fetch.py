"""The fetcher: one test per fetch error kind and per retry rule, plus redirects and address checks.

httpx.MockTransport stands in for the network. DNS, backoff sleeps, randomness and the
clock are replaced so the tests are fast and deterministic.
"""

import asyncio
import encodings.aliases
import gzip
import inspect
import socket
import tracemalloc
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace

import httpx
import pytest

from linkx.fetch import Fetcher, _decode, _inflate, is_public, make_client, system_resolve
from linkx.models import ErrorKind, FetchResult, Job, Settings
from linkx.ratelimit import HostLimiter

type Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]

PUBLIC_IP = "93.184.216.34"
SETTINGS = Settings(timeout=1.0, retries=2, max_bytes=1_000)


class RawStream(httpx.AsyncByteStream):
    """Body bytes exactly as a server sends them, delivered as a stream."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._data


def as_network_response(response: httpx.Response) -> httpx.Response:
    """httpx reads a Response built from bytes into memory at once, but a real transport
    streams it. Re-wrap such responses so the fetcher's streaming path is what is tested."""
    if isinstance(response.stream, httpx.ByteStream):
        raw = b"".join(response.stream)  # the bytes as sent, before content decoding
        return httpx.Response(response.status_code, headers=response.headers, stream=RawStream(raw))
    return response


def raw(status: int, headers: dict[str, str], body: bytes) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=RawStream(body))


def html(body: str = "<a href='/x'>x</a>", **kwargs: object) -> httpx.Response:
    headers = {"content-type": "text/html; charset=utf-8"}
    return httpx.Response(200, headers=headers, text=body, **kwargs)  # type: ignore[arg-type]


async def resolve_public(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


@dataclass
class Harness:
    fetcher: Fetcher
    requests: list[str] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)

    async def fetch(self, url: str) -> FetchResult:
        return await self.fetcher.fetch(Job(0, url))


@asynccontextmanager
async def harness(
    handler: Handler,
    settings: Settings = SETTINGS,
    *,
    resolve: Callable[[str, int], Awaitable[list[str]]] = resolve_public,
    limiter: HostLimiter | None = None,
    rng: Callable[[], float] = lambda: 1.0,
) -> AsyncIterator[Harness]:
    requests: list[str] = []
    sleeps: list[float] = []
    resolved: list[str] = []
    clock = [0.0]

    async def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        response = handler(request)
        if inspect.isawaitable(response):
            response = await response
        return as_network_response(response)

    async def recording_resolve(host: str, port: int) -> list[str]:
        resolved.append(host)
        return await resolve(host, port)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    transport = httpx.MockTransport(recording_handler)
    async with make_client(settings, transport=transport) as client:
        fetcher = Fetcher(
            client,
            settings,
            limiter=limiter,
            resolve=recording_resolve,
            sleep=fake_sleep,
            rng=rng,
            clock=lambda: clock[0],
        )
        yield Harness(fetcher, requests, sleeps, resolved)


def sequence(*responses: httpx.Response) -> Handler:
    """A handler that returns the given responses in order, repeating the last one."""
    remaining = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return handle


def raising(exc_type: type[Exception]) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        if issubclass(exc_type, httpx.RequestError):
            raise exc_type("simulated", request=request)
        raise exc_type("simulated")

    return handle


# --- success -----------------------------------------------------------------------------


async def test_html_page_is_fetched() -> None:
    async with harness(sequence(html("<p>hi</p>"))) as h:
        result = await h.fetch("http://a.test/page")
    assert result.error is None
    assert result.html == "<p>hi</p>"
    assert result.http_status == 200
    assert result.final_url == "http://a.test/page"
    assert result.attempts == 1


async def test_body_is_decoded_with_the_declared_charset() -> None:
    response = httpx.Response(
        200, headers={"content-type": "text/html; charset=iso-8859-1"}, content=b"caf\xe9"
    )
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.html == "café"


async def test_undecodable_bytes_are_replaced_not_fatal() -> None:
    response = httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok \xff\xfe")
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None
    assert result.html is not None
    assert result.html.startswith("ok ")


async def test_unknown_charset_falls_back_to_utf8() -> None:
    response = httpx.Response(
        200, headers={"content-type": "text/html; charset=no-such-codec"}, content=b"caf\xc3\xa9"
    )
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.html == "café"


async def test_xhtml_is_accepted() -> None:
    response = httpx.Response(
        200, headers={"content-type": "application/xhtml+xml"}, content=b"<a href='/x'/>"
    )
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None


async def test_redirect_is_followed_and_final_url_reported() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return html()

    async with harness(handle) as h:
        result = await h.fetch("http://a.test/old")
    assert result.error is None
    assert result.final_url == "http://a.test/new"
    assert h.requests == ["http://a.test/old", "http://a.test/new"]


# --- error kinds -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "example.com/page",
        "ftp://a.test/file",
        "http://",
        "https:///path",
        "http://[::1/broken",
    ],
)
async def test_invalid_url_sends_no_request(url: str) -> None:
    async with harness(sequence(html())) as h:
        result = await h.fetch(url)
    assert result.error is not None
    assert result.error.kind == ErrorKind.INVALID_URL
    assert result.attempts == 0
    assert h.requests == []


async def test_http_error_status_is_reported_and_4xx_not_retried() -> None:
    async with harness(sequence(httpx.Response(404))) as h:
        result = await h.fetch("http://a.test/missing")
    assert result.error is not None
    assert result.error.kind == ErrorKind.HTTP_STATUS
    assert result.http_status == 404
    assert result.final_url == "http://a.test/missing"
    assert result.attempts == 1
    assert h.sleeps == []


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", None])
async def test_non_html_is_rejected_without_retry(content_type: str | None) -> None:
    headers = {} if content_type is None else {"content-type": content_type}
    async with harness(sequence(httpx.Response(200, headers=headers, content=b"{}"))) as h:
        result = await h.fetch("http://a.test/data")
    assert result.error is not None
    assert result.error.kind == ErrorKind.NOT_HTML
    assert result.attempts == 1


async def test_oversize_body_with_content_length_is_rejected() -> None:
    async with harness(sequence(html("x" * 2_000))) as h:
        result = await h.fetch("http://a.test/big")
    assert result.error is not None
    assert result.error.kind == ErrorKind.TOO_LARGE
    assert result.attempts == 1


async def test_oversize_streamed_body_is_cut_off() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(100):
            yield b"x" * 100

    response = httpx.Response(200, headers={"content-type": "text/html"}, content=chunks())
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/stream")
    assert result.error is not None
    assert result.error.kind == ErrorKind.TOO_LARGE


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [("gzip", gzip.compress), ("x-gzip", gzip.compress), ("deflate", zlib.compress)],
)
async def test_compressed_pages_are_decoded(
    encoding: str, compress: Callable[[bytes], bytes]
) -> None:
    body = "<a href='/x'>café</a>".encode()
    headers = {"content-type": "text/html; charset=utf-8", "content-encoding": encoding}
    response = raw(200, headers, compress(body))
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None
    assert result.html == "<a href='/x'>café</a>"


async def test_only_gzip_and_deflate_are_requested() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["accept-encoding"])
        return html()

    async with harness(handle) as h:
        await h.fetch("http://a.test/")
    assert seen == ["gzip, deflate"]


@pytest.mark.parametrize(
    ("encoding", "content"),
    [("br", b"\x1b\x00\x00"), ("gzip", b"not gzip at all"), ("gzip, br", b"x")],
)
async def test_unsupported_or_corrupt_encoding_is_an_error(encoding: str, content: bytes) -> None:
    response = raw(200, {"content-type": "text/html", "content-encoding": encoding}, content)
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.UNEXPECTED
    assert result.attempts == 1


async def test_decompression_bomb_never_holds_much_more_than_the_cap() -> None:
    bomb = gzip.compress(b"\0" * 50_000_000)  # about 50 KB that expands to 50 MB
    response = raw(200, {"content-type": "text/html", "content-encoding": "gzip"}, bomb)
    tracemalloc.start()
    try:
        async with harness(sequence(response)) as h:
            result = await h.fetch("http://a.test/bomb")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.error is not None
    assert result.error.kind == ErrorKind.TOO_LARGE
    assert peak < 5_000_000, f"peak {peak} bytes while decoding a capped body"


@pytest.mark.parametrize(("size", "kind"), [(1_000, None), (1_001, ErrorKind.TOO_LARGE)])
async def test_a_response_already_read_into_memory_is_still_capped(
    size: int, kind: ErrorKind | None
) -> None:
    # A plain MockTransport returns bytes responses already read; no streaming re-wrap here.
    def handle(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * size)
        assert response.is_stream_consumed  # httpx read it at construction
        del response.headers["content-length"]  # so the early length check cannot decide
        return response

    async with make_client(SETTINGS, transport=httpx.MockTransport(handle)) as client:
        fetcher = Fetcher(client, SETTINGS, resolve=resolve_public)
        result = await fetcher.fetch(Job(0, "http://a.test/"))
    assert (result.error.kind if result.error else None) == kind


async def test_redirect_bodies_are_never_read() -> None:
    # httpx reads a 3xx body in full when it follows a redirect itself. linkx follows
    # redirects itself and closes each 3xx unread, so a bomb on a redirect costs nothing.
    bomb = gzip.compress(b"\0" * 50_000_000)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            headers = {"location": "/page", "content-encoding": "gzip", "content-type": "text/html"}
            return raw(302, headers, bomb)
        return html()

    tracemalloc.start()
    try:
        async with harness(handle) as h:
            result = await h.fetch("http://a.test/start")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.error is None
    assert result.final_url == "http://a.test/page"
    assert peak < 5_000_000, f"peak {peak} bytes following a redirect"


@pytest.mark.parametrize(("room", "expected"), [(0, 1), (10, 11), (99, 100), (500, 200)])
def test_inflate_produces_at_most_one_byte_past_the_room(room: int, expected: int) -> None:
    data = zlib.compress(bytes(range(200)))
    inflate = zlib.decompressobj(zlib.MAX_WBITS | 32)
    assert len(_inflate(inflate, data, room)) == expected


def test_every_known_codec_name_decodes_or_falls_back() -> None:
    names = set(encodings.aliases.aliases.values()) | {"idna", "undefined", "base64", "rot13"}
    for name in sorted(names):
        assert isinstance(_decode("café <a>".encode(), name), str), name


@pytest.mark.parametrize("charset", ["base64", "rot13", "hex", "utf-16", "idna", "undefined"])
async def test_non_text_or_unusable_charset_falls_back_to_utf8(charset: str) -> None:
    response = httpx.Response(
        200, headers={"content-type": f"text/html; charset={charset}"}, content=b"caf\xc3\xa9!"
    )
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None
    assert result.html is not None


async def test_size_cap_counts_decompressed_bytes() -> None:
    bomb = gzip.compress(b"x" * 50_000)
    assert len(bomb) < SETTINGS.max_bytes
    response = raw(200, {"content-type": "text/html", "content-encoding": "gzip"}, bomb)
    async with harness(sequence(response)) as h:
        result = await h.fetch("http://a.test/gz")
    assert result.error is not None
    assert result.error.kind == ErrorKind.TOO_LARGE


async def test_attempt_timeout_is_retried() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return html()

    async with harness(slow, replace(SETTINGS, timeout=0.05, retries=1)) as h:
        result = await h.fetch("http://a.test/slow")
    assert result.error is not None
    assert result.error.kind == ErrorKind.TIMEOUT
    assert result.attempts == 2


@pytest.mark.parametrize(
    ("exc_type", "kind"),
    [
        (httpx.ReadTimeout, ErrorKind.TIMEOUT),
        (httpx.ConnectTimeout, ErrorKind.TIMEOUT),
        (httpx.ConnectError, ErrorKind.CONNECT),
        (httpx.ReadError, ErrorKind.CONNECT),
        (httpx.RemoteProtocolError, ErrorKind.CONNECT),
    ],
)
async def test_transport_errors_are_mapped_and_retried(
    exc_type: type[Exception], kind: ErrorKind
) -> None:
    async with harness(raising(exc_type)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == kind
    assert result.attempts == 3
    assert result.final_url is None
    assert result.http_status is None


async def test_redirect_loop_is_too_many_redirects() -> None:
    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": request.url.path})

    async with harness(loop) as h:
        result = await h.fetch("http://a.test/loop")
    assert result.error is not None
    assert result.error.kind == ErrorKind.TOO_MANY_REDIRECTS
    assert result.attempts == 1
    assert len(h.requests) == SETTINGS.max_redirects + 1


async def test_redirect_without_location_is_an_http_status_error() -> None:
    async with harness(sequence(httpx.Response(302))) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.HTTP_STATUS
    assert result.http_status == 302


async def test_unexpected_exception_becomes_an_error_record() -> None:
    async with harness(raising(ValueError)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.UNEXPECTED
    assert "ValueError" in result.error.message
    assert result.attempts == 1


async def test_cancellation_is_not_turned_into_an_error() -> None:
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with harness(hang) as h:
        task = asyncio.create_task(h.fetch("http://a.test/"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- non-public addresses ----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.0.1:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
async def test_non_public_ip_literals_are_blocked(url: str) -> None:
    async with harness(sequence(html())) as h:
        result = await h.fetch(url)
    assert result.error is not None
    assert result.error.kind == ErrorKind.BLOCKED_ADDRESS
    assert result.attempts == 1
    assert h.requests == []
    assert h.resolved == []


async def test_host_resolving_to_a_private_address_is_blocked() -> None:
    async def resolve_mixed(host: str, port: int) -> list[str]:
        return [PUBLIC_IP, "10.0.0.7"]

    async with harness(sequence(html()), resolve=resolve_mixed) as h:
        result = await h.fetch("http://internal.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.BLOCKED_ADDRESS
    assert "10.0.0.7" in result.error.message
    assert h.requests == []


async def test_redirect_to_a_private_address_is_blocked_at_the_hop() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/meta"})

    async with harness(handle) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.BLOCKED_ADDRESS
    assert h.requests == ["http://a.test/"]


async def test_redirect_to_a_non_web_scheme_is_invalid() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    async with harness(handle) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.INVALID_URL
    assert h.requests == ["http://a.test/"]


async def test_allow_private_skips_the_address_check() -> None:
    settings = replace(SETTINGS, allow_private=True)
    async with harness(sequence(html()), settings) as h:
        result = await h.fetch("http://127.0.0.1:8000/")
    assert result.error is None
    assert h.resolved == []


class FakeNetworkStream:
    """Stands in for httpcore's stream: reports which address the connection reached."""

    def __init__(self, peer: str | None) -> None:
        self.peer = peer

    def get_extra_info(self, name: str) -> object:
        if name != "server_addr" or self.peer is None:
            return None  # as anyio reports it once the socket was reset
        return (self.peer, 80)


def connected_to(peer: str | None, response: httpx.Response) -> httpx.Response:
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        stream=RawStream(b"".join(response.stream)),  # type: ignore[arg-type]
        extensions={"network_stream": FakeNetworkStream(peer)},
    )


async def test_connection_that_reached_a_private_address_is_refused() -> None:
    # DNS answered with a public address for the check, but the connection itself went
    # to the metadata address: a rebinding attack. The connected peer decides.
    async with harness(lambda request: connected_to("169.254.169.254", html())) as h:
        result = await h.fetch("http://rebind.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.BLOCKED_ADDRESS
    assert "169.254.169.254" in result.error.message


async def test_redirect_hop_that_connected_to_a_private_address_is_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return connected_to(PUBLIC_IP, httpx.Response(302, headers={"location": "/next"}))
        return connected_to("10.0.0.9", html())

    async with harness(handle) as h:
        result = await h.fetch("http://a.test/start")
    assert result.error is not None
    assert result.error.kind == ErrorKind.BLOCKED_ADDRESS
    assert h.requests == ["http://a.test/start", "http://a.test/next"]


async def test_connection_whose_address_cannot_be_read_is_refused() -> None:
    # Fail closed: a real connection whose peer is unknown (for example reset by the
    # server just after answering) must not be trusted.
    async with harness(lambda request: connected_to(None, html())) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.BLOCKED_ADDRESS
    # The address is unknown, not known to be private, and the advice must not be to turn
    # every protection off.
    assert "could not confirm" in result.error.message
    assert "non-public" not in result.error.message
    assert "--allow-private" not in result.error.message


@pytest.mark.parametrize("target", ["javascript:alert(1)", "mailto:a@b.test"])
async def test_redirect_to_a_non_web_url_is_invalid_and_not_retried(target: str) -> None:
    async with harness(sequence(httpx.Response(302, headers={"location": target}))) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.INVALID_URL
    assert result.attempts == 1


async def test_connection_to_a_public_address_is_accepted() -> None:
    async with harness(lambda request: connected_to(PUBLIC_IP, html())) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None


async def test_allow_private_skips_the_connected_address_check() -> None:
    settings = replace(SETTINGS, allow_private=True)
    async with harness(lambda request: connected_to("127.0.0.1", html()), settings) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None


def test_client_ignores_proxy_settings_from_the_environment() -> None:
    # Through a proxy the connected peer would be the proxy, not the server.
    assert make_client(SETTINGS).trust_env is False


async def test_allow_private_also_follows_a_redirect_to_a_private_address() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.test":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/internal"})
        return html()

    async with harness(handle, replace(SETTINGS, allow_private=True)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None
    assert result.final_url == "http://10.0.0.5/internal"


async def test_resolver_failure_is_a_retried_connect_error() -> None:
    async def fail(host: str, port: int) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    async with harness(sequence(html()), resolve=fail) as h:
        result = await h.fetch("http://nowhere.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.CONNECT
    assert result.attempts == 3


async def test_resolver_returning_nothing_is_a_connect_error() -> None:
    async def empty(host: str, port: int) -> list[str]:
        return []

    async with harness(sequence(html()), replace(SETTINGS, retries=0), resolve=empty) as h:
        result = await h.fetch("http://nowhere.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.CONNECT


async def test_system_resolver_returns_addresses() -> None:
    addresses = await system_resolve("localhost", 80)
    assert addresses
    assert not any(is_public(address) for address in addresses)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        (PUBLIC_IP, True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("100.64.0.1", False),
        ("fe80::1%eth0", False),
        ("::ffff:10.0.0.1", False),
        ("224.0.0.1", False),
        ("64:ff9b::a9fe:a9fe", False),  # NAT64 form of 169.254.169.254
        ("64:ff9b::7f00:1", False),  # NAT64 form of 127.0.0.1
        ("64:ff9b::808:808", True),  # NAT64 form of 8.8.8.8
        ("::127.0.0.1", False),  # IPv4-compatible IPv6
        ("192.0.0.9", False),
        ("192.88.99.1", False),
    ],
)
def test_is_public(address: str, public: bool) -> None:
    assert is_public(address) is public


# --- retries, backoff, timing ------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 429])
async def test_5xx_and_429_are_retried_then_succeed(status: int) -> None:
    async with harness(sequence(httpx.Response(status), html())) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None
    assert result.attempts == 2
    assert h.sleeps == [1.0]


async def test_retries_exhausted_reports_the_last_failure() -> None:
    async with harness(sequence(httpx.Response(503))) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is not None
    assert result.error.kind == ErrorKind.HTTP_STATUS
    assert result.http_status == 503
    assert result.attempts == 3
    assert h.sleeps == [1.0, 2.0]


async def test_backoff_doubles_and_is_capped() -> None:
    settings = replace(SETTINGS, retries=5)
    async with harness(sequence(httpx.Response(503)), settings) as h:
        await h.fetch("http://a.test/")
    assert h.sleeps == [1.0, 2.0, 4.0, 5.0, 5.0]


async def test_backoff_is_jittered_by_the_random_source() -> None:
    async with harness(sequence(httpx.Response(503)), rng=lambda: 0.25) as h:
        await h.fetch("http://a.test/")
    assert h.sleeps == [0.25, 0.5]


async def test_elapsed_time_includes_backoff() -> None:
    async with harness(sequence(httpx.Response(503))) as h:
        result = await h.fetch("http://a.test/")
    assert result.elapsed_ms == 3_000


async def test_retry_is_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    async with harness(sequence(httpx.Response(503), html())) as h:
        await h.fetch("http://a.test/")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert getattr(warnings[0], "event", None) == "retry"
    assert getattr(warnings[0], "index", None) == 0


async def test_host_limiter_wait_does_not_count_against_the_timeout() -> None:
    class SlowLimiter(HostLimiter):
        async def wait(self, host: str) -> None:
            await asyncio.sleep(0.2)

    settings = replace(SETTINGS, timeout=0.1)
    async with harness(sequence(html()), settings, limiter=SlowLimiter(0)) as h:
        result = await h.fetch("http://a.test/")
    assert result.error is None
