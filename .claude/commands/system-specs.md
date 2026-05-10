---
description: Collect Windows system specs (CPU, GPU, RAM, storage, OS) and write them to docs/system-specs/system-specs.md
---

Collect this machine's hardware/OS specs and write a markdown report to `docs/system-specs/system-specs.md` (overwrite if it exists).

## Collection

Run these PowerShell queries (parallel where independent):

1. **System / OS / CPU / GPU / BIOS / disks** via CIM:
   - `Win32_ComputerSystem` → manufacturer, model, total RAM, processor count
   - `Win32_OperatingSystem` → caption, version, build, architecture, install date, last boot
   - `Win32_Processor` → name, cores, threads, max clock, L2/L3 cache
   - `Win32_VideoController` → all GPUs (name, driver, VRAM)
   - `Win32_BIOS` → manufacturer, version, release date
   - `Win32_Baseboard` → motherboard manufacturer/product/version
   - `Win32_DiskDrive` → physical disks
   - `Win32_LogicalDisk -Filter "DriveType=3"` → logical volumes with free space
2. **RAM modules** via `Win32_PhysicalMemory` → part number, capacity, speed, slot.
3. **Accurate NVIDIA VRAM** via `nvidia-smi --query-gpu=name,memory.total,driver_version,vbios_version --format=csv` (the CIM `AdapterRAM` field is capped at 4 GB by a UInt32 limit — always cross-check with `nvidia-smi` when an NVIDIA GPU is present).

If `nvidia-smi` is missing, note it and fall back to the CIM value with a caveat.

## Output format

Match `docs/system-specs/system-specs.example.md` exactly: same section order, same headings, same table columns. Only the values change.

Header line: `_Captured: YYYY-MM-DD_` using today's date.

## Where to write

`docs/system-specs/system-specs.md` — overwrite each run. This file is gitignored; only the example is committed.

After writing, report the file path and a one-line summary of what was captured. Do not echo the full file contents back into chat.
