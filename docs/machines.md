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
| `openclaw` | Ubuntu laptop · future inference node | Ubuntu | `192.168.0.11` | `openclaw` | *(no wired NIC — Wi‑Fi only)* | GeForce MX250 |
| `gaming` | Ryzen inference satellite · STT/TTS offload (#323) | Ubuntu 24.04 (HWE, kernel 7.0) | `192.168.0.16` (static) | `gaming` | `d4:5d:64:d6:7e:a0` (`enp4s0`) | Ryzen 9 5900X · GeForce GTX 1070 8 GB (`nvidia-driver-535`, installed 2026-07-21) · 16 GB RAM (single stick) |

All non-host machines carry a `sudoers.d/99-<user>-nopasswd` drop-in
(passwordless sudo), so reboot/shutdown and read-only stat probes run over the
hub user's own SSH with no key deploy. On the systemd satellite (`gaming`) the
same passwordless sudo also covers `systemctl` — the Linux hub-lifecycle
dispatcher (`linux/bin/hub-remote-ctl.sh`, #368) and the
`POST /admin/api/hub/{stop,restart}` endpoints run `sudo -n systemctl
{start,stop,restart} local-llm-hub`, and `python -m src.install --fix` writes
the unit via `sudo -n tee`. All use `sudo -n`, so a missing/incorrect drop-in
fails fast with "a password is required" instead of hanging. The bootstrap/sync
verbs themselves ride the dedicated **forced-command** SSH key (the
`authorized_keys` line is documented in the README's "Linux satellite
lifecycle" section), separate from this general-SSH sudo channel.

### GPU-VRAM capacity ceilings (issue #375)

The GPU-VRAM facts in the Hardware column above are now also declared as
**structured** data the placement system reads — `config/models.yaml` `hosts:`
carries an optional `vram_mb` per host, and each GPU model row carries a rough
`est_vram_mb` footprint. The fleet placement grid sums a host's placed models'
`est_vram_mb` and shows an **advisory** warning (never a hard block) when the
total exceeds that host's `vram_mb` ceiling — replacing the ad-hoc `nvidia-smi`
glance that used to gate a placement change by hand.

| Host id | `vram_mb` ceiling | Source |
| --- | --- | --- |
| `tower` | `16384` | RTX 5060 Ti 16 GB |
| `gaming` | `8192` | GTX 1070 8 GB — the tightest ceiling; holds whisper + orpheus + whisper_translate + whisper_vanilla (#370) — 2000 + 2800 + 0 + 2000 = 6800 MB, comfortably under |
| `mac-mini-m4` | *(none)* | Apple-silicon **unified memory** has no fixed VRAM partition to check against — the grid skips the warning rather than inventing a misleading ceiling |
| `openclaw` | *(none)* | Serves no models; not placeable, so no ceiling needed |

The `est_vram_mb` estimates are engineering approximations (weights quant + KV
cache + any CPU-offload), each documented inline in `config/models.yaml`; they
are deliberately static, not live telemetry (exact live VRAM accounting was
explicitly out of scope for #375). CPU-only backends (`whisper_translate`,
`piper`), off-GPU paths (`parakeet` on the Mac ANE), and virtual aliases are
`0`. Keep both the prose Hardware column and these fields in sync when a
machine's GPU changes.

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
- **`mac-mini-m4`** (macOS, Apple silicon) — ethernet MAC `1c:f6:4c:56:05:da`; `pmset womp=1` and `autorestart=1` both set 2026-07-22. **Wired since 2026-07-23**: `en0` holds the reserved `192.168.0.14` (DHCP reservation is on this wired MAC), so WOL is live for wake-from-sleep; `en1` Wi‑Fi stays up as an unreserved fallback on a pool address. Apple-silicon caveat: WOL only wakes from sleep, never from a full power-off — full-off recovery instead relies on "start up after power failure" (already enabled via `autorestart`).
- **`openclaw`** (Ubuntu laptop) — **no wired NIC exists** (Wi‑Fi only), so Wake-on-LAN isn't possible without a USB-ethernet adapter; no `mac:` is set in the registry. `tailscaled` is enabled.
- **`gaming`** (Ubuntu 24.04) — **wired since 2026-07-23** (#383): `enp4s0`, MAC `d4:5d:64:d6:7e:a0`, 1 Gb/s full duplex, and the router's DHCP reservation hands it the same `192.168.0.16`. WOL is persistently enabled (`nmcli 802-3-ethernet.wake-on-lan magic` + `ethtool wol g`, set 2026-07-22) and now genuinely armed — a wake packet has a live NIC to land on. The USB Wi‑Fi dongle is parked (connection kept, `autoconnect no`) as a manual fallback only; `tailscaled` is enabled and rides whichever link is up. BIOS WOL / AC-restore are still unverified.

**Pending manual items** — tracked in #357: BIOS-level WOL + AC-restore verification per box, and completing the reboot-with-no-login Tailscale proof on `tower`. (`gaming` and `mac-mini-m4` were both wired 2026-07-23.)

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
- **`gaming`'s WiFi flakiness — superseded by the wire.** The 10-20s
  SSH/Tailscale drops (#340) were fixed by an antenna reseat + 5 GHz pin
  (2026-07-22, 0% loss over 400 pings), but #383 (2026-07-23) then showed the
  dongle still **collapses under any sustained transfer** (throughput decays
  3 MB/s → ~37 KB/s → link dead; box unaffected, auto-recovers). Wired
  ethernet went live 2026-07-23 and is now the only routine link; the dongle
  stays plugged as a parked manual fallback (`autoconnect no`). If it must
  carry a bulk transfer again, use the burst+bounce pattern recorded in #383.
- **`gaming` serves all four whisper voice backends since #323/#370** —
  `whisper` (STT, transcribe-role fallback; ~9.1 RTFx) and `orpheus` (TTS,
  explicit-model; ~2× real-time) moved in #323; `whisper_translate`
  (translate-role, CPU-only) and `whisper_vanilla` (unbiased auto-detect,
  GPU/lazy) joined in #370, so **tower carries no whisper backends at all**.
  All four run under the systemd-supervised hub on `:8000`, CUDA-built for
  the GTX 1070 (`sm_61`) where applicable. The tower proxies all of them
  transparently and keeps its VRAM for the agentic lanes.
- **`gaming`'s torch must come from the cu126 wheel index** (#385): PyTorch
  dropped Pascal (`sm_61`) kernels from cu128+ builds, so a default
  `pip install torch` silently yields a CUDA-blind torch and orpheus's SNAC
  vocoder falls back to CPU. Install
  `torch==<ver>+cu126 --index-url https://download.pytorch.org/whl/cu126`
  (satisfies the `>=2.9` pin; verified 2026-07-23 — GPU SNAC took the orpheus
  hub-e2e median from 4469 ms to 3753 ms).

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
