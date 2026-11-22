"""Markdown rendering of a run's storage section for the health report.

Every unknown field is printed as ``unknown`` with its reason, and a lower
bound is labelled as one, so the report never shows a cut-short walk or an
unreachable Docker engine as a size or as "nothing there"."""

from __future__ import annotations

from typing import Any, Dict, List

_GIB = 1024 ** 3


def _gib(value: Any) -> str:
    return f"{value / _GIB:.1f} GiB" if isinstance(value, (int, float)) else "—"


def _size(entry: Dict[str, Any]) -> str:
    """A size cell: the measured value, a labelled lower bound, or the reason."""
    status = entry.get("status")
    if status == "ok":
        return _gib(entry.get("bytes"))
    if status == "absent":
        return "absent"
    lower = entry.get("lower_bound_bytes")
    bound = f" (at least {_gib(lower)})" if lower else ""
    return f"unknown — {entry.get('reason', 'not established')}{bound}"


def _drives(section: Dict[str, Any]) -> List[str]:
    if section.get("status") != "ok":
        return [f"- Drives: {_size(section)}"]
    lines = ["| Drive | Total | Free |", "| --- | --- | --- |"]
    for d in section.get("items") or []:
        if d.get("status") == "ok":
            lines.append(f"| {d['mount']} | {_gib(d['total_bytes'])} | {_gib(d['free_bytes'])} |")
        else:
            lines.append(f"| {d['mount']} | {_size(d)} | |")
    return lines


def _growers(section: Dict[str, Any]) -> List[str]:
    if section.get("status") != "ok":
        return [f"- Known growers: {_size(section)}"]
    lines = ["| Grower | Size |", "| --- | --- |"]
    lines += [f"| {g['label']} | {_size(g)} |" for g in section.get("items") or []]
    return lines


def _top_folders(section: Dict[str, Any]) -> List[str]:
    if section.get("status") != "ok":
        return [f"- Largest folders: {_size(section)}"]
    lines: List[str] = []
    for root in section.get("roots") or []:
        lines += [f"**Largest folders under** `{root['root']}`", ""]
        if root.get("status") != "ok":
            lines += [f"_{_size(root)}_", ""]
            continue
        lines += ["| Folder | Size |", "| --- | --- |"]
        lines += [f"| `{f['path']}` | {_size(f)} |" for f in root.get("folders") or []]
        if root.get("unmeasured") or root.get("children_truncated"):
            lines += ["", f"_{root.get('unmeasured', 0)} folder(s) not fully measured"
                          + ("; the folder list itself was capped" if root.get("children_truncated") else "")
                          + "._"]
        lines.append("")
    return lines


def _docker(section: Dict[str, Any]) -> List[str]:
    if section.get("status") != "ok":
        return [f"- Docker: {_size(section)}"]
    lines = [f"- Docker engine: reachable (server {section.get('server_version') or '?'})"]
    containers = section.get("containers") or {}
    if containers.get("status") == "ok":
        items = containers.get("items") or []
        lines += ["", "| Container | State | Health |", "| --- | --- | --- |"]
        lines += [f"| {c.get('name')} | {c.get('state')} | {c.get('health') or '—'} |" for c in items]
        if not items:
            lines.append("| _none_ | | |")
    else:
        lines.append(f"- Containers: {_size(containers)}")
    usage = section.get("usage") or {}
    if usage.get("status") == "ok":
        lines += ["", "| Docker usage | Count | Active | Size | Reclaimable |",
                  "| --- | --- | --- | --- | --- |"]
        lines += [f"| {u.get('type')} | {u.get('total_count')} | {u.get('active')} |"
                  f" {u.get('size')} | {u.get('reclaimable')} |" for u in usage.get("items") or []]
    else:
        lines.append(f"- Docker disk usage: {_size(usage)}")
    return lines


def markdown_lines(storage: Dict[str, Any]) -> List[str]:
    """The ``## Storage`` section, or a "none recorded" note for a run (an
    ingested foreign run, or one stopped early) with no stored section."""
    lines = ["## Storage", ""]
    if not storage:
        return lines + ["_No storage section was recorded for this run._", ""]
    if storage.get("status") == "unknown":
        return lines + [f"_Storage collection failed — {_size(storage)}._", ""]
    for part in (_drives(storage.get("drives") or {}), _growers(storage.get("growers") or {}),
                 _docker(storage.get("docker") or {}), _top_folders(storage.get("top_folders") or {})):
        lines += part + [""]
    return lines
