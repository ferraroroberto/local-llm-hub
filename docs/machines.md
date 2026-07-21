# Fleet machines

A single coherent record of the physical machines this hub knows about — their
LAN addresses, SSH logins, Tailscale identities, OS, and role. The **source of
truth for what the hub acts on** is `config/models.yaml` `hosts:` (the machine
console reads it directly); this page is the human-readable companion, and the
cross-machine SSH/access details also live in the `life-os` `geek-out`
`context/setup.md` skill. Update all three together when a machine changes.

> LAN IPs and SSH usernames are already committed in `config/models.yaml` (same
> convention across the fleet). Tailscale `100.x` addresses are private
> CGNAT-range tailnet IPs, not publicly routable. The sudo/login password is
> **never** stored anywhere in these repos — passwordless-sudo sudoers drop-ins
> make it unnecessary for automation.

## Inventory

| Host id | Role | OS | LAN IP | SSH user | Hardware |
| --- | --- | --- | --- | --- | --- |
| `pc-cuda` | The hub runs here; Windows workstation | Windows 11 | `192.168.0.13` | — (active host, never remote-powered) | Ryzen 7 7800X3D · RTX 5060 Ti 16 GB · 128 GB RAM · hostname `tower` |
| `mac-mini-m4` | Apple-silicon hub peer (owns `qwen3.5-9b`, `parakeet`) | macOS | `192.168.0.14` | `roberto` | Apple M4 |
| `openclaw` | Ubuntu laptop · future inference node | Ubuntu | `192.168.0.239` | `openclaw` | GeForce MX250 |
| `gaming` | Ryzen inference satellite · STT/TTS offload (#323) | Ubuntu 24.04 (HWE, kernel 7.0) | `192.168.0.16` (static) | `gaming` | Ryzen 9 5900X · GeForce GTX 1070 8 GB (`nvidia-driver-535`, installed 2026-07-21) · 16 GB RAM (single stick) |

All non-host machines carry a `sudoers.d/99-<user>-nopasswd` drop-in
(passwordless sudo), so reboot/shutdown and read-only stat probes run over the
hub user's own SSH with no key deploy.

`gaming`'s NVIDIA proprietary driver (`nvidia-driver-535`) was installed
2026-07-21 (Secure Boot off, DKMS built against the running HWE kernel), so its
Machines-tab card now renders a live GPU gauge alongside CPU/RAM/disk. Until
then the box ran only `nouveau` with no `nvidia-smi`, which is why the GPU gauge
was absent. The STT/TTS model offload itself is still deferred (#323,
replica-first) — the driver is prerequisite groundwork, not the migration.

## Tailscale identities

| Host id | Tailscale magic-DNS | Tailscale IP | Notes |
| --- | --- | --- | --- |
| `mac-mini-m4` | *(not recorded)* | `100.82.9.41` | Confirmed 2026-07-18 |
| `openclaw` | `laptop.tail1121fd.ts.net` | `100.102.186.128` | Confirmed 2026-07-18 |
| `gaming` | `gaming-linux.tail1121fd.ts.net` | `100.77.216.127` | Tailscale installed; its own tailnet node (#332). Confirmed 2026-07-21. |
| `pc-cuda` | *(to confirm)* | *(to confirm)* | — |

**Open item — the `tower.tail1121fd.ts.net` alias.** `gaming` is now its own
tailnet node `gaming-linux.tail1121fd.ts.net` (#332), so it does **not** own the
historical `tower.tail1121fd.ts.net` name — which is still the host in
`docs/telemetry-langfuse.md`'s `LANGFUSE_PUBLIC_URL`
(`tower.tail1121fd.ts.net:3000`). Which machine actually serves Langfuse under
that name (the hub box `pc-cuda`, or somewhere else) is **not yet confirmed
here** — resolve it and update the Langfuse docs to match, rather than guessing
now. Kept deliberately un-asserted so this record stays trustworthy.

**Also unverified — `openclaw` vs `asus-linux`.** As of 2026-07-21 the tailnet
lists an `asus-linux` node at `100.102.186.128` — the same IP this doc records
for `openclaw` (`laptop.tail1121fd.ts.net`) — plus a separate `asus-windows`
node. Whether `openclaw` was renamed on the tailnet (and how `laptop` /
`asus-linux` / `asus-windows` map to fleet host ids) is **not yet confirmed
here**; left un-asserted pending a check rather than rewritten on a guess.

## Machine specs snapshot

`config/machine_specs.yaml` is a separate, **auto-generated** hardware snapshot
of whichever box runs `scripts/detect_machine_specs.py`; it is keyed by that
box's **hostname** (currently `tower`, the hub box `pc-cuda`), not by fleet host
id — do not confuse its `name: tower` with the `gaming` satellite.
