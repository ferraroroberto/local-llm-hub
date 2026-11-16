"""Name a drained loopback port pool in the gate output.

Every TCP connection a browser opens to a local server leaves a TIME_WAIT entry
on its (ephemeral port, server port) pair for minutes. Windows answers a
connect() that lands on such a pair with ``net::ERR_ADDRESS_IN_USE`` instead of
trying another port, so a busy host (this run, or another lane gating at the
same time) fails whichever page loads next, unrelated to the diff.

Two pytest hooks, imported by ``conftest.py``: a failure whose text carries
``ERR_ADDRESS_IN_USE`` is relabelled ``PORT POOL EXHAUSTED`` and listed in its
own terminal-summary section, so a gate reader sees a host-capacity problem
instead of a page defect.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

PORT_POOL_MARKER = "ERR_ADDRESS_IN_USE"
PORT_POOL_HEADLINE = (
    "PORT POOL EXHAUSTED — this is the host running out of loopback ephemeral "
    f"ports ({PORT_POOL_MARKER}), not a page failure. Another gate or a recent "
    "run is holding ports in TIME_WAIT; wait a few minutes and re-run rather "
    "than debugging the page."
)

_PORT_POOL_FAILURES: list[str] = []


def is_port_pool_failure(text: str) -> bool:
    return PORT_POOL_MARKER in text


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Relabel a report that failed on ``ERR_ADDRESS_IN_USE`` so its first line names the host."""
    report = yield
    if report.failed and is_port_pool_failure(report.longreprtext):
        report.longrepr = f"{PORT_POOL_HEADLINE}\n\n{report.longreprtext}"
        _PORT_POOL_FAILURES.append(report.nodeid)
    return report


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Print the port-pool section naming each affected test."""
    if not _PORT_POOL_FAILURES:
        return
    terminalreporter.write_sep("=", "PORT POOL EXHAUSTED", red=True, bold=True)
    terminalreporter.write_line(PORT_POOL_HEADLINE)
    for nodeid in dict.fromkeys(_PORT_POOL_FAILURES):
        terminalreporter.write_line(f"  {nodeid}")
