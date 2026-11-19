"""Measure peak memory for gzip bombs served over real HTTP (no Content-Length).

Reproduces the security review's setup: a local server sends a gzip of 256 MiB of zeros
with chunked transfer, 10 jobs, 10 workers, --max-bytes 1000. Reports the traced peak.

Run:  uv run python .review/feat-core/evidence/gzip_bomb.py
"""

import asyncio
import gzip
import io
import threading
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from linkx.models import Job, Settings
from linkx.pipeline import extract

BOMB = gzip.compress(b"\0" * (256 * 1024 * 1024), compresslevel=9)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-encoding", "gzip")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        try:
            for start in range(0, len(BOMB), 65536):
                piece = BOMB[start : start + 65536]
                self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/bomb"
    jobs = [Job(i, f"{url}?{i}") for i in range(10)]
    settings = Settings(
        concurrency=10, max_bytes=1000, retries=0, allow_private=True, progress_interval=0
    )
    tracemalloc.start()
    summary = await extract(jobs, settings, io.StringIO())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    server.shutdown()
    print(f"bomb: {len(BOMB)} bytes compressed, 268435456 bytes decompressed, no Content-Length")
    print(f"10 jobs, 10 workers, max_bytes 1000: outcomes {dict(summary.counts)}")
    print(f"peak traced memory: {peak / 1e6:.1f} MB")


if __name__ == "__main__":
    asyncio.run(main())
