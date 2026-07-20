"""Ingest a portable capture into the store as an ordinary run (issue #316).

The counterpart to ``scripts/portable_capture.py``: that script captures raw on
a hub-less machine and interprets nothing; this module replays the raw samples
into the same SQLite store the in-hub sampler writes to, applying **all** the
central interpretation — fleet attribution, coverage, the health verdict — at
ingest time. After ingest a foreign ``openclaw`` run is indistinguishable to
``report.summary``/``drift``/verdict from a locally captured one.

Two things are done *here* rather than trusting the payload, on purpose:

* **Attribution** — each process is re-attributed at ingest with the **source
  machine's** rule group (``attribution.attribute(name, cmd, platform=...)``),
  so the rules stay in one committed config file and editing
  ``config/diagnostics_apps.json`` re-attributes every future ingest with no
  change on any peer. A Linux capture is judged by the Linux path rules even
  though the hub doing the ingest is Windows (#320's per-OS groups, #316's
  cross-OS replay).
* **CPU-count normalization** — ``params.cpu_count`` is taken from the payload,
  so ``report`` normalizes per-process CPU against the **source** machine's core
  count, never the ingesting hub's.

Everything is validated before a single row is written, so a truncated or
garbled payload is refused whole rather than leaving a half-run behind.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import attribution, coverage, rules, store

logger = logging.getLogger(__name__)

SCHEMA_PREFIX = "llm-hub-diagnostics-capture/"
_VALID_PLATFORMS = {"windows", "darwin", "linux"}


class IngestError(ValueError):
    """A payload that cannot be trusted — raised before anything is written."""


# ------------------------------------------------------------- validation


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise IngestError(msg)


def _validate(payload: Any) -> None:
    """Reject a partial/garbled payload *before* any run row exists.

    Deliberately strict about the envelope (schema, platform, a non-empty
    sample list) and about each sample carrying the two list fields the replay
    iterates, but tolerant of individual metric dicts being absent — a peer
    that couldn't read swap should still ingest, recorded as a gap, not a
    hard failure."""
    _require(isinstance(payload, dict), "payload is not a JSON object")
    schema = payload.get("schema")
    _require(isinstance(schema, str) and schema.startswith(SCHEMA_PREFIX),
             f"unrecognized schema {schema!r} (expected {SCHEMA_PREFIX}*)")
    _require(payload.get("platform") in _VALID_PLATFORMS,
             f"platform must be one of {sorted(_VALID_PLATFORMS)}, got {payload.get('platform')!r}")
    samples = payload.get("samples")
    _require(isinstance(samples, list) and len(samples) > 0,
             "payload has no samples")
    for i, s in enumerate(samples):
        _require(isinstance(s, dict), f"sample {i} is not an object")
        _require(s.get("ts") is not None, f"sample {i} has no timestamp")
        _require(isinstance(s.get("processes"), list), f"sample {i} has no process list")
        _require(isinstance(s.get("ports"), list), f"sample {i} has no port list")


# ------------------------------------------------------------- replay


def _sample_of(s: Dict[str, Any]) -> store.SystemSample:
    return store.SystemSample(
        ts=float(s["ts"]),
        cpu_percent=s.get("cpu_percent"),
        per_core=list(s.get("per_core") or []),
        load_avg=s.get("load_avg"),
        ram=s.get("ram") or {},
        swap=s.get("swap") or {},
        disk=s.get("disk") or {},
        disk_io=s.get("disk_io") or {},
        net_io=s.get("net_io") or {},
        gpus=list(s.get("gpus") or []),
        process_count=int(s.get("process_count")
                          if s.get("process_count") is not None else len(s.get("processes") or [])),
    )


def ingest_payload(payload: Dict[str, Any], *, machine: str = None,
                   trigger: str = "remote") -> str:
    """Ingest one validated capture and return the new ``run_id``.

    ``machine`` overrides the machine id stamped on the run (the orchestrator
    knows the fleet id even when the peer only knew its hostname); otherwise the
    payload's ``machine`` field is used."""
    _validate(payload)

    platform = payload["platform"]
    samples: List[Dict[str, Any]] = payload["samples"]
    machine_id = machine or payload.get("machine") or payload.get("hostname") or "unknown"

    run_id = store.create_run(
        machine_id=str(machine_id),
        os_name=str(payload.get("os") or platform),
        hostname=str(payload.get("hostname") or ""),
        interval_s=float(payload.get("interval_s") or 0.0),
        duration_s=_duration_of(payload),
        trigger=trigger,
        params={"cpu_count": _cpu_count_of(payload), "source_platform": platform,
                "ingested": True},
    )

    ports_denied_all = True
    for s in samples:
        procs = _attribute_processes(s.get("processes") or [], platform)
        ports = _attribute_ports(s.get("ports") or [], procs, platform)
        if not s.get("ports_denied"):
            ports_denied_all = False
        store.write_sample(run_id, _sample_of(s), procs, ports)

    # A run every one of whose port scans was denied was blind to ports
    # throughout — the same run-level signal the live sampler derives, recorded
    # as coverage so a blind table never reads as "nothing listening" (#322).
    _finalize(run_id, platform=platform, ports_denied=ports_denied_all)
    logger.info("📥 ingested remote run %s for %s (%s, %d samples)",
                run_id, machine_id, platform, len(samples))
    return run_id


def _attribute_processes(raw: List[Dict[str, Any]], platform: str) -> List[Dict[str, Any]]:
    """Copy each raw process through central attribution, tagging ``app_id``.

    The portable script deliberately ships no ``app_id`` — attribution is a
    central, config-driven decision, applied here against the source platform's
    rule group."""
    out: List[Dict[str, Any]] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or ""
        cmdline = p.get("cmdline") or ""
        row = dict(p)
        row["app_id"] = attribution.attribute(name, cmdline, platform=platform)
        out.append(row)
    return out


def _attribute_ports(raw: List[Dict[str, Any]], attributed_procs: List[Dict[str, Any]],
                     platform: str) -> List[Dict[str, Any]]:
    """Attribute each listening port to its owning app, reusing the app_id of
    the already-attributed process that owns the pid (matching the live scan's
    ``owner.get('app_id') or attribute(name, '')`` fallback)."""
    by_pid = {p.get("pid"): p for p in attributed_procs}
    out: List[Dict[str, Any]] = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        owner = by_pid.get(q.get("pid")) or {}
        name = q.get("name") or owner.get("name") or ""
        row = dict(q)
        row["app_id"] = owner.get("app_id") or attribution.attribute(name, "", platform=platform)
        out.append(row)
    return out


def _finalize(run_id: str, *, platform: str, ports_denied: bool) -> None:
    """Close, cover, and judge — the ingest twin of ``sampler._finalize``.

    Coverage is written before the verdict so a rule that depends on a blind
    collector declines to score it (#322); GPU-unsupported is keyed off the
    *source* platform, not the ingesting hub's."""
    store.finish_run(run_id, status="complete")
    store.save_coverage(run_id, coverage.compute(run_id, ports_denied=ports_denied,
                                                  platform=platform))
    rules.evaluate_and_save(run_id)


def _duration_of(payload: Dict[str, Any]) -> float:
    try:
        return max(0.0, float(payload["ended_at"]) - float(payload["started_at"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _cpu_count_of(payload: Dict[str, Any]) -> int:
    try:
        return max(1, int(payload.get("cpu_count") or 1))
    except (TypeError, ValueError):
        return 1


# ------------------------------------------------------------- CLI


def ingest_file(path: Path, *, machine: str = None) -> str:
    """Read a capture JSON from a file (or ``-`` for stdin) and ingest it."""
    if str(path) == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(f"payload is not valid JSON: {exc}") from exc
    return ingest_payload(payload, machine=machine)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest a portable diagnostics capture into the hub store (#316)")
    parser.add_argument("file", help="capture JSON file, or - for stdin")
    parser.add_argument("--machine", default=None,
                        help="override the fleet machine id stamped on the run")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        run_id = ingest_file(Path(args.file), machine=args.machine)
    except IngestError as exc:
        sys.stderr.write(f"ingest refused: {exc}\n")
        return 2
    sys.stdout.write(run_id + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
