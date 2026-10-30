"""Lint: process-control timing is named, not a bare number.

A stop budget or a TUI keystroke pause written as a literal reads as a
measured value to the next reader, so retuning it means re-deriving it. This
pins the two places the audit found it: every `stop_popen` call takes named
timeouts, and `_switch_model` (the `agy` `/model` picker driver) paces its
keystrokes with named constants.
"""

from __future__ import annotations

import ast

from tests.ratchets._js_lint_support import REPO_ROOT

_STOP_TIMEOUT_KWARGS = {"terminate_timeout", "kill_timeout"}


def _is_number(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def test_stop_popen_timeouts_are_named():
    offenders: list[str] = []
    for root in ("src", "tray"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) != "stop_popen":
                    continue
                for kw in node.keywords:
                    if kw.arg in _STOP_TIMEOUT_KWARGS and _is_number(kw.value):
                        offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{kw.value.lineno} {kw.arg}")

    assert not offenders, (
        "stop_popen timeout given as a bare number -- use a named constant "
        "(process_supervisor.STOP_* for model backends) with its reason:\n  "
        + "\n  ".join(offenders)
    )


def test_switch_model_sleeps_are_named():
    path = REPO_ROOT / "src" / "gemini_cli.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_switch_model"
    )
    offenders = [
        f"line {n.lineno}"
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and _call_name(n) == "sleep" and n.args and _is_number(n.args[0])
    ]

    assert not offenders, (
        "_switch_model sleeps for a bare number -- name it next to "
        "_SLASH_SETTLE_S and say what the pause waits for: " + ", ".join(offenders)
    )
