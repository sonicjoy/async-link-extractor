"""The output record is the contract: one JSON object per job, fixed keys, explicit nulls."""

import json

from linkx.models import ErrorKind, FetchError, FetchResult, Job, Outcome, Record

KEYS = [
    "index",
    "url",
    "final_url",
    "outcome",
    "http_status",
    "links",
    "error",
    "elapsed_ms",
    "attempts",
]

JOB = Job(index=3, url="http://a.test")


def fetched(**overrides: object) -> FetchResult:
    values: dict[str, object] = {
        "job": JOB,
        "final_url": "http://a.test/",
        "http_status": 200,
        "html": "<a href='/x'>x</a>",
        "error": None,
        "elapsed_ms": 42,
        "attempts": 1,
    }
    values.update(overrides)
    return FetchResult(**values)  # type: ignore[arg-type]


def as_dict(record: Record) -> dict[str, object]:
    line = record.to_json()
    assert "\n" not in line
    parsed: dict[str, object] = json.loads(line)
    assert list(parsed) == KEYS
    return parsed


def test_outcome_values_are_the_documented_strings() -> None:
    assert [o.value for o in Outcome] == [
        "ok",
        "fetch_error",
        "parse_error",
        "dropped",
        "cancelled",
    ]


def test_ok_record() -> None:
    record = Record.ok(fetched(), ["http://a.test/x"])
    assert as_dict(record) == {
        "index": 3,
        "url": "http://a.test",
        "final_url": "http://a.test/",
        "outcome": "ok",
        "http_status": 200,
        "links": ["http://a.test/x"],
        "error": None,
        "elapsed_ms": 42,
        "attempts": 1,
    }


def test_fetch_error_without_a_response_has_null_url_and_status() -> None:
    result = fetched(
        final_url=None,
        http_status=None,
        html=None,
        error=FetchError(ErrorKind.TIMEOUT, "no response within 10s"),
        attempts=3,
    )
    data = as_dict(Record.fetch_error(result))
    assert data["outcome"] == "fetch_error"
    assert data["final_url"] is None
    assert data["http_status"] is None
    assert data["links"] is None
    assert data["error"] == {"kind": "timeout", "message": "no response within 10s"}
    assert data["attempts"] == 3


def test_fetch_error_with_a_response_keeps_its_status() -> None:
    result = fetched(
        html=None, http_status=404, error=FetchError(ErrorKind.HTTP_STATUS, "HTTP 404")
    )
    data = as_dict(Record.fetch_error(result))
    assert data["http_status"] == 404
    assert data["final_url"] == "http://a.test/"
    assert data["error"] == {"kind": "http_status", "message": "HTTP 404"}


def test_parse_error_record() -> None:
    data = as_dict(Record.parse_error(fetched(), "ValueError: bad markup"))
    assert data["outcome"] == "parse_error"
    assert data["links"] is None
    assert data["error"] == {"kind": "parse", "message": "ValueError: bad markup"}
    assert data["http_status"] == 200


def test_dropped_record_keeps_the_fetch_error() -> None:
    result = fetched(
        html=None, http_status=503, error=FetchError(ErrorKind.HTTP_STATUS, "HTTP 503")
    )
    data = as_dict(Record.dropped(result))
    assert data["outcome"] == "dropped"
    assert data["links"] is None
    assert data["error"] == {"kind": "http_status", "message": "HTTP 503"}
    assert data["http_status"] == 503


def test_dropped_record_of_a_good_fetch_has_no_error() -> None:
    data = as_dict(Record.dropped(fetched()))
    assert data["outcome"] == "dropped"
    assert data["error"] is None
    assert data["links"] is None
    assert data["elapsed_ms"] == 42


def test_cancelled_record_has_only_identity() -> None:
    assert as_dict(Record.cancelled(JOB)) == {
        "index": 3,
        "url": "http://a.test",
        "final_url": None,
        "outcome": "cancelled",
        "http_status": None,
        "links": None,
        "error": None,
        "elapsed_ms": None,
        "attempts": None,
    }


def test_non_ascii_is_written_as_utf8_text() -> None:
    record = Record.ok(fetched(), ["http://a.test/café"])
    assert "café" in record.to_json()
