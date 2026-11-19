"""Command-line contract: help, usage errors and numeric bounds."""

import pytest

from linkx.cli import main


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--queue-policy" in out
    assert "drop-oldest" in out


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
