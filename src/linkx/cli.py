"""Command-line entry point."""

import argparse
from collections.abc import Callable, Sequence

QUEUE_POLICIES = ("drop-oldest", "block")


def _bounded[T: (int, float)](
    convert: Callable[[str], T], *, minimum: T, inclusive: bool = True
) -> Callable[[str], T]:
    """Build an argparse type that converts a value and enforces a lower bound."""

    def parse(text: str) -> T:
        try:
            value = convert(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
        if value < minimum or (not inclusive and value == minimum):
            relation = "at least" if inclusive else "above"
            raise argparse.ArgumentTypeError(f"must be {relation} {minimum}, got {text}")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkx",
        description="Fetch pages concurrently and write the links on each one as JSON lines.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="URL_OR_FILE",
        help="a URL starting with http:// or https://, or a file with one URL per line",
    )
    parser.add_argument("--out", metavar="FILE", help="write records here instead of stdout")
    parser.add_argument(
        "--keep-duplicates", action="store_true", help="fetch repeated URLs once per occurrence"
    )
    parser.add_argument(
        "--concurrency", type=_bounded(int, minimum=1), default=10, help="fetch workers"
    )
    parser.add_argument(
        "--consumers", type=_bounded(int, minimum=1), default=1, help="parsing consumers"
    )
    parser.add_argument(
        "--queue-max", type=_bounded(int, minimum=1), default=100, help="queue capacity"
    )
    parser.add_argument(
        "--queue-policy",
        choices=QUEUE_POLICIES,
        default="drop-oldest",
        help="what a fetch worker does when the queue is full",
    )
    parser.add_argument(
        "--timeout",
        type=_bounded(float, minimum=0.0, inclusive=False),
        default=10.0,
        help="seconds per fetch attempt",
    )
    parser.add_argument(
        "--retries", type=_bounded(int, minimum=0), default=2, help="extra attempts per URL"
    )
    parser.add_argument(
        "--max-bytes",
        type=_bounded(int, minimum=1),
        default=2_000_000,
        help="largest body accepted",
    )
    parser.add_argument(
        "--per-host-delay",
        type=_bounded(float, minimum=0.0),
        default=0.0,
        help="minimum seconds between requests to one host",
    )
    parser.add_argument(
        "--allow-private",
        action="store_true",
        help="allow loopback, private and link-local addresses",
    )
    parser.add_argument("--verbose", action="store_true", help="log each attempt and record")
    parser.add_argument("--log-json", action="store_true", help="write logs as JSON lines")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
