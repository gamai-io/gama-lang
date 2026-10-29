# GAMA OS — PRODUCT LINE (v0.1 scaffold; 2.0 is a roadmap)

> Current status: builder + launcher only, not a bootable signed image. 2.0 acceptance tests: `gama-os/ROADMAP_2.0.md`.

Our own operating-system family on top of a Linux kernel — **the GAMA fleet as a platform**.
Every machine = a node in our mesh, with integrated AI (GAMA-L).

## Versions (customer choice)

| Version | Purpose | Min hardware | RAM focus | Price |
|---|---|---|---|---|
| **GAMA-OS KIOSK** (Lite) | "revive old machines" | Celeron/Pentium, 512MB-1GB | 256MB runtime | package |
| **GAMA-OS TERMINAL** | thin client / workstation | 1GB+, i686/x64 | 512MB | package |
| **GAMA-OS PRO** | = like our Linux3 | x64, 2-4GB | 1GB | package |
| **GAMA-OS NODE** | service node / backend | x64, 8GB+ | server | subscription |

## Every version = same core + a role
- **Kernel**: Alpine Linux (ultra-light, secure)
- **UI**: GAMA-OS launcher (web UI = GAMA-L `interface`) — works on mobile too
- **AI**: GAMA-L runtime (`ask`, `remember`, `sql`, `notify`) — local or over our network
- **Network**: automatic Tailscale presence + gama-bus (our protocol)
- **Alerts**: `notify` (SMTP + bus) — user notified everywhere
- **Security**: locked kiosk, remote-admin, self-update

## How we differ from everyone
1. **AI is in the language** (GAMA-L), not an add-on — every machine can ask/speak.
2. **Everything branded GAMA**: splash, launcher, UI components.
3. **Cloud bridge to our fleet**: an old machine is capacity (node) instead of e-waste.
4. **Tested on real junk** — our Q-LAB list.

## Sales / business model
- **Kiosk/Terminal**: licensed package (ISO + install time + branding) — one-time.
- **PRO/NODE**: subscription (support + AI + cloud bridge + aftercare).
- **"Revive junk" service**: turn old computers into GAMA terminals for firms/schools
  (e.g. a store, a school).
- **Labelling/licensing** for customer companies: their splash on our core.

## Q-LAB (we test on the real thing)
- [ ] 512MB test of old machines
- [ ] 1GB Dual Pentium / Celeron
- [ ] Linux3 machine (x64) — PRO baseline
- [ ] mobile view (UI on a phone)
