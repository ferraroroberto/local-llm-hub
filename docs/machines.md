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
| `tower` | The hub runs here; Windows workstation | Windows 11 | `192.168.0.13` | — (active host, never remote-powered) | Ryzen 7 7800X3D · RTX 5060 Ti 16 GB · 128 GB RAM · hostname `tower` |
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

## Boot mode — Server (headless) default, Desktop (GUI) opt-in

`openclaw` and `gaming` both dual-boot between two systemd targets — done and
verified live via real reboots, 2026-07-21 (`life-os` `geek-out` session; the
Mac Mini and hub box aren't in scope, this only applies to the two Linux
boxes):

- **Server (default)** — `systemctl set-default multi-user.target`. No
  GNOME/gdm3, no GUI compositor. This is what actually boots on an
  unattended remote reboot with no keyboard/monitor attached. Networking,
  SSH, Tailscale, and the local-llm-hub role are unaffected — none of those
  are gated by `graphical.target`.
- **Desktop (opt-in)** — a custom GRUB entry (`/etc/grub.d/12_desktop_mode`,
  "Ubuntu (Desktop mode)") reuses the same kernel/initrd (`/boot/vmlinuz`,
  `/boot/initrd.img` — stable symlinks that survive kernel upgrades) with
  `systemd.unit=graphical.target` appended, overriding the target for that
  one boot only. The GRUB menu is visible for ~5s
  (`GRUB_TIMEOUT_STYLE=menu`, `GRUB_TIMEOUT=5`) if a screen/keyboard is
  attached; otherwise it just times out to Server.
- **Remote switch, no physical access needed**:
  `sudo grub-reboot "Ubuntu (Desktop mode)" && sudo reboot` boots once into
  Desktop, then auto-reverts to Server on the next boot after.
- Also trimmed on both: `bluetooth`, `avahi-daemon`, `ModemManager`, `cups`
  (+ its snap variant on `openclaw`) disabled — unused by
  SSH/Tailscale/local-llm-hub. `tlp` installed + enabled on both for
  ongoing USB/PCIe/disk power tuning.
- **Known limitation** — `openclaw`'s NVIDIA MX250 does not support Runtime
  D3 (`Runtime D3 status: Not supported`, even after setting
  `NVreg_DynamicPowerManagement=0x02`); its idle GPU draw is a hardware
  ceiling, not fixable via driver config.
- **`gaming`'s WiFi is intermittently flaky** — SSH/Tailscale both dropped
  and recovered (10-20s) several times during this session's reboot
  testing, independent of the boot-mode changes. Matches the open,
  unresolved [#330](https://github.com/ferraroroberto/local-llm-hub/issues/330)
  packet-loss issue — worth prioritizing now that this is a headless-only
  box for remote access.

## Tailscale identities

| Host id | Tailscale magic-DNS | Tailscale IP | Notes |
| --- | --- | --- | --- |
| `mac-mini-m4` | *(not recorded)* | `100.82.9.41` | Confirmed 2026-07-18 |
| `openclaw` | `asus-linux.tail1121fd.ts.net` | `100.102.186.128` | Confirmed 2026-07-21 — the tailnet node is `asus-linux` (renamed from the earlier `laptop`). |
| `gaming` | `gaming-linux.tail1121fd.ts.net` | `100.77.216.127` | Tailscale installed; its own tailnet node (#332). Confirmed 2026-07-21. |
| `tower` | `tower.tail1121fd.ts.net` | *(not recorded)* | The hub box; serves Langfuse at `tower.tail1121fd.ts.net:3000`. Confirmed 2026-07-21. |

**Resolved — the `tower.tail1121fd.ts.net` name and Langfuse host.** The hub box
(fleet id `tower`, formerly `pc-cuda` — renamed in #335) owns
`tower.tail1121fd.ts.net` and is where Langfuse runs
(`docs/telemetry-langfuse.md`'s `LANGFUSE_PUBLIC_URL` →
`tower.tail1121fd.ts.net:3000`). The `gaming` satellite is a separate tailnet
node `gaming-linux.tail1121fd.ts.net` and does **not** own this name — earlier
records that reserved `tower.tail1121fd.ts.net` for `gaming` were mistaken.

**Resolved — `openclaw` is the tailnet node `asus-linux`.** The `asus-linux`
node at `100.102.186.128` is `openclaw` (renamed from the earlier `laptop`
magic-DNS name). The tailnet also lists a separate `asus-windows` node (offline,
last seen 11d as of 2026-07-21); it is not mapped to any fleet host in
`config/models.yaml`, and its identity is left unstated here rather than guessed.

## Machine specs snapshot

`config/machine_specs.yaml` is a separate, **auto-generated** hardware snapshot
of whichever box runs `scripts/detect_machine_specs.py`; it is keyed by that
box's OS **hostname**, not by fleet host id. Its `name: tower` is the snapshot of
the hub box (whose hostname is `tower`). Note both the hub box and the `gaming`
satellite happen to report OS hostname `tower`, so `name:` alone doesn't
disambiguate them — the fleet host id (`tower` vs `gaming`) does.
