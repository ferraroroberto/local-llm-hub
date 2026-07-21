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
| `gaming` | Ryzen inference satellite · STT/TTS offload (#323) | Ubuntu | `192.168.0.16` (static) | `gaming` | Ryzen 9 5900X · 8 GB VRAM NVIDIA · 16 GB RAM (single stick) |

All non-host machines carry a `sudoers.d/99-<user>-nopasswd` drop-in
(passwordless sudo), so reboot/shutdown and read-only stat probes run over the
hub user's own SSH with no key deploy.

## Tailscale identities

| Host id | Tailscale magic-DNS | Tailscale IP | Notes |
| --- | --- | --- | --- |
| `mac-mini-m4` | *(not recorded)* | `100.82.9.41` | Confirmed 2026-07-18 |
| `openclaw` | `laptop.tail1121fd.ts.net` | `100.102.186.128` | Confirmed 2026-07-18 |
| `gaming` | `tower.tail1121fd.ts.net` (reserved) | *(pending)* | **Tailscale not yet reinstalled** on the fresh Ubuntu — the alias is reserved for when it is. See the ambiguity note below. |
| `pc-cuda` | *(to confirm)* | *(to confirm)* | — |

**Open item — the `tower.tail1121fd.ts.net` alias.** This name was historically
attached to the old dormant `tower` node (now reinstalled as `gaming`) and is
also the host in `docs/telemetry-langfuse.md`'s `LANGFUSE_PUBLIC_URL`
(`tower.tail1121fd.ts.net:3000`). Whether Langfuse is served from the hub box or
from the gaming satellite — and therefore which machine should own this magic-DNS
name once Tailscale is reinstalled on `gaming` — is **not yet confirmed here**.
Resolve this (and update `models.yaml` `gaming.tailscale` + the Langfuse docs to
match) as part of the Tailscale reinstall, rather than guessing now. Kept
deliberately un-asserted so this record stays trustworthy.

## Machine specs snapshot

`config/machine_specs.yaml` is a separate, **auto-generated** hardware snapshot
of whichever box runs `scripts/detect_machine_specs.py`; it is keyed by that
box's **hostname** (currently `tower`, the hub box `pc-cuda`), not by fleet host
id — do not confuse its `name: tower` with the `gaming` satellite.
