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

| Host id | Role | OS | LAN IP | SSH user | Wired-NIC MAC | Hardware |
| --- | --- | --- | --- | --- | --- | --- |
| `tower` | The hub runs here; Windows workstation | Windows 11 | `192.168.0.13` | — (active host, never remote-powered) | `34:5a:60:d3:59:53` (Realtek 2.5GbE) | Ryzen 7 7800X3D · RTX 5060 Ti 16 GB · 128 GB RAM · hostname `tower` |
| `mac-mini-m4` | Apple-silicon hub peer (owns `qwen3.5-9b`, `parakeet`) | macOS | `192.168.0.14` | `roberto` | `1c:f6:4c:56:05:da` | Apple M4 |
| `openclaw` | Ubuntu laptop · future inference node | Ubuntu | `192.168.0.239` | `openclaw` | *(no wired NIC — Wi‑Fi only)* | GeForce MX250 |
| `gaming` | Ryzen inference satellite · STT/TTS offload (#323) | Ubuntu 24.04 (HWE, kernel 7.0) | `192.168.0.16` (static) | `gaming` | `d4:5d:64:d6:7e:a0` (`enp4s0`) | Ryzen 9 5900X · GeForce GTX 1070 8 GB (`nvidia-driver-535`, installed 2026-07-21) · 16 GB RAM (single stick) |

All non-host machines carry a `sudoers.d/99-<user>-nopasswd` drop-in
(passwordless sudo), so reboot/shutdown and read-only stat probes run over the
hub user's own SSH with no key deploy.

`gaming`'s NVIDIA proprietary driver (`nvidia-driver-535`) was installed
2026-07-21 (Secure Boot off, DKMS built against the running HWE kernel), so its
Machines-tab card now renders a live GPU gauge alongside CPU/RAM/disk. Until
then the box ran only `nouveau` with no `nvidia-smi`, which is why the GPU gauge
was absent. The STT/TTS model offload itself is still deferred (#323,
replica-first) — the driver is prerequisite groundwork, not the migration.

## Wake-on-LAN

Issue #356 added an optional `mac:` field to each host row in `config/models.yaml` — the box's **wired-NIC MAC address**, since Wake-on-LAN over WiFi is not supported by any of these machines' network adapters. Accepted formats are `aa:bb:cc:dd:ee:ff` or `AA-BB-CC-DD-EE-FF` (case-insensitive, `:` or `-` separators, validated by `src/wake_on_lan.py`). A host with no `mac:` simply has no Wake action on its Machines-console card — that's the case for `openclaw`, which has no wired NIC at all.

Mechanically, waking a host sends a standard 102-byte magic packet (6 bytes of `0xFF` followed by the target MAC repeated 16 times) as a UDP broadcast to `255.255.255.255:9`, built and sent by `src/wake_on_lan.py`. The admin SPA's Machines tab surfaces a Wake button on any down/dormant card whose host carries a `mac:`; clicking it calls `POST /admin/api/machines/{id}/wake`. This is **fire-and-forget by design** — Wake-on-LAN has no acknowledgement path, so the hub can only report that the packet was handed to the OS for broadcast, never that the target actually woke. A "wake failed" case only exists for a malformed MAC or a socket-level send error; there is no way to detect "packet sent but machine didn't wake" from the hub side.

**The hub cannot wake its own host.** `tower` runs the hub itself, so there is no remote path for a magic packet to reach it after a power outage — recovery there depends on the BIOS's AC-restore behavior (power state resumes automatically when mains power returns), not on WOL.

### Per-machine WOL status (as of 2026-07-22, from #357 remote-prep recon)

- **`tower`** (Windows 11, hub host) — wired MAC `34:5a:60:d3:59:53` (Realtek 2.5GbE, link up). Tailscale service is Running/Automatic, but reboot-with-no-login verification is still pending. BIOS WOL + AC-restore are **not yet verified** — manual checklist tracked in #357.
- **`mac-mini-m4`** (macOS, Apple silicon) — ethernet MAC `1c:f6:4c:56:05:da`; `pmset womp=1` and `autorestart=1` both set 2026-07-22. **Ethernet is currently unplugged** (box is on Wi‑Fi), so WOL is armed but inert until it's wired back in. Apple-silicon caveat: WOL only wakes from sleep, never from a full power-off — full-off recovery instead relies on "start up after power failure" (already enabled via `autorestart`).
- **`openclaw`** (Ubuntu laptop) — **no wired NIC exists** (Wi‑Fi only), so Wake-on-LAN isn't possible without a USB-ethernet adapter; no `mac:` is set in the registry. `tailscaled` is enabled.
- **`gaming`** (Ubuntu 24.04) — wired NIC `enp4s0`, MAC `d4:5d:64:d6:7e:a0`, currently `NO-CARRIER` (box is on a Wi‑Fi USB adapter, see #340). WOL is persistently enabled on the interface (`nmcli 802-3-ethernet.wake-on-lan magic` + `ethtool wol g`, set 2026-07-22) but needs a physical cable plugged in before a wake packet can do anything. `tailscaled` is enabled. BIOS WOL / AC-restore are unverified.

**Pending manual items** — tracked in #357: BIOS-level WOL + AC-restore verification per box, plugging the wired ethernet cable into `mac-mini-m4` and `gaming`, and completing the reboot-with-no-login Tailscale proof on `tower`.

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
