"""Python module names cited in source comments and docstrings must exist.

The doc-refs ratchet reads Markdown only, so a docstring that still points at
a module that moved (``tts_engines/process.py``, chat routes "in
``server.py``") goes stale unnoticed in the very place a reader trusts for
orientation. This reads every comment and docstring under the source roots and
fails on a backticked ``*.py`` name that no tracked file answers to.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
import tomllib
from pathlib import Path
from typing import Iterator, List, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("src", "app_web", "tray")

# A backticked (single or double) relative module path, e.g. ``routes/openai_chat.py``.
_REF = re.compile(r"`{1,2}([A-Za-z_][\w./-]*\.py)`{1,2}")


def _tracked_py_files() -> Set[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _vendored_files() -> Set[str]:
    """Byte-vendored scaffold copies cite scaffold paths and are never edited here."""
    config = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    return {entry["dest"] for entry in config.get("vendored", {}).values()}


def _comment_and_docstring_text(source: str) -> Iterator[str]:
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            yield tok.string
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                yield doc


def _resolves(ref: str, tracked: Set[str]) -> bool:
    """A ref resolves when some tracked file's path ends with it."""
    return any(path == ref or path.endswith("/" + ref) for path in tracked)


def test_cited_python_modules_exist():
    tracked = _tracked_py_files()
    vendored = _vendored_files()
    dangling: List[str] = []
    for path in sorted(tracked):
        if path.split("/", 1)[0] not in SOURCE_ROOTS or path in vendored:
            continue
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        for text in _comment_and_docstring_text(source):
            for ref in _REF.findall(text):
                if not _resolves(ref, tracked):
                    dangling.append(f"{path}: `{ref}`")
    assert not dangling, "comments cite modules that do not exist:\n" + "\n".join(sorted(set(dangling)))
