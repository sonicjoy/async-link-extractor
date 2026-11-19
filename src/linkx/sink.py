"""The single place where records are written and counted.

Every method is synchronous. Both stages call `emit` from the event loop, so
two writes can never interleave and no lock is needed.
"""

import math
from collections import Counter
from collections.abc import Sequence
from typing import TextIO

from linkx.models import Job, Outcome, Record, Summary


def percentile(values: Sequence[int], p: float) -> int | None:
    """Nearest-rank percentile: the smallest value with at least p% of values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


class Sink:
    def __init__(self, stream: TextIO, jobs: Sequence[Job]) -> None:
        self._stream = stream
        self._jobs = {job.index: job for job in jobs}
        self._written: set[int] = set()
        self._counts: Counter[Outcome] = Counter()
        self._latencies: list[int] = []
        self._retries = 0
        self._failed = False

    @property
    def emitted(self) -> int:
        return len(self._written)

    def count(self, outcome: Outcome) -> int:
        return self._counts[outcome]

    def emit(self, record: Record) -> None:
        """Write one record. A second record for the same job is a bug and raises.

        After a failed write every later call raises too: other tasks may still run in
        the loop iteration before the TaskGroups cancel them, and they must not write
        past a gap in the output.
        """
        if self._failed:
            raise RuntimeError("output failed earlier; no further records are written")
        if record.index not in self._jobs:
            raise RuntimeError(f"record for unknown job {record.index}")
        if record.index in self._written:
            raise RuntimeError(f"job {record.index} already has a record")
        self._written.add(record.index)
        self._counts[record.outcome] += 1
        if record.elapsed_ms is not None:
            self._latencies.append(record.elapsed_ms)
        if record.attempts:
            self._retries += record.attempts - 1
        try:
            self._stream.write(record.to_json() + "\n")
            self._stream.flush()
        except BaseException:
            self._failed = True
            raise

    def close(self, *, interrupted: bool) -> Summary:
        """Close the audit: every job must have exactly one record.

        After an interrupt, jobs without a record get a `cancelled` one. On an
        uninterrupted run a missing record means a result was lost: that is a bug.
        """
        missing = [job for index, job in self._jobs.items() if index not in self._written]
        if missing and not interrupted:
            noun = "job has" if len(missing) == 1 else "jobs have"
            raise RuntimeError(f"{len(missing)} {noun} no record")
        for job in missing:
            self.emit(Record.cancelled(job))
        return Summary(
            jobs=len(self._jobs),
            counts={outcome: self._counts[outcome] for outcome in Outcome},
            retries=self._retries,
            p50_ms=percentile(self._latencies, 50),
            p95_ms=percentile(self._latencies, 95),
            max_ms=max(self._latencies, default=None),
            interrupted=interrupted,
        )
