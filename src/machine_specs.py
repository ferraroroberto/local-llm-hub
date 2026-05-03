"""Load `config/machine_specs.yaml` for hardware-aware reasoning.

The file is gitignored and optional. When present it describes the host's
CPU/RAM/GPU/storage so the UI can decide whether a candidate model is
realistic to run locally. When absent or malformed, callers get `None`
back and should fall back to a "specs unknown" code path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPECS_PATH = PROJECT_ROOT / "config" / "machine_specs.yaml"


@dataclass(frozen=True)
class GPUSpec:
    name: str
    vram_gb: float
    cuda: bool = False
    primary: bool = False
    compute_capability: Optional[str] = None
    driver_version: Optional[str] = None


@dataclass(frozen=True)
class MachineSpecs:
    name: str
    cpu_model: str
    cpu_cores: int
    cpu_threads: int
    ram_gb: float
    gpus: List[GPUSpec] = field(default_factory=list)
    os_name: Optional[str] = None
    notes: Optional[str] = None

    @property
    def primary_gpu(self) -> Optional[GPUSpec]:
        for g in self.gpus:
            if g.primary:
                return g
        return self.gpus[0] if self.gpus else None

    @property
    def total_vram_gb(self) -> float:
        return float(sum(g.vram_gb for g in self.gpus))


def load() -> Optional[MachineSpecs]:
    """Return parsed machine specs, or None if the file is missing/invalid."""
    if not SPECS_PATH.exists():
        return None
    try:
        raw = yaml.safe_load(SPECS_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("machine_specs.yaml parse error: %s", exc)
        return None

    machine = raw.get("machine") or {}
    cpu = raw.get("cpu") or {}
    mem = raw.get("memory") or {}
    os_block = raw.get("os") or {}

    gpus: List[GPUSpec] = []
    for g in raw.get("gpus") or []:
        try:
            gpus.append(GPUSpec(
                name=str(g.get("name") or "unknown"),
                vram_gb=float(g.get("vram_gb") or 0.0),
                cuda=bool(g.get("cuda", False)),
                primary=bool(g.get("primary", False)),
                compute_capability=_opt_str(g.get("compute_capability")),
                driver_version=_opt_str(g.get("driver_version")),
            ))
        except (TypeError, ValueError) as exc:
            logger.warning("skipping malformed gpu row %r: %s", g, exc)

    try:
        return MachineSpecs(
            name=str(machine.get("name") or "this-machine"),
            cpu_model=str(cpu.get("model") or "unknown"),
            cpu_cores=int(cpu.get("cores") or 0),
            cpu_threads=int(cpu.get("threads") or 0),
            ram_gb=float(mem.get("total_gb") or 0.0),
            gpus=gpus,
            os_name=_opt_str(os_block.get("name")),
            notes=_opt_str(raw.get("notes")),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("machine_specs.yaml missing required fields: %s", exc)
        return None


def _opt_str(value: object) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
