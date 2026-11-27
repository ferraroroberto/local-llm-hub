"""The e2e layout sweep re-measures a failed check, never swallows a real one."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.e2e.test_layout import _SWEEP_ATTEMPTS, _settled


def _failing(times: int) -> tuple[list, Callable[[], None]]:
    calls: list = []

    def check() -> None:
        calls.append(1)
        if len(calls) <= times:
            raise AssertionError("effective 0x0px")

    return calls, check


def test_a_check_that_settles_passes():
    calls, check = _failing(_SWEEP_ATTEMPTS - 1)
    _settled(check)
    assert len(calls) == _SWEEP_ATTEMPTS


def test_a_check_that_fails_every_attempt_raises():
    calls, check = _failing(_SWEEP_ATTEMPTS)
    with pytest.raises(AssertionError, match="0x0px"):
        _settled(check)
    assert len(calls) == _SWEEP_ATTEMPTS
