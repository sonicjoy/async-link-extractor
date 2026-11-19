"""Fetch one URL: validation, address checks, per-host delay, timeout, size cap and retries.

`Fetcher.fetch` never raises `Exception`: every failure becomes a `FetchResult`
with an error, so one bad URL cannot affect any other. Cancellation is a
`BaseException` and passes through untouched.
"""

import asyncio
import ipaddress
import logging
import random
import socket
import time
import zlib
from collections.abc import Awaitable, Callable

import httpx

from linkx import __version__
from linkx.models import ErrorKind, FetchError, FetchResult, Job, Settings
from linkx.ratelimit import HostLimiter

log = logging.getLogger(__name__)

HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
DEFAULT_PORTS = {"http": 80, "https": 443}
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 5.0

type Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolve(host: str, port: int) -> list[str]:
    """Resolve a host name with the event loop's non-blocking getaddrinfo."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class FetchFailure(Exception):
    """A failure with a known kind. Raised inside the fetcher, never outside it."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        retryable: bool = False,
        final_url: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.final_url = final_url
        self.http_status = http_status


def validate_url(url: str | httpx.URL) -> httpx.URL:
    """Accept only absolute http(s) URLs with a host."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise FetchFailure(ErrorKind.INVALID_URL, f"invalid URL: {exc}") from None
    if parsed.scheme not in DEFAULT_PORTS:
        reason = f"unsupported scheme {parsed.scheme!r}" if parsed.scheme else "no scheme"
        raise FetchFailure(ErrorKind.INVALID_URL, f"{reason} in {str(url)!r}")
    if not parsed.host:
        raise FetchFailure(ErrorKind.INVALID_URL, f"no host in {str(url)!r}")
    return parsed


NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")  # RFC 6052: last 32 bits are IPv4
IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")  # deprecated ::a.b.c.d form
NOT_GLOBAL_EXTRA = (  # treated as global by the ipaddress module, but not public targets
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.IPv4Network("192.88.99.0/24"),  # former 6to4 relay anycast
)


def is_public(address: str) -> bool:
    """True for a globally routable unicast address.

    An IPv6 address that embeds an IPv4 one (mapped, NAT64, IPv4-compatible) is judged
    by the IPv4 address inside it, because that is where a gateway would send it.
    """
    ip = ipaddress.ip_address(address.partition("%")[0])  # drop an IPv6 zone such as %eth0
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in NAT64_PREFIX or (ip in IPV4_COMPATIBLE and int(ip) > 1):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if any(ip in network for network in NOT_GLOBAL_EXTRA if ip.version == 4):
        return False
    return ip.is_global and not ip.is_multicast


def make_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """One shared client: a connection pool sized to the worker count."""
    return httpx.AsyncClient(
        transport=transport,
        # No proxies from the environment: the address checks must see the real server,
        # and a proxy would resolve names on its own.
        trust_env=False,
        # Fetcher follows redirects itself: httpx would read every 3xx body in full,
        # outside the size cap.
        follow_redirects=False,
        timeout=settings.timeout,
        limits=httpx.Limits(
            max_connections=settings.concurrency,
            max_keepalive_connections=settings.concurrency,
        ),
        headers={
            "user-agent": f"linkx/{__version__}",
            "accept": "text/html,application/xhtml+xml",
            # Exactly what _read_page can decompress in bounded steps.
            "accept-encoding": "gzip, deflate",
        },
    )


def _decode(body: bytes, charset: str | None) -> str:
    """Decode with the declared charset; fall back to UTF-8 if it is unknown, not a text
    encoding (LookupError: base64, rot13) or cannot decode at all (ValueError, which
    includes UnicodeError: idna, undefined). The charset is the server's choice."""
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except (LookupError, ValueError):
        return body.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        limiter: HostLimiter | None = None,
        resolve: Resolver = system_resolve,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        # The keyword arguments exist so tests run with no network and no real waits.
        # `clock` times a fetch (perf_counter has the resolution for milliseconds); the
        # limiter keeps its own clock and sleep, since it schedules rather than measures.
        self._client = client
        self._settings = settings
        self._limiter = limiter or HostLimiter(settings.per_host_delay)
        self._resolve = resolve
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        # httpx calls request hooks before every request, redirect hops included, and
        # response hooks after each response's headers, before its body is read. So every
        # hop is checked twice: the resolved name before connecting, the connected peer after.
        hooks = client.event_hooks
        client.event_hooks = {
            "request": [*hooks["request"], self._check_request],
            "response": [*hooks["response"], self._check_response],
        }

    async def fetch(self, job: Job) -> FetchResult:
        started = self._clock()
        attempts = 0
        try:
            url = validate_url(job.url)
            while True:
                attempts += 1
                log.debug(
                    "[#%d] attempt %d: GET %s",
                    job.index,
                    attempts,
                    job.url,
                    extra={"event": "attempt", "index": job.index, "attempt": attempts},
                )
                try:
                    final_url, status, html = await self._attempt(url)
                except FetchFailure as failure:
                    if not failure.retryable or attempts > self._settings.retries:
                        raise
                    delay = self._backoff(attempts)
                    log.warning(
                        "[#%d] attempt %d failed (%s: %s); retrying in %.2fs",
                        job.index,
                        attempts,
                        failure.kind,
                        failure,
                        delay,
                        extra={
                            "event": "retry",
                            "index": job.index,
                            "url": job.url,
                            "attempt": attempts,
                            "kind": str(failure.kind),
                            "delay_s": round(delay, 3),
                        },
                    )
                    await self._sleep(delay)
                else:
                    return self._result(
                        job, started, attempts, final_url=final_url, http_status=status, html=html
                    )
        except FetchFailure as failure:
            error = FetchError(failure.kind, str(failure))
            return self._result(
                job,
                started,
                attempts,
                final_url=failure.final_url,
                http_status=failure.http_status,
                error=error,
            )
        except Exception as exc:  # isolation: anything unforeseen is this job's error only
            log.debug("[#%d] unexpected error", job.index, exc_info=True)
            error = FetchError(ErrorKind.UNEXPECTED, f"{type(exc).__name__}: {exc}")
            return self._result(job, started, attempts, error=error)

    def _backoff(self, retry: int) -> float:
        """Full jitter: a random wait between 0 and min(cap, base * 2**retry)."""
        return self._rng() * min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2.0**retry)

    def _result(
        self,
        job: Job,
        started: float,
        attempts: int,
        *,
        final_url: str | None = None,
        http_status: int | None = None,
        html: str | None = None,
        error: FetchError | None = None,
    ) -> FetchResult:
        return FetchResult(
            job=job,
            final_url=final_url,
            http_status=http_status,
            html=html,
            error=error,
            elapsed_ms=round((self._clock() - started) * 1000),
            attempts=attempts,
        )

    async def _attempt(self, url: httpx.URL) -> tuple[str, int, str]:
        """One attempt: wait for the host's slot, then request, redirects and body under one
        timeout. The slot wait is outside the timeout, so queueing for a busy host is not
        mistaken for a slow server."""
        await self._limiter.wait(url.host)
        timeout = self._settings.timeout
        try:
            async with asyncio.timeout(timeout):
                return await self._follow(self._client.build_request("GET", url))
        except TimeoutError:
            raise FetchFailure(
                ErrorKind.TIMEOUT, f"no complete response within {timeout}s", retryable=True
            ) from None
        except httpx.TimeoutException as exc:
            raise FetchFailure(ErrorKind.TIMEOUT, _describe(exc), retryable=True) from None
        except httpx.InvalidURL as exc:  # a redirect to javascript:, mailto: and the like
            raise FetchFailure(
                ErrorKind.INVALID_URL, f"redirect to an invalid URL: {exc}"
            ) from None
        except httpx.TransportError as exc:
            raise FetchFailure(ErrorKind.CONNECT, _describe(exc), retryable=True) from None

    async def _follow(self, request: httpx.Request) -> tuple[str, int, str]:
        """Send the request and follow up to `max_redirects` redirects.

        Each 3xx is closed without reading its body: the only body ever read is the final
        one, through the size cap. httpx builds the next request (`next_request`),
        resolving a relative Location; an unparseable one raises RemoteProtocolError.
        """
        for _ in range(self._settings.max_redirects + 1):
            response = await self._client.send(request, stream=True)
            try:
                if response.next_request is None:
                    return await self._read_page(response)
                request = response.next_request
            finally:
                await response.aclose()
        raise FetchFailure(
            ErrorKind.TOO_MANY_REDIRECTS, f"more than {self._settings.max_redirects} redirects"
        )

    async def _read_page(self, response: httpx.Response) -> tuple[str, int, str]:
        final_url = str(response.url)
        status = response.status_code

        def fail(kind: ErrorKind, message: str, retryable: bool = False) -> FetchFailure:
            return FetchFailure(
                kind, message, retryable=retryable, final_url=final_url, http_status=status
            )

        if not response.is_success:
            raise fail(ErrorKind.HTTP_STATUS, f"HTTP {status}", status >= 500 or status == 429)
        content_type = response.headers.get("content-type", "")
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type not in HTML_MEDIA_TYPES:
            raise fail(ErrorKind.NOT_HTML, f"content type {media_type or 'missing'!r} is not HTML")

        limit = self._settings.max_bytes
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:  # bytes on the wire; fail early
            raise fail(ErrorKind.TOO_LARGE, f"declared length {declared} exceeds {limit} bytes")
        body = await _read_body(response, limit, fail)
        return final_url, status, _decode(body, response.charset_encoding)

    async def _check_request(self, request: httpx.Request) -> None:
        """Request hook: runs before the first request and every redirect hop.

        A failure raised here happens before that hop's response exists, so its record
        has no `final_url` or `http_status`, even when an earlier hop answered with a 3xx.
        """
        url = validate_url(request.url)
        if not self._settings.allow_private:
            await self._check_public(url)

    async def _check_response(self, response: httpx.Response) -> None:
        """Response hook: refuse a connection that reached a non-public address.

        The request hook resolved the name itself; httpx then resolves it again to
        connect, and a hostile DNS server can answer differently the second time (DNS
        rebinding). The connected peer is the address that was really used. The request
        has been sent by now, but nothing the server returned is read or written out.
        """
        if self._settings.allow_private:
            return
        stream = response.extensions.get("network_stream")
        if stream is None:  # no network connection: an in-memory transport, as in tests
            return
        # Fail closed: once a socket is reset its address can no longer be read, and an
        # unknown peer must not be trusted.
        peer = stream.get_extra_info("server_addr")
        address = str(peer[0]) if peer else None
        if address is None or not is_public(address):
            raise FetchFailure(
                ErrorKind.BLOCKED_ADDRESS,
                f"{response.url.host} connected to non-public address {address or 'unknown'}"
                " (use --allow-private to permit)",
            )

    async def _check_public(self, url: httpx.URL) -> None:
        host = url.host
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:  # a name, not an address literal
            try:
                addresses = await self._resolve(host, url.port or DEFAULT_PORTS[url.scheme])
            except OSError as exc:
                raise FetchFailure(
                    ErrorKind.CONNECT, f"cannot resolve {host}: {exc}", retryable=True
                ) from None
        if not addresses:
            raise FetchFailure(ErrorKind.CONNECT, f"{host} resolved to no addresses")
        for address in addresses:
            if not is_public(address):
                raise FetchFailure(
                    ErrorKind.BLOCKED_ADDRESS,
                    f"{host} resolves to non-public address {address}"
                    " (use --allow-private to permit)",
                )


async def _read_body(
    response: httpx.Response, limit: int, fail: Callable[[ErrorKind, str], FetchFailure]
) -> bytes:
    """Read the body, decompressed, refusing it as soon as it passes `limit` bytes.

    Decompression is done here rather than by httpx: httpx inflates a whole network chunk
    at once, so 64 KB of gzip could become 64 MB in memory before any cap saw it. zlib's
    `max_length` lets each step produce at most what the cap still allows.
    """
    too_large = f"body exceeds {limit} bytes"
    if response.is_stream_consumed:
        # The transport handed back a response already read into memory and decoded by
        # httpx (httpx.MockTransport does this for bytes content). Nothing left to bound.
        if len(response.content) > limit:
            raise fail(ErrorKind.TOO_LARGE, too_large)
        return response.content

    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        inflate = None
    elif encoding in ("gzip", "x-gzip", "deflate"):
        inflate = zlib.decompressobj(zlib.MAX_WBITS | 32)  # gzip or zlib header, detected
    else:
        raise fail(ErrorKind.UNEXPECTED, f"unsupported content encoding {encoding!r}")

    body = bytearray()
    try:
        async for raw in response.aiter_raw():
            # _inflate drains its input completely, so no output is left inside zlib.
            body += raw if inflate is None else _inflate(inflate, raw, limit - len(body))
            if len(body) > limit:
                raise fail(ErrorKind.TOO_LARGE, too_large)
    except zlib.error as exc:
        raise fail(ErrorKind.UNEXPECTED, f"corrupt {encoding} body: {exc}") from None
    return bytes(body)


def _inflate(inflate: "zlib._Decompress", data: bytes, room: int) -> bytes:
    """Decompress `data`, stopping once the output passes `room` bytes (room + 1 is enough
    for the caller to see that the cap was crossed)."""
    out = bytearray()
    while data and len(out) <= room:
        out += inflate.decompress(data, room - len(out) + 1)
        data = inflate.unconsumed_tail
    return bytes(out)


def _describe(exc: Exception) -> str:
    detail = str(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
