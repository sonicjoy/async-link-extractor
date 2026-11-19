"""End to end: the real CLI, real HTTP, a local server, and one bad URL among good ones."""

import io
import json
import socket
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from linkx.cli import main
from linkx.models import Job, Settings
from linkx.pipeline import extract


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(
        '<html><head><base href="/docs/"></head><body>'
        '<a href="intro.html">intro</a> <a href="/about.html#team">about</a>'
        '<a href="mailto:x@y.test">mail</a> <a href="intro.html">again</a></body></html>',
        encoding="utf-8",
    )
    (root / "about.html").write_text('<a href="index.html">home</a>', encoding="utf-8")
    (root / "data.json").write_text("{}", encoding="utf-8")
    handler = partial(QuietHandler, directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_one_bad_url_does_not_affect_the_others(
    server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dead = f"http://127.0.0.1:{closed_port()}/"
    listing = tmp_path / "urls.txt"
    listing.write_text(
        "\n".join(
            [
                f"{server}/index.html",
                dead,
                f"{server}/missing.html",
                f"{server}/data.json",
                "not-a-url",
                f"{server}/about.html",
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "results.jsonl"

    code = main(
        [
            *(str(listing), "--out", str(out), "--allow-private"),
            *("--retries", "0", "--timeout", "5", "--log-json"),
        ]
    )

    assert code == 0
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    summary = next(e["summary"] for e in events if e.get("event") == "summary")
    assert sum(summary["counts"].values()) == 6 == summary["jobs"]
    records = {r["index"]: r for r in map(json.loads, out.read_text("utf-8").splitlines())}
    assert len(records) == 6
    assert records[0]["outcome"] == "ok"
    assert records[0]["links"] == [f"{server}/docs/intro.html", f"{server}/about.html"]
    assert records[1]["outcome"] == "fetch_error"
    assert records[1]["error"]["kind"] == "connect"
    assert records[2]["error"]["kind"] == "http_status"
    assert records[2]["http_status"] == 404
    assert records[3]["error"]["kind"] == "not_html"
    assert records[4]["error"]["kind"] == "invalid_url"
    assert records[5]["outcome"] == "ok"
    assert records[5]["links"] == [f"{server}/index.html"]


async def test_dns_rebinding_to_loopback_is_refused(server: str) -> None:
    # The address check is told "localhost" is public (a lying DNS server), but the real
    # connection goes to 127.0.0.1. The check of the connected peer must still refuse it.
    async def lying_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    url = server.replace("127.0.0.1", "localhost") + "/index.html"
    stream = io.StringIO()
    settings = Settings(retries=0, progress_interval=0)
    await extract([Job(0, url)], settings, stream, resolve=lying_resolver)
    record = json.loads(stream.getvalue())
    assert record["outcome"] == "fetch_error"
    assert record["error"]["kind"] == "blocked_address"
    assert "127.0.0.1" in record["error"]["message"]


def test_loopback_is_refused_without_allow_private(server: str, tmp_path: Path) -> None:
    out = tmp_path / "results.jsonl"
    assert main([f"{server}/index.html", "--out", str(out)]) == 0
    record = json.loads(out.read_text("utf-8"))
    assert record["outcome"] == "fetch_error"
    assert record["error"]["kind"] == "blocked_address"
