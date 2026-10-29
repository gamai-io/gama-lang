# GAMA-OS 2.0 — appliance roadmap (honest scope)

Status: **design target**. The current `gama-os/` is a builder script and a
launcher, not a bootable OS image. 2.0 must be a **branded Linux appliance**,
not a claim of a new kernel.

## Product definition

A signed, immutable Linux image that turns a PC, mini-PC or VM into a GAMA node:

| SKU | Target | Minimum | Core |
|---|---|---|---|
| Kiosk | old PCs, reception, shop | 1 GB RAM, x64 | launcher + browser + AI client |
| Terminal | office workstation | 2 GB | launcher + office/web + AI |
| Pro | developer/ops | 4 GB | terminal + local runtime + SSH |
| Node | 24/7 service | 8 GB | headless runtime + fleet agent |

Base: Alpine or Debian stable; **not** a from-scratch kernel.

## 2.0 acceptance tests

1. Boots on a VM and one old x64 PC from USB.
2. First boot creates a user, joins Tailscale, registers in the fleet.
3. `gama-os health` reports disk, RAM, services, version.
4. Signed image verifies; unsigned update is refused.
5. Rollback to previous image after a failed update.
6. Kiosk mode locks to the launcher; admin exit requires a local credential.
7. Offline mode still opens local apps and shows a clear "AI unavailable" state.
8. Remote command is authenticated and audited; destructive commands are denied
   unless an explicit lease/approval exists.
9. Factory reset preserves logs/evidence for export.
10. Reproducible build hash published with every release.

## Milestones

- M1: build rootfs + boot in QEMU; no branding.
- M2: launcher, network, Tailscale, first-run.
- M3: signed updates + rollback.
- M4: kiosk/terminal/pro/node profiles.
- M5: one old PC + one VM demo; Q-LAB results.
- M6: installer USB and support runbook.

## Non-goals

- New kernel, custom init, custom package manager.
- Selling before M5.
- Claiming "AI OS" when the AI is a remote API client.
