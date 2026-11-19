"""Command-line contract: inputs, duplicates, output, logging and exit codes."""

import asyncio
import io
import json
import logging
import runpy
import signal
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import linkx.cli
from linkx.cli import (
    _root_cause,
    build_parser,
    dedupe_key,
    format_summary,
    main,
    make_jobs,
    read_urls,
    settings_from,
)
from linkx.models import Outcome, Settings, Summary
from linkx.sink import OutputFailed


@pytest.fixture(autouse=True)
def _reset_linkx_logging() -> Iterator[None]:
    yield
    logger = logging.getLogger("linkx")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


def page(body: str = '<a href="/next">next</a>') -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=body)


def site(request: httpx.Request) -> httpx.Response:
    if request.url.host == "missing.test":
        return httpx.Response(404)
    return page()


def transport() -> httpx.MockTransport:
    return httpx.MockTransport(site)


def run(args: list[str]) -> int:
    return main(["--allow-private", *args], transport=transport())


def stdout_records(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


# --- argument parsing --------------------------------------------------------------------


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--queue-policy" in out
    assert "drop-oldest" in out


def test_module_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["linkx", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("linkx", run_name="__main__")
    assert exc.value.code == 0


def test_no_inputs_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--concurrency", "0"],
        ["--consumers", "0"],
        ["--queue-max", "0"],
        ["--max-bytes", "0"],
        ["--timeout", "0"],
        ["--retries", "-1"],
        ["--per-host-delay", "-0.5"],
        ["--queue-policy", "lifo"],
        ["--concurrency", "ten"],
    ],
)
def test_out_of_range_options_are_usage_errors(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["http://example.com/", *args])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--timeout", "nan"],
        ["--timeout", "inf"],
        ["--per-host-delay", "inf"],
        ["--per-host-delay", "nan"],
    ],
)
def test_non_finite_numbers_are_usage_errors(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["http://example.com/", *args])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("option", "largest"),
    [
        ("--concurrency", "1000"),
        ("--consumers", "64"),
        ("--queue-max", "1000000"),
        ("--max-bytes", "1000000000"),
        ("--retries", "10"),
        ("--timeout", "3600"),
        ("--per-host-delay", "3600"),
    ],
)
def test_options_have_an_upper_bound(option: str, largest: str) -> None:
    # Parse only: if a bound ever regresses, main() would start a real run and hang.
    build_parser().parse_args(["http://x.test/", option, largest])  # the bound itself is fine
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["http://x.test/", option, str(int(float(largest)) + 1)])
    assert exc.value.code == 2


def test_a_huge_integer_option_is_a_usage_error_not_a_crash() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["http://x.test/", "--retries", "9" * 400])
    assert exc.value.code == 2


@pytest.mark.parametrize("flag", ["--out", "input"])
def test_a_path_with_a_null_character_is_a_usage_error(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["http://a.test/", "--out", "bad\0name"] if flag == "--out" else ["bad\0name"]
    assert run(args) == 2
    assert "cannot" in capsys.readouterr().err


def test_option_defaults_are_the_settings_defaults() -> None:
    assert settings_from(build_parser().parse_args(["http://x.test/"])) == Settings()


def test_every_option_reaches_the_settings() -> None:
    args = build_parser().parse_args(
        [
            "http://x.test/",
            *("--concurrency", "3", "--consumers", "2", "--queue-max", "7"),
            *("--queue-policy", "block", "--timeout", "1.5", "--retries", "4"),
            *("--max-bytes", "99", "--per-host-delay", "0.25", "--allow-private"),
        ]
    )
    assert settings_from(args) == Settings(
        concurrency=3,
        consumers=2,
        queue_max=7,
        queue_policy="block",
        timeout=1.5,
        retries=4,
        max_bytes=99,
        per_host_delay=0.25,
        allow_private=True,
    )


# --- inputs ------------------------------------------------------------------------------


def test_urls_come_from_arguments_and_files(tmp_path: Path) -> None:
    listing = tmp_path / "urls.txt"
    listing.write_text(
        "﻿# a comment\nhttp://one.test/\n\n   https://two.test/a  \n#http://skipped.test/\n",
        encoding="utf-8",
    )
    assert read_urls(["HTTP://first.test/", str(listing), "https://last.test/"]) == [
        "HTTP://first.test/",
        "http://one.test/",
        "https://two.test/a",
        "https://last.test/",
    ]


def test_file_lines_are_kept_even_if_not_urls(tmp_path: Path) -> None:
    listing = tmp_path / "urls.txt"
    listing.write_text("example.com\nftp://files.test/\n", encoding="utf-8")
    assert read_urls([str(listing)]) == ["example.com", "ftp://files.test/"]


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("utf-16 with BOM", "http://a.test/\n".encode("utf-16")),
        ("latin-1", "http://a.test/caf\xe9\n".encode("latin-1")),
    ],
)
def test_input_file_that_is_not_utf8_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str, content: bytes
) -> None:
    listing = tmp_path / "urls.txt"
    listing.write_bytes(content)
    assert run([str(listing)]) == 2
    assert "not UTF-8" in capsys.readouterr().err


def test_keep_duplicates_flag_fetches_each_occurrence(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["http://a.test", "http://a.test/", "--keep-duplicates"]) == 0
    assert len(stdout_records(capsys)) == 2


def test_unreadable_input_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run([str(tmp_path / "missing.txt")]) == 2
    assert "cannot read input" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("http://a.com", "http://a.com/"),
        ("HTTP://A.com:80/#top", "http://a.com/"),
        ("https://a.com:443/x", "https://a.com/x"),
        ("http://a.com/x#one", "http://a.com/x#two"),
        ("http://[::1]:80/", "http://[::1]/"),
    ],
)
def test_equal_urls_share_a_key(first: str, second: str) -> None:
    assert dedupe_key(first) == dedupe_key(second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("http://a.com/x/", "http://a.com/x"),
        ("http://a.com/?b=1&a=2", "http://a.com/?a=2&b=1"),
        ("http://a.com:8080/", "http://a.com/"),
        ("http://a.com/", "https://a.com/"),
        ("http://a.com/Path", "http://a.com/path"),
    ],
)
def test_different_urls_have_different_keys(first: str, second: str) -> None:
    assert dedupe_key(first) != dedupe_key(second)


def test_unparseable_url_is_its_own_key() -> None:
    assert dedupe_key("http://[broken") == "http://[broken"


def test_duplicates_are_skipped_and_counted() -> None:
    jobs, skipped = make_jobs(
        ["http://a.com", "http://b.com/", "http://a.com/", "HTTP://B.COM"], keep_duplicates=False
    )
    assert [(job.index, job.url) for job in jobs] == [(0, "http://a.com"), (1, "http://b.com/")]
    assert skipped == 2


def test_keep_duplicates_keeps_every_line() -> None:
    jobs, skipped = make_jobs(["http://a.com", "http://a.com/"], keep_duplicates=True)
    assert [job.url for job in jobs] == ["http://a.com", "http://a.com/"]
    assert skipped == 0


# --- output ------------------------------------------------------------------------------


def test_records_go_to_stdout_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["http://a.test/", "http://missing.test/", "http://a.test"]) == 0
    records = stdout_records(capsys)
    assert {(r["index"], r["url"], r["outcome"]) for r in records} == {
        (0, "http://a.test/", "ok"),
        (1, "http://missing.test/", "fetch_error"),
    }


def test_records_go_to_the_out_file_which_is_truncated(tmp_path: Path) -> None:
    out = tmp_path / "results.jsonl"
    out.write_text("old content\n" * 10, encoding="utf-8")
    assert run(["http://a.test/", "--out", str(out)]) == 0
    lines = out.read_bytes().split(b"\n")
    assert lines[-1] == b""
    assert len(lines) == 2
    assert json.loads(lines[0])["links"] == ["http://a.test/next"]


def test_unopenable_out_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["http://a.test/", "--out", str(tmp_path / "no" / "such" / "dir.jsonl")]) == 2
    assert "cannot open output" in capsys.readouterr().err


def test_output_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class BrokenStdout(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError("disk full")

    monkeypatch.setattr(sys, "stdout", BrokenStdout())
    assert run(["http://a.test/"]) == 1
    assert "disk full" in capsys.readouterr().err


# --- logging and summary -----------------------------------------------------------------


def test_summary_is_logged_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["http://a.test/", "http://a.test", "http://missing.test/"]) == 0
    err = capsys.readouterr().err
    assert "2 jobs (duplicates skipped: 1)" in err
    assert "ok 1" in err
    assert "fetch_error 1" in err
    assert "p95" in err


def test_json_logs_are_one_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["http://a.test/", "--log-json"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert events[0]["event"] == "start"
    summary = events[-1]
    assert summary["event"] == "summary"
    assert summary["level"] == "INFO"
    assert summary["summary"]["counts"]["ok"] == 1
    assert summary["summary"]["jobs"] == 1


def test_verbose_logs_each_attempt_and_record(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["http://a.test/", "--verbose", "--log-json"]) == 0
    events = [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]
    assert "attempt" in events
    assert "record" in events


def test_failure_traceback_in_json_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class BrokenStdout(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError("disk full")

    monkeypatch.setattr(sys, "stdout", BrokenStdout())
    assert run(["http://a.test/", "--log-json"]) == 1
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert events[-1]["level"] == "ERROR"
    assert "OSError" in events[-1]["exception"]


# --- interrupts --------------------------------------------------------------------------


def test_ctrl_c_during_the_run_exits_130_with_every_job_recorded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def hang_on_slow(request: httpx.Request) -> httpx.Response:
        if request.url.host == "slow.test":
            signal.raise_signal(signal.SIGINT)  # the operator presses Ctrl-C mid-run
            await asyncio.Event().wait()
        return page()

    code = main(
        ["--allow-private", "--concurrency", "1", "http://a.test/", "http://slow.test/"],
        transport=httpx.MockTransport(hang_on_slow),
    )
    assert code == 130
    captured = capsys.readouterr()
    records = {r["url"]: r["outcome"] for r in map(json.loads, captured.out.splitlines())}
    assert records == {"http://a.test/": "ok", "http://slow.test/": "cancelled"}
    assert "summary: 2 jobs" in captured.err
    assert "(interrupted)" in captured.err


def test_ctrl_c_outside_the_pipeline_handler_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(linkx.cli, "extract", interrupted)
    assert run(["http://a.test/"]) == 130


def test_broken_pipe_on_stdout_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    class ClosedPipe(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError("reader went away")

    monkeypatch.setattr(sys, "stdout", ClosedPipe())
    assert run(["http://a.test/"]) == 1


def test_the_first_output_error_is_the_one_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Many tasks emit at once; after the first write fails, the others are refused.
    # The operator must see the real cause, not "output failed earlier".
    class ClosedPipe(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError("reader went away")

    monkeypatch.setattr(sys, "stdout", ClosedPipe())
    urls = [f"http://h{i}.test/" for i in range(40)]
    assert run([*urls, "--concurrency", "8", "--consumers", "4", "--queue-max", "1"]) == 1
    err = capsys.readouterr().err
    assert "reader went away" in err
    assert "output failed earlier" not in err


def test_root_cause_prefers_the_first_real_failure() -> None:
    original = BrokenPipeError("reader went away")
    refused = OutputFailed("output failed earlier")
    refused.__cause__ = original
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [refused]), original])
    assert _root_cause(nested) is original
    assert _root_cause(ExceptionGroup("only refusals", [refused])) is original


def test_summary_without_fetches_says_so() -> None:
    summary = Summary(
        jobs=1,
        counts=dict.fromkeys(Outcome, 0) | {Outcome.CANCELLED: 1},
        retries=0,
        p50_ms=None,
        p95_ms=None,
        max_ms=None,
        interrupted=True,
    )
    text = format_summary(summary)
    assert "no fetches" in text
    assert "(interrupted)" in text
    assert "cancelled 1" in text


def test_ctrl_c_before_the_pipeline_starts_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(sources: list[str]) -> list[str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(linkx.cli, "read_urls", interrupted)
    assert run(["http://a.test/"]) == 130
