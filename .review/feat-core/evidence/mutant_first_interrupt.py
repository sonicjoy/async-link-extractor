"""Mutation check: the first interrupt also cancels consumers. The drain test must fail."""
import sys
import pytest
from linkx.pipeline import Pipeline

def mutant_interrupt(self):  # type: ignore[no-untyped-def]
    self._interrupts += 1
    for task in [*self._workers, *self._consumers]:
        task.cancel()

Pipeline.interrupt = mutant_interrupt  # type: ignore[method-assign]
sys.exit(pytest.main(["-q", "-p", "no:cacheprovider",
    "tests/test_pipeline.py::test_first_interrupt_still_parses_every_queued_result"]))
