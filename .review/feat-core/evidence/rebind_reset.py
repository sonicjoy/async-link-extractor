"""DNS rebinding plus a connection reset (fix review round 2, finding 1), over real sockets.

An "internal" server on 127.0.0.1 answers with a page and then resets the connection
(SO_LINGER 0), so the client can no longer read the peer address. A lying resolver tells
the address check that "localhost" is public. Every record must be refused.

Run:  uv run python .review/feat-core/evidence/rebind_reset.py
"""

import asyncio
import io
import json
import socket
import struct
import threading

from linkx.models import Job, Settings
from linkx.pipeline import extract

BODY = b'<a href="http://internal.secret/admin">secret</a>'
RESPONSE = (
    b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\n"
    + f"content-length: {len(BODY)}\r\n\r\n".encode()
    + BODY
)


def serve(listener: socket.socket) -> None:
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        conn.recv(65536)
        conn.sendall(RESPONSE)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()  # RST, not FIN


async def lying_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


async def main() -> None:
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    threading.Thread(target=serve, args=(listener,), daemon=True).start()
    jobs = [Job(i, f"http://localhost:{port}/p{i}") for i in range(40)]
    stream = io.StringIO()
    settings = Settings(concurrency=4, retries=0, progress_interval=0)
    await extract(jobs, settings, stream, resolve=lying_resolver)
    listener.close()
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    kinds: dict[str, int] = {}
    for record in records:
        key = record["outcome"] if record["error"] is None else record["error"]["kind"]
        kinds[key] = kinds.get(key, 0) + 1
    leaked = sum(1 for r in records if r["links"])
    print(f"40 fetches of an internal page that resets after answering: {kinds}")
    print(f"records with internal links written out: {leaked}")


if __name__ == "__main__":
    asyncio.run(main())
