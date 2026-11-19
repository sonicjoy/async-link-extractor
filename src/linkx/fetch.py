"""Fetch one URL: validation, address checks, per-host delay, timeout, size cap and retries.

`Fetcher.fetch` never raises `Exception`: every failure becomes a `FetchResult`
with an error, so one bad URL cannot affect any other. Cancellation is a
`BaseException` and passes through untouched.
"""

import asyncio
import codecs
import ipaddress
import logging
import random
import socket
import time
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


def is_public(address: str) -> bool:
    """True for a globally routable unicast address."""
    ip = ipaddress.ip_address(address.partition("%")[0])  # drop an IPv6 zone such as %eth0
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def make_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """One shared client: a connection pool sized to the worker count."""
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        max_redirects=settings.max_redirects,
        timeout=settings.timeout,
        limits=httpx.Limits(
            max_connections=settings.concurrency,
            max_keepalive_connections=settings.concurrency,
        ),
        headers={
            "user-agent": f"linkx/{__version__}",
            "accept": "text/html,application/xhtml+xml",
        },
    )


def _codec_name(charset: str | None) -> str:
    try:
        return codecs.lookup(charset or "utf-8").name
    except LookupError:
        return "utf-8"


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
        self._client = client
        self._settings = settings
        self._limiter = limiter or HostLimiter(settings.per_host_delay)
        self._resolve = resolve
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        # httpx calls request hooks before the first request and before every redirect
        # hop, so each hop gets the same scheme and address checks.
        hooks = client.event_hooks
        client.event_hooks = {**hooks, "request": [*hooks["request"], self._check_request]}

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
                async with self._client.stream("GET", url) as response:
                    return await self._read_page(response)
        except TimeoutError:
            raise FetchFailure(
                ErrorKind.TIMEOUT, f"no complete response within {timeout}s", retryable=True
            ) from None
        except httpx.TimeoutException as exc:
            raise FetchFailure(ErrorKind.TIMEOUT, _describe(exc), retryable=True) from None
        except httpx.TooManyRedirects:
            raise FetchFailure(
                ErrorKind.TOO_MANY_REDIRECTS,
                f"more than {self._settings.max_redirects} redirects",
            ) from None
        except httpx.TransportError as exc:
            raise FetchFailure(ErrorKind.CONNECT, _describe(exc), retryable=True) from None

    async def _read_page(self, response: httpx.Response) -> tuple[str, int, str]:
        final_url = str(response.url)
        status = response.status_code

        def fail(kind: ErrorKind, message: str, *, retryable: bool = False) -> FetchFailure:
            return FetchFailure(
                kind, message, retryable=retryable, final_url=final_url, http_status=status
            )

        if not response.is_success:
            raise fail(
                ErrorKind.HTTP_STATUS, f"HTTP {status}", retryable=status >= 500 or status == 429
            )
        content_type = response.headers.get("content-type", "")
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type not in HTML_MEDIA_TYPES:
            raise fail(ErrorKind.NOT_HTML, f"content type {media_type or 'missing'!r} is not HTML")

        limit = self._settings.max_bytes
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise fail(ErrorKind.TOO_LARGE, f"declared length {declared} exceeds {limit} bytes")

        # aiter_bytes yields decompressed bytes, so a small gzip body that expands past the
        # cap is caught as it streams. Decoding is incremental so a multi-byte character
        # split across chunks is not corrupted.
        decoder = codecs.getincrementaldecoder(_codec_name(response.charset_encoding))("replace")
        parts: list[str] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise fail(ErrorKind.TOO_LARGE, f"body exceeds {limit} bytes")
            parts.append(decoder.decode(chunk))
        parts.append(decoder.decode(b"", final=True))
        return final_url, status, "".join(parts)

    async def _check_request(self, request: httpx.Request) -> None:
        """Request hook: runs before the first request and every redirect hop."""
        url = validate_url(request.url)
        if not self._settings.allow_private:
            await self._check_public(url)

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


def _describe(exc: Exception) -> str:
    detail = str(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
